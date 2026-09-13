"""Full-context ChatGPT-authenticated GPT-5.5 judge for EXP-S4-006."""

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

from . import CONTRACT_RELATIVE
from .context import build_full_context, repository_root, sha256_file


MODEL_SELECTOR = "gpt-5.5"
REASONING_EFFORT = "high"
CODEX_COMMAND = "codex"
CODEX_CLI_VERSION = "codex-cli 0.148.0-alpha.9"
JUDGMENTS_PER_PROGRAM = 5
PROGRAM_TIMEOUT_SECONDS = 300.0
JUDGMENT_TIMEOUT_SECONDS = 60.0
VERDICTS = {"verified-within-bounds", "violated", "unknown", "invalid"}
QUOTA_RE = re.compile(
    r"(?:usage limit|rate limit|quota|rolling window|try again (?:at|later)|resets? (?:at|in))",
    re.IGNORECASE,
)


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
        "user_config_loaded": False,
        "web_shell_mcp_subagents_enabled": False,
        "program_wall_budget_seconds": int(PROGRAM_TIMEOUT_SECONDS),
        "per_judgment_wall_budget_seconds": int(JUDGMENT_TIMEOUT_SECONDS),
        "prompt_sha256": sha256_file(root / _contract("LLM_JUDGE_PROMPT.txt")),
        "context_manifest_sha256": sha256_file(root / _contract("CONTEXT_MANIFEST.json")),
        "source_api_spec_sha256": sha256_file(root / _contract("SOURCE_API_AND_SAFETY_SPEC.json")),
        "aggregation_policy_sha256": sha256_file(root / _contract("AGGREGATION_POLICY.json")),
        "judge_output_schema_sha256": sha256_file(root / _contract("judge_output.schema.json")),
        "reproducibility_wording": "model-family-pinned and protocol-reproducible",
    }


def validate_binding(binding: Mapping[str, Any], *, root: Path | None = None) -> list[str]:
    expected = build_binding(root=root)
    errors = [f"{key}_binding_mismatch" for key, value in expected.items() if binding.get(key) != value]
    extra = sorted(set(binding) - set(expected))
    if extra:
        errors.append("unexpected_binding_fields:" + ",".join(extra))
    return errors


def discover_cli_identity(timeout_seconds: float = 10.0) -> Mapping[str, Any]:
    environment = dict(os.environ)
    environment.pop("OPENAI_API_KEY", None)
    environment.pop("CODEX_API_KEY", None)
    version = subprocess.run(
        [CODEX_COMMAND, "--version"], capture_output=True, text=True,
        check=False, timeout=timeout_seconds, env=environment,
    )
    login = subprocess.run(
        [CODEX_COMMAND, "login", "status"], capture_output=True, text=True,
        check=False, timeout=timeout_seconds, env=environment,
    )
    lines = [line.strip() for line in (login.stdout + login.stderr).splitlines() if line.strip()]
    return {
        "command_path": shutil.which(CODEX_COMMAND),
        "codex_cli_version": version.stdout.strip(),
        "version_exit_code": version.returncode,
        "login_status": next((line for line in lines if line.startswith("Logged in using ")), ""),
        "login_exit_code": login.returncode,
        "api_key_removed_from_formal_child": True,
    }


def build_judgment_requests(
    source: str,
    *,
    program_id: str,
    binding: Mapping[str, Any],
    root: Path | None = None,
) -> list[Mapping[str, Any]]:
    root = (root or repository_root()).resolve()
    errors = validate_binding(binding, root=root)
    if errors:
        raise ValueError("invalid full-context GPT binding: " + ",".join(errors))
    prompt = (root / _contract("LLM_JUDGE_PROMPT.txt")).read_text(encoding="utf-8")
    context = build_full_context(root)
    return [
        {
            "program_id": program_id,
            "judgment_index": index,
            "system_prompt": prompt,
            "program_source": source,
            "frozen_model_summary": context,
            "allowed_verdicts": sorted(VERDICTS),
        }
        for index in range(JUDGMENTS_PER_PROGRAM)
    ]


