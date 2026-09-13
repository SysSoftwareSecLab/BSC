"""Metadata-blind GPT-5.5 replication with fail-closed tool auditing."""

from __future__ import annotations

import json
import os
import re
import shutil
import signal
import subprocess
import tempfile
import time
from collections import Counter
from pathlib import Path
from typing import Any, Callable, Mapping

from bisafecode.stage4_baseline_correction.context import (
    build_full_context,
    repository_root,
    sha256_file,
)
from bisafecode.stage4_baseline_correction.llm_full_context import (
    CODEX_CLI_VERSION,
    CODEX_COMMAND,
    JUDGMENT_TIMEOUT_SECONDS,
    JUDGMENTS_PER_PROGRAM,
    MODEL_SELECTOR,
    PROGRAM_TIMEOUT_SECONDS,
    QUOTA_RE,
    REASONING_EFFORT,
    VERDICTS,
    _argv,
    _contains_refusal,
    _parse_events,
    _parse_final,
    _usage,
    build_binding as build_base_binding,
    discover_cli_identity,
)

from . import CONTRACT_RELATIVE


ALLOWED_STREAM_ITEM_TYPES = {"agent_message", "reasoning", "error"}
PROGRAM_ID_RE = re.compile(r"EXP-S4-002-P\d{3}")
FORBIDDEN_CONTEXT_KEYS = {
    "program_id", "family_id", "expected_verdict", "intended_oracle_class",
    "ground_truth", "oracle_label",
}


def _contract(name: str) -> str:
    return f"{CONTRACT_RELATIVE}/{name}"


def build_binding(
    *, root: Path | None = None, cli_version: str = CODEX_CLI_VERSION
) -> Mapping[str, Any]:
    root = (root or repository_root()).resolve()
    return {
        "model_selector": MODEL_SELECTOR,
        "reasoning_effort": REASONING_EFFORT,
        "codex_cli_version": cli_version,
        "authentication": "chatgpt_subscription",
        "interface": "codex exec",
        "fresh_process_per_judgment": True,
        "resume_allowed": False,
        "original_program_id_in_prompt": False,
        "opaque_case_token_in_prompt": False,
        "prompt_scientific_inputs": ["program_source", "frozen_model_summary"],
        "user_config_loaded": False,
        "web_shell_mcp_subagents_enabled": False,
        "fail_closed_on_disallowed_stream_item": True,
        "program_wall_budget_seconds": int(PROGRAM_TIMEOUT_SECONDS),
        "per_judgment_wall_budget_seconds": int(JUDGMENT_TIMEOUT_SECONDS),
        "protocol_sha256": sha256_file(root / _contract("PROTOCOL.json")),
        "isolation_policy_sha256": sha256_file(root / _contract("OPAQUE_ID_AND_ISOLATION_POLICY.json")),
        "base_full_context_binding": dict(build_base_binding(root=root, cli_version=cli_version)),
        "reproducibility_wording": "model-family-pinned and protocol-reproducible",
    }


def validate_binding(binding: Mapping[str, Any], *, root: Path | None = None) -> list[str]:
    expected = build_binding(root=root)
    errors = [
        f"{key}_binding_mismatch"
        for key, value in expected.items()
        if binding.get(key) != value
    ]
    extra = sorted(set(binding) - set(expected))
    if extra:
        errors.append("unexpected_binding_fields:" + ",".join(extra))
    return errors


def audit_embedded_context(context: Mapping[str, Any]) -> Mapping[str, Any]:
    forbidden_keys: set[str] = set()
    program_identifiers: set[str] = set()

    def visit(value: Any) -> None:
        if isinstance(value, dict):
            for key, item in value.items():
                if str(key) in FORBIDDEN_CONTEXT_KEYS:
                    forbidden_keys.add(str(key))
                visit(item)
        elif isinstance(value, list):
            for item in value:
                visit(item)
        elif isinstance(value, str):
            program_identifiers.update(PROGRAM_ID_RE.findall(value))

    visit(context)
    return {
        "forbidden_context_keys": sorted(forbidden_keys),
        "controlled_program_identifiers": sorted(program_identifiers),
        "passed": not forbidden_keys and not program_identifiers,
    }


