"""Version-pinned GPT-5.5 baseline adapter with raw-response retention.

FREEZE_ONLY_NOT_FORMAL_STAGE4_EVIDENCE

Importing this module performs no provider call.  Formal calls are made only
through the approval-gated pipeline or the explicitly synthetic preflight.
"""

from __future__ import annotations

import json
import os
import urllib.error
import urllib.request
import urllib.parse
import time
from collections import Counter
from pathlib import Path
from typing import Any, Callable, Mapping

from .identity import CONTRACT_DIR, repository_root, sha256_file


PROVIDER_ADAPTER_IDS = {
    "openai_responses_json_v1",
}

MODEL_ID = "gpt-5.5-2026-04-23"
PROVIDER_API_VERSION = "v1"
ENDPOINT = "https://api.openai.com/v1/responses"
API_KEY_ENV = "OPENAI_API_KEY"
REASONING_EFFORT = "high"
INVOCATION_ORIGIN = "external_openai_responses_api"


def validate_exact_llm_binding(binding: Mapping[str, Any], *, root: Path | None = None) -> list[str]:
    root = (root or repository_root()).resolve()
    errors = []
    for key in (
        "model_id",
        "provider_api_version",
        "provider_adapter_id",
        "endpoint",
        "api_key_env",
        "invocation_origin",
    ):
        value = binding.get(key)
        if not isinstance(value, str) or not value or "unavailable" in value:
            errors.append(f"{key}_missing")
    if isinstance(binding.get("endpoint"), str) and not binding["endpoint"].startswith("https://"):
        errors.append("endpoint_must_be_https")
    if isinstance(binding.get("endpoint"), str):
        endpoint = urllib.parse.urlparse(binding["endpoint"])
        if endpoint.path.rstrip("/") != "/v1/responses" or endpoint.query or endpoint.fragment:
            errors.append("openai_responses_endpoint_shape_mismatch")
    exact_values = {
        "model_id": MODEL_ID,
        "provider_api_version": PROVIDER_API_VERSION,
        "endpoint": ENDPOINT,
        "api_key_env": API_KEY_ENV,
        "reasoning_effort": REASONING_EFFORT,
        "invocation_origin": INVOCATION_ORIGIN,
    }
    for key, expected_value in exact_values.items():
        if binding.get(key) != expected_value:
            errors.append(f"{key}_binding_mismatch")
    if binding.get("provider_adapter_id") not in PROVIDER_ADAPTER_IDS:
        errors.append("provider_adapter_id_unsupported")
    if binding.get("invocation_origin") == "current_codex_conversation":
        errors.append("current_codex_conversation_forbidden")
    expected = {
        "prompt_sha256": f"{CONTRACT_DIR}/llm/LLM_JUDGE_PROMPT.txt",
        "api_request_schema_sha256": f"{CONTRACT_DIR}/llm/request.schema.json",
        "api_response_schema_sha256": f"{CONTRACT_DIR}/llm/response.schema.json",
        "aggregation_policy_sha256": f"{CONTRACT_DIR}/llm/AGGREGATION_POLICY.json",
    }
    for key, path in expected.items():
        if binding.get(key) != sha256_file(root / path):
            errors.append(f"{key}_mismatch")
    if binding.get("raw_response_retention") is not True:
        errors.append("raw_response_retention_required")
    return errors


def build_llm_requests(
    source: str, *, program_id: str, binding: Mapping[str, Any], root: Path | None = None
) -> list[Mapping[str, Any]]:
    root = (root or repository_root()).resolve()
    errors = validate_exact_llm_binding(binding, root=root)
    if errors:
        raise ValueError("invalid LLM binding: " + ",".join(errors))
    prompt = (root / CONTRACT_DIR / "llm" / "LLM_JUDGE_PROMPT.txt").read_text(encoding="utf-8")
    return [
        {
            "model": binding["model_id"],
            "reasoning_effort": binding["reasoning_effort"],
            "judgment_index": index,
            "input": {
                "system_prompt": prompt,
                "program_id": program_id,
                "program_source": source,
                "allowed_verdicts": ["verified-within-bounds", "violated", "unknown", "invalid"],
            },
        }
        for index in range(5)
    ]