def prompt_bytes(request: Mapping[str, Any]) -> bytes:
    payload = {
        "program_id": request["program_id"],
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


def _argv(schema_path: Path, final_path: Path, workdir: Path) -> list[str]:
    return [
        CODEX_COMMAND, "exec", "--model", MODEL_SELECTOR,
        "-c", "model_reasoning_effort=high",
        "-c", 'web_search="disabled"',
        "-c", "agents.enabled=false",
        "-c", "features.shell_tool=false",
        "-c", "mcp_servers={}",
        "--ignore-user-config", "--ignore-rules", "--sandbox", "read-only",
        "--ephemeral", "--skip-git-repo-check", "--cd", str(workdir), "--json",
        "--output-schema", str(schema_path), "--output-last-message", str(final_path), "-",
    ]


def _parse_events(raw_jsonl: str) -> tuple[list[Any], list[int]]:
    events: list[Any] = []
    malformed: list[int] = []
    for line_number, line in enumerate(raw_jsonl.splitlines(), start=1):
        if not line.strip():
            continue
        try:
            events.append(json.loads(line))
        except json.JSONDecodeError:
            malformed.append(line_number)
    return events, malformed


def _contains_refusal(value: Any) -> bool:
    if isinstance(value, dict):
        return (
            value.get("type") == "refusal"
            or isinstance(value.get("refusal"), str)
            or any(_contains_refusal(item) for item in value.values())
        )
    if isinstance(value, list):
        return any(_contains_refusal(item) for item in value)
    return False


def _usage(events: list[Any]) -> list[Mapping[str, Any]]:
    found: list[Mapping[str, Any]] = []
    def visit(value: Any) -> None:
        if isinstance(value, dict):
            if isinstance(value.get("usage"), dict):
                found.append(dict(value["usage"]))
            for item in value.values():
                visit(item)
        elif isinstance(value, list):
            for item in value:
                visit(item)
    visit(events)
    return found


def _parse_final(raw: str) -> Mapping[str, Any] | None:
    try:
        value = json.loads(raw)
    except json.JSONDecodeError:
        return None
    if not isinstance(value, dict) or set(value) != {"verdict", "reason_codes", "source_locations"}:
        return None
    if value.get("verdict") not in VERDICTS:
        return None
    if not isinstance(value.get("reason_codes"), list) or any(not isinstance(item, str) for item in value["reason_codes"]):
        return None
    if not isinstance(value.get("source_locations"), list):
        return None
    for location in value["source_locations"]:
        if not isinstance(location, dict) or set(location) != {"line_start", "line_end"}:
            return None
        start, end = location["line_start"], location["line_end"]
        if type(start) is not int or type(end) is not int or start < 1 or end < start:
            return None
    return value


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
        raise ValueError("invalid full-context GPT binding: " + ",".join(errors))
    started = time.monotonic()
    with tempfile.TemporaryDirectory(prefix="bisafecode-s4-006-gpt55-") as directory:
        workdir = Path(directory).resolve()
        schema_path = workdir / "judge_output.schema.json"
        final_path = workdir / "final.json"
        schema_path.write_bytes((root / _contract("judge_output.schema.json")).read_bytes())
        argv = _argv(schema_path, final_path, workdir)
        environment = dict(os.environ)
        environment.pop("OPENAI_API_KEY", None)
        environment.pop("CODEX_API_KEY", None)
        try:
            process = subprocess.Popen(
                argv, cwd=workdir, env=environment, stdin=subprocess.PIPE,
                stdout=subprocess.PIPE, stderr=subprocess.PIPE, start_new_session=True,
            )
        except Exception as error:
            return {
                "status": "cli_or_service_error", "verdict": None,
                "reason_codes": ["CODEX_CLI_PROCESS_LAUNCH_FAILED"],
                "error": f"{type(error).__name__}:{error}", "dispatched": False,
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
    base = {
        "program_id": request["program_id"],
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
        "turn_started": any(isinstance(item, dict) and item.get("type") == "turn.started" for item in events),
        "environment_policy": {"api_keys_removed": True, "chatgpt_auth_cache_reused": True},
    }
    if timed_out:
        return {**base, "status": "timeout", "verdict": None, "reason_codes": ["CODEX_CLI_JUDGMENT_TIMEOUT"]}
    combined = "\n".join((raw_jsonl, stderr, final_raw))
    if process.returncode != 0:
        quota = bool(QUOTA_RE.search(combined)) and not base["turn_started"]
        return {
            **base,
            "status": "quota_window_interruption" if quota else "cli_or_service_error",
            "verdict": None,
            "reason_codes": ["CODEX_SUBSCRIPTION_QUOTA_WINDOW_UNAVAILABLE" if quota else "CODEX_CLI_OR_SERVICE_NONZERO_EXIT"],
        }
    if malformed:
        return {**base, "status": "cli_or_service_error", "verdict": None, "reason_codes": ["CODEX_JSONL_STREAM_MALFORMED"]}
    if _contains_refusal(events):
        return {**base, "status": "refusal", "verdict": None, "reason_codes": ["CODEX_MODEL_REFUSAL"]}
    if parsed is None:
        return {**base, "status": "malformed_response", "verdict": None, "reason_codes": ["CODEX_FINAL_RESPONSE_SCHEMA_MISMATCH"]}
    return {**base, "status": "completed", "verdict": parsed["verdict"], "reason_codes": parsed["reason_codes"], "final_response": parsed}


def run_full_context_judge(
    source: str,
    *,
    program_id: str,
    binding: Mapping[str, Any],
    total_timeout_seconds: float = PROGRAM_TIMEOUT_SECONDS,
    invoke: Callable[[Mapping[str, Any], float], Mapping[str, Any]] | None = None,
) -> Mapping[str, Any]:
    requests = build_judgment_requests(source, program_id=program_id, binding=binding)
    dispatch = invoke or (lambda request, timeout: invoke_codex(request, binding=binding, timeout_seconds=timeout))
    deadline = time.monotonic() + total_timeout_seconds
    judgments: list[Mapping[str, Any]] = []
    for request in requests:
        remaining = deadline - time.monotonic()
        if remaining <= 0:
            judgments.append({
                "program_id": program_id,
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
                "status": "cli_or_service_error", "verdict": None,
                "reason_codes": [f"CODEX_CLI_{type(error).__name__.upper()}"],
                "dispatched": False,
            }
        outcome.setdefault("program_id", program_id)
        outcome.setdefault("judgment_index", request["judgment_index"])
        judgments.append(outcome)
        if outcome.get("status") == "quota_window_interruption":
            break
    completed = [str(item["verdict"]) for item in judgments if item.get("status") == "completed" and item.get("verdict") in VERDICTS]
    verdict_counts = Counter(completed)
    status_counts = Counter(str(item.get("status")) for item in judgments)
    if completed:
        ordered = verdict_counts.most_common()
        predicted = "unknown" if len(ordered) > 1 and ordered[0][1] == ordered[1][1] else ordered[0][0]
        reasons = ["LLM_JUDGMENT_TIE"] if predicted == "unknown" and len(ordered) > 1 and ordered[0][1] == ordered[1][1] else []
    else:
        predicted = "unknown"
        reasons = ["NO_VALID_LLM_JUDGMENT"]
    if status_counts["quota_window_interruption"]:
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
        "judgments": judgments,
        "aggregation": {
            "scheduled_count": JUDGMENTS_PER_PROGRAM,
            "retained_count": len(judgments),
            "valid_completed_count": len(completed),
            "verdict_counts": dict(sorted(verdict_counts.items())),
            "judgment_status_counts": dict(sorted(status_counts.items())),
            "policy": "plurality-with-tie-to-unknown/full-context-v1",
            "program_wall_budget_seconds": total_timeout_seconds,
            "per_judgment_wall_budget_seconds": JUDGMENT_TIMEOUT_SECONDS,
            "timeout_is_not_judgment_error": True,
            "retry_count": 0,
        },
    }