def build_judgment_requests(
    source: str,
    *,
    opaque_case_id: str,
    binding: Mapping[str, Any],
    root: Path | None = None,
) -> list[Mapping[str, Any]]:
    root = (root or repository_root()).resolve()
    errors = validate_binding(binding, root=root)
    if errors:
        raise ValueError("invalid metadata-blind GPT binding: " + ",".join(errors))
    prompt = (
        root
        / "03_experiments/contracts/EXP-S4-006_RQ1_BASELINE_CORRECTION/LLM_JUDGE_PROMPT.txt"
    ).read_text(encoding="utf-8")
    context = build_full_context(root)
    context_audit = audit_embedded_context(context)
    if not context_audit["passed"]:
        raise ValueError("embedded GPT context contains program-specific metadata")
    if PROGRAM_ID_RE.search(source):
        raise ValueError("program source contains an original controlled program identifier")
    return [
        {
            "opaque_case_id": opaque_case_id,
            "judgment_index": index,
            "system_prompt": prompt,
            "program_source": source,
            "frozen_model_summary": context,
            "allowed_verdicts": sorted(VERDICTS),
        }
        for index in range(JUDGMENTS_PER_PROGRAM)
    ]


def prompt_bytes(request: Mapping[str, Any]) -> bytes:
    """Serialize exactly two scientific inputs and no case identifier."""

    payload = {
        "program_source": request["program_source"],
        "frozen_model_summary": request["frozen_model_summary"],
        "allowed_verdicts": request["allowed_verdicts"],
    }
    return (
        str(request["system_prompt"]).rstrip()
        + "\n\nCURRENT_PROGRAM_INPUT_JSON\n"
        + json.dumps(payload, sort_keys=True, separators=(",", ":"), ensure_ascii=False)
        + "\n"
    ).encode("utf-8")


def _stream_boundary(events: list[Any]) -> Mapping[str, Any]:
    item_types: list[str] = []
    for event in events:
        if not isinstance(event, dict) or event.get("type") not in {"item.started", "item.completed"}:
            continue
        item = event.get("item")
        if isinstance(item, dict):
            item_types.append(str(item.get("type")))
    disallowed = sorted(set(item_types) - ALLOWED_STREAM_ITEM_TYPES)
    return {
        "observed_item_types": sorted(set(item_types)),
        "disallowed_item_types": disallowed,
        "tool_or_file_event_count": sum(item not in ALLOWED_STREAM_ITEM_TYPES for item in item_types),
        "passed": not disallowed,
    }


def _argv_boundary(argv: list[str]) -> Mapping[str, Any]:
    joined = "\0".join(argv)
    required = (
        'web_search="disabled"',
        "agents.enabled=false",
        "features.shell_tool=false",
        "mcp_servers={}",
        "--ignore-user-config",
        "--ignore-rules",
        "--sandbox\0read-only",
        "--ephemeral",
    )
    missing = [value for value in required if value not in joined]
    return {"required_controls": list(required), "missing_controls": missing, "passed": not missing}