def parse_llm_response(response: Mapping[str, Any]) -> Mapping[str, Any]:
    if not isinstance(response, dict):
        return {"status": "format_error", "verdict": None, "reason_codes": ["LLM_RESPONSE_NOT_OBJECT"]}
    if isinstance(response.get("provider_error"), dict):
        reasons = response.get("reason_codes")
        return {
            "status": "provider_failure",
            "verdict": None,
            "reason_codes": (
                list(reasons)
                if isinstance(reasons, list)
                and reasons
                and all(isinstance(value, str) for value in reasons)
                else ["OPENAI_PROVIDER_FAILURE"]
            ),
        }
    if response.get("refusal"):
        return {"status": "refusal", "verdict": None, "reason_codes": ["LLM_REFUSAL"]}
    retained_reasons = response.get("reason_codes")
    if response.get("verdict") is None and isinstance(retained_reasons, list) and all(
        isinstance(value, str) for value in retained_reasons
    ):
        return {
            "status": "format_error",
            "verdict": None,
            "reason_codes": retained_reasons or ["LLM_RESPONSE_SCHEMA_ERROR"],
        }
    verdict = response.get("verdict")
    reasons = response.get("reason_codes")
    if verdict not in {"verified-within-bounds", "violated", "unknown", "invalid"} or not isinstance(reasons, list) or any(not isinstance(value, str) for value in reasons):
        return {"status": "format_error", "verdict": None, "reason_codes": ["LLM_RESPONSE_SCHEMA_ERROR"]}
    return {"status": "ok", "verdict": verdict, "reason_codes": reasons}


def run_llm_judge(
    source: str,
    *,
    program_id: str,
    binding: Mapping[str, Any],
    invoke: Callable[[Mapping[str, Any], float], Mapping[str, Any]],
    total_timeout_seconds: float = 120.0,
) -> Mapping[str, Any]:
    if total_timeout_seconds < 0:
        raise ValueError("LLM total timeout must be nonnegative")
    requests = build_llm_requests(source, program_id=program_id, binding=binding)
    judgments = []
    deadline = time.monotonic() + total_timeout_seconds
    for index, request in enumerate(requests):
        remaining = deadline - time.monotonic()
        if remaining <= 0:
            judgments.extend(
                {
                    "request": pending,
                    "raw_response": None,
                    "parsed": {
                        "status": "timeout",
                        "verdict": None,
                        "reason_codes": ["LLM_SHARED_WALL_BUDGET_EXHAUSTED"],
                    },
                }
                for pending in requests[index:]
            )
            break
        try:
            raw = invoke(request, remaining)
            parsed = parse_llm_response(raw)
            judgments.append({"request": request, "raw_response": raw, "parsed": parsed})
        except Exception as error:
            status = "timeout" if isinstance(error, TimeoutError) else "provider_failure"
            code = "LLM_SHARED_WALL_BUDGET_EXHAUSTED" if isinstance(error, TimeoutError) else f"LLM_CALL_{type(error).__name__.upper()}"
            judgments.append(
                {
                    "request": request,
                    "raw_response": None,
                    "parsed": {"status": status, "verdict": None, "reason_codes": [code]},
                }
            )
    valid = [item["parsed"]["verdict"] for item in judgments if item["parsed"]["status"] == "ok"]
    counts = Counter(valid)
    status_counts = Counter(item["parsed"]["status"] for item in judgments)
    if not valid:
        retained_failure_reasons = sorted(
            {
                reason
                for item in judgments
                for reason in item["parsed"].get("reason_codes", ())
                if isinstance(reason, str)
            }
        )
        verdict, reasons = "unknown", [
            "NO_VALID_LLM_JUDGMENT",
            *retained_failure_reasons,
        ]
    else:
        ordered = counts.most_common()
        if len(ordered) > 1 and ordered[0][1] == ordered[1][1]:
            verdict, reasons = "unknown", ["LLM_JUDGMENT_TIE"]
        else:
            verdict, reasons = ordered[0][0], []
    return {
        "verdict": verdict,
        "reason_codes": reasons,
        "states": 5,
        "transitions": 5,
        "model_time_ns": 0,
        "judgments": judgments,
        "execution_status": (
            "timeout"
            if not valid and status_counts["timeout"] == len(judgments)
            else (
                "provider_failure"
                if not valid and status_counts["provider_failure"]
                else "ok"
            )
        ),
        "aggregation": {
            "valid_count": len(valid),
            "verdict_counts": dict(sorted(counts.items())),
            "judgment_status_counts": dict(sorted(status_counts.items())),
            "policy": "plurality-with-tie-to-unknown/v2-timeout-separated",
            "attempted_or_retained_count": len(judgments),
            "shared_wall_budget_seconds": total_timeout_seconds,
            "retry_count": 0,
        },
    }