def invoke_codex(
    request: Mapping[str, Any],
    *,
    binding: Mapping[str, Any],
    timeout_seconds: float,
    root: Path | None = None,
) -> Mapping[str, Any]:
    root = (root or repository_root()).resolve()
    errors = validate_binding(binding, root=root)
    if errors:
        raise ValueError("invalid metadata-blind GPT binding: " + ",".join(errors))
    started = time.monotonic()
    with tempfile.TemporaryDirectory(prefix="bisafecode-s4-007-gpt55-") as directory:
        workdir = Path(directory).resolve()
        schema_source = root / "03_experiments/contracts/EXP-S4-006_RQ1_BASELINE_CORRECTION/judge_output.schema.json"
        schema_path = workdir / "judge_output.schema.json"
        final_path = workdir / "final.json"
        schema_path.write_bytes(schema_source.read_bytes())
        argv = _argv(schema_path, final_path, workdir)
        environment = dict(os.environ)
        environment.pop("OPENAI_API_KEY", None)
        environment.pop("CODEX_API_KEY", None)
        try:
            process = subprocess.Popen(
                argv,
                cwd=workdir,
                env=environment,
                stdin=subprocess.PIPE,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                start_new_session=True,
            )
        except Exception as error:
            return {
                "opaque_case_id": request["opaque_case_id"],
                "judgment_index": request["judgment_index"],
                "status": "cli_or_service_error",
                "verdict": None,
                "reason_codes": ["CODEX_CLI_PROCESS_LAUNCH_FAILED"],
                "error": f"{type(error).__name__}:{error}",
                "dispatched": False,
            }
        timed_out = False
        try:
            stdout_bytes, stderr_bytes = process.communicate(
                input=prompt_bytes(request), timeout=timeout_seconds
            )
        except subprocess.TimeoutExpired:
            timed_out = True
            try:
                os.killpg(process.pid, signal.SIGKILL)
            except (ProcessLookupError, PermissionError):
                process.kill()
            stdout_bytes, stderr_bytes = process.communicate()
        final_raw = final_path.read_text(encoding="utf-8") if final_path.is_file() else ""
    raw_jsonl = stdout_bytes.decode("utf-8", "replace")
    stderr = stderr_bytes.decode("utf-8", "replace")
    events, malformed = _parse_events(raw_jsonl)
    parsed = _parse_final(final_raw) if final_raw else None
    stream_boundary = _stream_boundary(events)
    argv_boundary = _argv_boundary(argv)
    base = {
        "opaque_case_id": request["opaque_case_id"],
        "judgment_index": request["judgment_index"],
        "dispatched": True,
        "elapsed_seconds": time.monotonic() - started,
        "exit_code": process.returncode,
        "argv_without_prompt": argv,
        "raw_jsonl": raw_jsonl,
        "stderr": stderr,
        "final_response_raw": final_raw,
        "jsonl_malformed_line_numbers": malformed,
        "token_usage": _usage(events),
        "turn_started": any(
            isinstance(item, dict) and item.get("type") == "turn.started"
            for item in events
        ),
        "environment_policy": {
            "api_keys_removed": True,
            "chatgpt_auth_cache_reused": True,
            "original_program_id_available_to_child": False,
            "case_token_present_in_prompt": False,
        },
        "stream_boundary_audit": stream_boundary,
        "argv_boundary_audit": argv_boundary,
    }
    if not stream_boundary["passed"] or not argv_boundary["passed"]:
        return {
            **base,
            "status": "isolation_violation",
            "verdict": None,
            "reason_codes": ["CODEX_ISOLATION_BOUNDARY_VIOLATION"],
        }
    if timed_out:
        return {
            **base,
            "status": "timeout",
            "verdict": None,
            "reason_codes": ["CODEX_CLI_JUDGMENT_TIMEOUT"],
        }
    combined = "\n".join((raw_jsonl, stderr, final_raw))
    if process.returncode != 0:
        quota = bool(QUOTA_RE.search(combined)) and not base["turn_started"]
        return {
            **base,
            "status": "quota_window_interruption" if quota else "cli_or_service_error",
            "verdict": None,
            "reason_codes": [
                "CODEX_SUBSCRIPTION_QUOTA_WINDOW_UNAVAILABLE"
                if quota else "CODEX_CLI_OR_SERVICE_NONZERO_EXIT"
            ],
        }
    if malformed:
        return {
            **base,
            "status": "cli_or_service_error",
            "verdict": None,
            "reason_codes": ["CODEX_JSONL_STREAM_MALFORMED"],
        }
    if _contains_refusal(events):
        return {
            **base,
            "status": "refusal",
            "verdict": None,
            "reason_codes": ["CODEX_MODEL_REFUSAL"],
        }
    if parsed is None:
        return {
            **base,
            "status": "malformed_response",
            "verdict": None,
            "reason_codes": ["CODEX_FINAL_RESPONSE_SCHEMA_MISMATCH"],
        }
    return {
        **base,
        "status": "completed",
        "verdict": parsed["verdict"],
        "reason_codes": parsed["reason_codes"],
        "final_response": parsed,
    }