def _provider_request(
    request_record: Mapping[str, Any], binding: Mapping[str, Any]
) -> tuple[Mapping[str, Any], Mapping[str, str]]:
    adapter = binding["provider_adapter_id"]
    if adapter == "openai_responses_json_v1":
        verdict_schema = {
            "type": "object",
            "additionalProperties": False,
            "properties": {
                "verdict": {
                    "type": "string",
                    "enum": [
                        "verified-within-bounds",
                        "violated",
                        "unknown",
                        "invalid",
                    ],
                },
                "reason_codes": {
                    "type": "array",
                    "items": {"type": "string"},
                },
            },
            "required": ["verdict", "reason_codes"],
        }
        body = {
            "model": binding["model_id"],
            "reasoning": {"effort": request_record["reasoning_effort"]},
            "max_output_tokens": 4096,
            "input": [
                {
                    "role": "system",
                    "content": [
                        {
                            "type": "input_text",
                            "text": request_record["input"]["system_prompt"],
                        }
                    ],
                },
                {
                    "role": "user",
                    "content": [
                        {
                            "type": "input_text",
                            "text": json.dumps(request_record["input"], sort_keys=True),
                        }
                    ],
                },
            ],
            "text": {
                "format": {
                    "type": "json_schema",
                    "name": "bisafecode_stage4_verdict",
                    "strict": True,
                    "schema": verdict_schema,
                }
            },
        }
        headers: Mapping[str, str] = {}
    else:
        raise ValueError("unsupported provider adapter")
    return body, headers


def _normalized_provider_response(
    raw: Mapping[str, Any], *, adapter_id: str
) -> Mapping[str, Any]:
    if adapter_id == "openai_responses_json_v1":
        output = raw.get("output")
        if not isinstance(output, list):
            return {
                "provider_raw": raw,
                "verdict": None,
                "reason_codes": ["OPENAI_RESPONSES_OUTPUT_MISSING"],
            }
        texts = []
        refusals = []
        for item in output:
            if not isinstance(item, dict) or item.get("type") != "message":
                continue
            content = item.get("content")
            if not isinstance(content, list):
                continue
            for part in content:
                if not isinstance(part, dict):
                    continue
                if part.get("type") == "output_text" and isinstance(part.get("text"), str):
                    texts.append(part["text"])
                elif part.get("type") == "refusal" and isinstance(part.get("refusal"), str):
                    refusals.append(part["refusal"])
        if refusals:
            return {
                "provider_raw": raw,
                "refusal": "\n".join(refusals),
                "verdict": None,
                "reason_codes": ["LLM_REFUSAL"],
            }
        text = "\n".join(texts) if texts else None
    else:
        raise ValueError("unsupported provider adapter")
    if not isinstance(text, str):
        return {
            "provider_raw": raw,
            "verdict": None,
            "reason_codes": ["OPENAI_RESPONSES_OUTPUT_TEXT_MISSING"],
        }
    try:
        parsed = json.loads(text)
    except json.JSONDecodeError:
        return {"provider_raw": raw, "verdict": None, "reason_codes": ["PROVIDER_RESPONSE_JSON_INVALID"]}
    if not isinstance(parsed, dict):
        return {"provider_raw": raw, "verdict": None, "reason_codes": ["PROVIDER_RESPONSE_JSON_NOT_OBJECT"]}
    return {**parsed, "provider_raw": raw}


def provider_http_invoke(
    request_record: Mapping[str, Any],
    *,
    binding: Mapping[str, Any],
    timeout_seconds: float,
) -> Mapping[str, Any]:
    """Dispatch a Mac-bound provider adapter within the shared deadline."""

    errors = validate_exact_llm_binding(binding)
    if errors:
        raise ValueError("invalid LLM binding: " + ",".join(errors))
    key = os.environ.get(binding["api_key_env"])
    if not key:
        raise RuntimeError("bound LLM API credential environment variable is absent")
    if timeout_seconds <= 0:
        raise TimeoutError("shared LLM wall budget exhausted")
    body, adapter_headers = _provider_request(request_record, binding)
    request = urllib.request.Request(
        binding["endpoint"],
        data=json.dumps(body, sort_keys=True).encode("utf-8"),
        headers={"Authorization": f"Bearer {key}", "Content-Type": "application/json", **adapter_headers},
        method="POST",
    )
    try:
        with urllib.request.urlopen(request, timeout=timeout_seconds) as response:
            raw = json.loads(response.read().decode("utf-8"))
    except urllib.error.HTTPError as error:
        body_text = error.read().decode("utf-8", "replace")
        try:
            raw_error: Any = json.loads(body_text)
        except json.JSONDecodeError:
            raw_error = {"unparsed_body": body_text}
        provider_code = None
        if isinstance(raw_error, dict) and isinstance(raw_error.get("error"), dict):
            provider_code = raw_error["error"].get("code") or raw_error["error"].get("type")
        safe_code = "".join(
            character.upper() if character.isalnum() else "_"
            for character in str(provider_code or "UNSPECIFIED")
        ).strip("_")
        request_id = error.headers.get("x-request-id") if error.headers else None
        return {
            "provider_error": {
                "http_status": int(error.code),
                "response_body": raw_error,
                "x_request_id": request_id,
            },
            "verdict": None,
            "reason_codes": [f"OPENAI_HTTP_{int(error.code)}_{safe_code}"],
        }
    return _normalized_provider_response(raw, adapter_id=binding["provider_adapter_id"])