def run_gpt_opaque_judge(
    source: str,
    *,
    opaque_case_id: str,
    binding: Mapping[str, Any],
    total_timeout_seconds: float = PROGRAM_TIMEOUT_SECONDS,
    invoke: Callable[[Mapping[str, Any], float], Mapping[str, Any]] | None = None,
) -> Mapping[str, Any]:
    requests = build_judgment_requests(
        source, opaque_case_id=opaque_case_id, binding=binding
    )
    dispatch = invoke or (
        lambda request, timeout: invoke_codex(
            request, binding=binding, timeout_seconds=timeout
        )
    )
    deadline = time.monotonic() + total_timeout_seconds
    judgments: list[Mapping[str, Any]] = []
    for request in requests:
        remaining = deadline - time.monotonic()
        if remaining <= 0:
            judgments.append({
                "opaque_case_id": opaque_case_id,
                "judgment_index": request["judgment_index"],
                "status": "timeout",
                "verdict": None,
                "reason_codes": ["LLM_SHARED_WALL_BUDGET_EXHAUSTED"],
                "dispatched": False,
            })
            continue
        try:
            outcome = dict(dispatch(request, min(JUDGMENT_TIMEOUT_SECONDS, remaining)))
        except Exception as error:
            outcome = {
                "status": "cli_or_service_error",
                "verdict": None,
                "reason_codes": [f"CODEX_CLI_{type(error).__name__.upper()}"],
                "dispatched": False,
            }
        outcome.setdefault("opaque_case_id", opaque_case_id)
        outcome.setdefault("judgment_index", request["judgment_index"])
        judgments.append(outcome)
        if outcome.get("status") in {"quota_window_interruption", "isolation_violation"}:
            break
    completed = [
        str(item["verdict"])
        for item in judgments
        if item.get("status") == "completed" and item.get("verdict") in VERDICTS
    ]
    verdict_counts = Counter(completed)
    status_counts = Counter(str(item.get("status")) for item in judgments)
    if completed:
        ordered = verdict_counts.most_common()
        tied = len(ordered) > 1 and ordered[0][1] == ordered[1][1]
        predicted = "unknown" if tied else ordered[0][0]
        reasons = ["LLM_JUDGMENT_TIE"] if tied else []
    else:
        predicted, reasons = "unknown", ["NO_VALID_LLM_JUDGMENT"]
    if status_counts["isolation_violation"]:
        execution_status = "isolation_violation"
        predicted = "unknown"
    elif status_counts["quota_window_interruption"]:
        execution_status = "quota_window_interruption"
    elif not completed and status_counts["timeout"] == len(judgments):
        execution_status = "timeout"
    elif not completed and status_counts["cli_or_service_error"]:
        execution_status = "cli_or_service_error"
    else:
        execution_status = "ok"
    return {
        "status": execution_status,
        "predicted_verdict": predicted,
        "reason_codes": reasons,
        "opaque_case_id": opaque_case_id,
        "original_program_id_sent_to_model": False,
        "opaque_case_id_sent_to_model": False,
        "judgments": judgments,
        "aggregation": {
            "scheduled_count": JUDGMENTS_PER_PROGRAM,
            "retained_count": len(judgments),
            "valid_completed_count": len(completed),
            "verdict_counts": dict(sorted(verdict_counts.items())),
            "judgment_status_counts": dict(sorted(status_counts.items())),
            "policy": "plurality-with-tie-to-unknown/full-context-metadata-blind-v1",
            "program_wall_budget_seconds": total_timeout_seconds,
            "per_judgment_wall_budget_seconds": JUDGMENT_TIMEOUT_SECONDS,
            "timeout_is_not_judgment_error": True,
            "retry_count": 0,
        },
    }
