"""Isolated ChatGPT-authenticated Codex CLI LLM-as-judge adapter.

FREEZE_ONLY_NOT_FORMAL_STAGE4_EVIDENCE

Each dispatched judgment starts a new ephemeral ``codex exec`` process in an
empty temporary directory.  The adapter never reads an API key and never
resumes a previous Codex session.
"""

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
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable, Mapping

from .identity import CONTRACT_DIR, repository_root, sha256_file


ADAPTER_ID = "codex_cli_jsonl_v1"
MODEL_SELECTOR = "gpt-5.5"
REASONING_EFFORT = "high"
CODEX_COMMAND = "codex"
CODEX_CLI_VERSION = "codex-cli 0.148.0-alpha.9"
AUTHENTICATION = "chatgpt_subscription"
LOGIN_STATUS = "Logged in using ChatGPT"
INVOCATION_ORIGIN = "chatgpt_authenticated_codex_cli"
PAPER_LABEL = "Codex-CLI-mediated GPT-5.5 LLM-as-judge baseline"
REPRODUCIBILITY_WORDING = "model-family-pinned and protocol-reproducible"
JUDGMENTS_PER_PROGRAM = 5

_VERDICTS = {
    "verified-within-bounds",
    "violated",
    "unknown",
    "invalid",
}
_QUOTA_RE = re.compile(
    r"(?:usage limit|rate limit|quota|rolling window|try again (?:at|later)|resets? (?:at|in))",
    re.IGNORECASE,
)


def _utc_now() -> str:
    return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")


def _contract_path(name: str) -> str:
    return f"{CONTRACT_DIR}/llm/{name}"


def build_codex_cli_binding(
    *, cli_version: str = CODEX_CLI_VERSION, root: Path | None = None
) -> Mapping[str, Any]:
    """Build the exact Mac-reviewable CLI binding without invoking the CLI."""

    root = (root or repository_root()).resolve()
    return {
        "model_selector": MODEL_SELECTOR,
        "model_id": MODEL_SELECTOR,
        "reasoning_effort": REASONING_EFFORT,
        "provider_adapter_id": ADAPTER_ID,
        "interface": "codex exec",
        "codex_command": CODEX_COMMAND,
        "codex_cli_version": cli_version,
        "authentication": AUTHENTICATION,
        "login_status": LOGIN_STATUS,
        "invocation_origin": INVOCATION_ORIGIN,
        "paper_label": PAPER_LABEL,
        "reproducibility_wording": REPRODUCIBILITY_WORDING,
        "api_key_env_removed": "OPENAI_API_KEY",
        "current_codex_conversation_allowed": False,
        "fresh_process_per_judgment": True,
        "resume_allowed": False,
        "user_config_loaded": False,
        "web_search_enabled": False,
        "shell_tool_enabled": False,
        "subagents_enabled": False,
        "raw_jsonl_retention": True,
        "final_response_retention": True,
        "prompt_sha256": sha256_file(root / _contract_path("LLM_JUDGE_PROMPT.txt")),
        "cli_invocation_schema_sha256": sha256_file(
            root / _contract_path("cli_invocation.schema.json")
        ),
        "judge_output_schema_sha256": sha256_file(
            root / _contract_path("judge_output.schema.json")
        ),
        "aggregation_policy_sha256": sha256_file(
            root / _contract_path("AGGREGATION_POLICY.json")
        ),
    }


def validate_codex_cli_binding(
    binding: Mapping[str, Any], *, root: Path | None = None
) -> list[str]:
    root = (root or repository_root()).resolve()
    expected = dict(build_codex_cli_binding(cli_version=CODEX_CLI_VERSION, root=root))
    errors: list[str] = []
    for key, value in expected.items():
        if binding.get(key) != value:
            errors.append(f"{key}_binding_mismatch")
    extra = set(binding) - set(expected)
    if extra:
        errors.append("unexpected_binding_fields:" + ",".join(sorted(extra)))
    return errors


def discover_codex_cli_identity(
    *, command: str = CODEX_COMMAND, timeout_seconds: float = 10.0
) -> Mapping[str, Any]:
    """Read CLI version and login method without exposing credentials."""

    environment = dict(os.environ)
    environment.pop("OPENAI_API_KEY", None)
    version = subprocess.run(
        [command, "--version"],
        capture_output=True,
        check=False,
        env=environment,
        text=True,
        timeout=timeout_seconds,
    )
    login = subprocess.run(
        [command, "login", "status"],
        capture_output=True,
        check=False,
        env=environment,
        text=True,
        timeout=timeout_seconds,
    )
    login_lines = [
        line.strip()
        for line in (login.stdout + login.stderr).splitlines()
        if line.strip()
    ]
    observed_login = next(
        (line for line in login_lines if line.startswith("Logged in using ")),
        "",
    )
    return {
        "command_path": shutil.which(command),
        "version_exit_code": version.returncode,
        "codex_cli_version": version.stdout.strip(),
        "login_exit_code": login.returncode,
        "login_status": observed_login,
        "openai_api_key_present_in_child_environment": False,
    }


def build_judgment_requests(
    source: str,
    *,
    program_id: str,
    binding: Mapping[str, Any],
    judgments: int = JUDGMENTS_PER_PROGRAM,
    root: Path | None = None,
) -> list[Mapping[str, Any]]:
    root = (root or repository_root()).resolve()
    errors = validate_codex_cli_binding(binding, root=root)
    if errors:
        raise ValueError("invalid Codex CLI binding: " + ",".join(errors))
    if judgments < 1:
        raise ValueError("at least one judgment is required")
    prompt = (root / _contract_path("LLM_JUDGE_PROMPT.txt")).read_text(
        encoding="utf-8"
    )
    return [
        {
            "program_id": program_id,
            "judgment_index": index,
            "model_selector": MODEL_SELECTOR,
            "reasoning_effort": REASONING_EFFORT,
            "system_prompt": prompt,
            "program_source": source,
            "allowed_verdicts": sorted(_VERDICTS),
        }
        for index in range(judgments)
    ]


def _prompt_bytes(request: Mapping[str, Any]) -> bytes:
    payload = {
        "program_id": request["program_id"],
        "program_source": request["program_source"],
        "allowed_verdicts": request["allowed_verdicts"],
    }
    return (
        str(request["system_prompt"]).rstrip()
        + "\n\nCURRENT_PROGRAM_INPUT_JSON\n"
        + json.dumps(payload, sort_keys=True, separators=(",", ":"))
        + "\n"
    ).encode("utf-8")


def _argv(*, schema_path: Path, final_path: Path, working_directory: Path) -> list[str]:
    return [
        CODEX_COMMAND,
        "exec",
        "--model",
        MODEL_SELECTOR,
        "-c",
        "model_reasoning_effort=high",
        "-c",
        'web_search="disabled"',
        "-c",
        "agents.enabled=false",
        "-c",
        "features.shell_tool=false",
        "-c",
        "mcp_servers={}",
        "--ignore-user-config",
        "--ignore-rules",
        "--sandbox",
        "read-only",
        "--ephemeral",
        "--skip-git-repo-check",
        "--cd",
        str(working_directory),
        "--json",
        "--output-schema",
        str(schema_path),
        "--output-last-message",
        str(final_path),
        "-",
    ]


def _kill_process_group(process: subprocess.Popen[bytes]) -> None:
    try:
        os.killpg(process.pid, signal.SIGKILL)
    except (ProcessLookupError, PermissionError):
        process.kill()


def _parse_events(raw_jsonl: str) -> tuple[list[Any], list[int]]:
    events: list[Any] = []
    malformed_lines: list[int] = []
    for line_number, line in enumerate(raw_jsonl.splitlines(), start=1):
        if not line.strip():
            continue
        try:
            events.append(json.loads(line))
        except json.JSONDecodeError:
            malformed_lines.append(line_number)
    return events, malformed_lines


def _turn_started(events: list[Any]) -> bool:
    return any(
        isinstance(event, dict) and event.get("type") == "turn.started"
        for event in events
    )


def _contains_refusal(value: Any) -> bool:
    if isinstance(value, dict):
        if value.get("type") == "refusal" or isinstance(value.get("refusal"), str):
            return True
        return any(_contains_refusal(item) for item in value.values())
    if isinstance(value, list):
        return any(_contains_refusal(item) for item in value)
    return False


def _token_usage(events: list[Any]) -> list[Mapping[str, Any]]:
    observed: list[Mapping[str, Any]] = []

    def visit(value: Any) -> None:
        if isinstance(value, dict):
            usage = value.get("usage")
            if isinstance(usage, dict):
                observed.append(dict(usage))
            for item in value.values():
                visit(item)
        elif isinstance(value, list):
            for item in value:
                visit(item)

    visit(events)
    return observed


def _parse_final(raw: str) -> Mapping[str, Any] | None:
    try:
        value = json.loads(raw)
    except json.JSONDecodeError:
        return None
    if not isinstance(value, dict) or set(value) != {
        "verdict",
        "reason_codes",
        "source_locations",
    }:
        return None
    if value.get("verdict") not in _VERDICTS:
        return None
    reasons = value.get("reason_codes")
    if not isinstance(reasons, list) or any(not isinstance(item, str) for item in reasons):
        return None
    locations = value.get("source_locations")
    if not isinstance(locations, list):
        return None
    normalized = []
    for location in locations:
        if not isinstance(location, dict) or set(location) != {"line_start", "line_end"}:
            return None
        start, end = location.get("line_start"), location.get("line_end")
        if (
            isinstance(start, bool)
            or isinstance(end, bool)
            or not isinstance(start, int)
            or not isinstance(end, int)
            or start < 1
            or end < start
        ):
            return None
        normalized.append({"line_start": start, "line_end": end})
    return {
        "verdict": value["verdict"],
        "reason_codes": list(reasons),
        "source_locations": normalized,
    }


def codex_cli_invoke(
    request: Mapping[str, Any],
    *,
    binding: Mapping[str, Any],
    timeout_seconds: float,
    root: Path | None = None,
) -> Mapping[str, Any]:
    """Dispatch one fresh CLI judgment and retain all non-secret raw output."""

    root = (root or repository_root()).resolve()
    errors = validate_codex_cli_binding(binding, root=root)
    if errors:
        raise ValueError("invalid Codex CLI binding: " + ",".join(errors))
    if timeout_seconds <= 0:
        return {
            "status": "timeout",
            "verdict": None,
            "reason_codes": ["LLM_SHARED_WALL_BUDGET_EXHAUSTED"],
            "dispatched": False,
        }

    prompt = _prompt_bytes(request)
    started_utc = _utc_now()
    started = time.monotonic()
    with tempfile.TemporaryDirectory(prefix="bisafecode-s4-cli-judge-") as directory:
        workdir = Path(directory).resolve()
        schema_path = workdir / "judge_output.schema.json"
        final_path = workdir / "final.json"
        schema_path.write_bytes(
            (root / _contract_path("judge_output.schema.json")).read_bytes()
        )
        argv = _argv(
            schema_path=schema_path,
            final_path=final_path,
            working_directory=workdir,
        )
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
                "dispatched": False,
                "program_id": request["program_id"],
                "judgment_index": request["judgment_index"],
                "started_utc": started_utc,
                "finished_utc": _utc_now(),
                "elapsed_seconds": time.monotonic() - started,
                "exit_code": None,
                "argv_without_prompt": argv,
                "working_directory_policy": "fresh_empty_temporary_directory_deleted_after_call",
                "environment_policy": {
                    "openai_api_key_removed": True,
                    "codex_api_key_removed": True,
                    "chatgpt_auth_cache_reused": True,
                },
                "raw_jsonl": "",
                "jsonl_events": [],
                "jsonl_malformed_line_numbers": [],
                "stderr": f"{type(error).__name__}:{error}",
                "final_response_raw": "",
                "token_usage": [],
                "turn_started": False,
                "status": "cli_or_service_error",
                "verdict": None,
                "reason_codes": ["CODEX_CLI_PROCESS_LAUNCH_FAILED"],
            }
        timed_out = False
        try:
            stdout_bytes, stderr_bytes = process.communicate(
                input=prompt, timeout=timeout_seconds
            )
        except subprocess.TimeoutExpired:
            timed_out = True
            _kill_process_group(process)
            stdout_bytes, stderr_bytes = process.communicate()
        except Exception as error:
            _kill_process_group(process)
            try:
                stdout_bytes, stderr_bytes = process.communicate()
            except Exception:
                stdout_bytes, stderr_bytes = b"", b""
            stderr_bytes += f"\n{type(error).__name__}:{error}".encode("utf-8")
            process.returncode = process.returncode if process.returncode is not None else -1
        final_raw = final_path.read_text(encoding="utf-8") if final_path.is_file() else ""

    elapsed = time.monotonic() - started
    raw_jsonl = stdout_bytes.decode("utf-8", "replace")
    stderr = stderr_bytes.decode("utf-8", "replace")
    events, malformed_lines = _parse_events(raw_jsonl)
    parsed_final = _parse_final(final_raw) if final_raw else None
    base: dict[str, Any] = {
        "dispatched": True,
        "program_id": request["program_id"],
        "judgment_index": request["judgment_index"],
        "started_utc": started_utc,
        "finished_utc": _utc_now(),
        "elapsed_seconds": elapsed,
        "exit_code": process.returncode,
        "argv_without_prompt": argv,
        "working_directory_policy": "fresh_empty_temporary_directory_deleted_after_call",
        "environment_policy": {
            "openai_api_key_removed": True,
            "codex_api_key_removed": True,
            "chatgpt_auth_cache_reused": True,
        },
        "raw_jsonl": raw_jsonl,
        "jsonl_events": events,
        "jsonl_malformed_line_numbers": malformed_lines,
        "stderr": stderr,
        "final_response_raw": final_raw,
        "token_usage": _token_usage(events),
        "turn_started": _turn_started(events),
    }
    combined_error = "\n".join((raw_jsonl, stderr, final_raw))
    if timed_out:
        return {
            **base,
            "status": "timeout",
            "verdict": None,
            "reason_codes": ["CODEX_CLI_JUDGMENT_TIMEOUT"],
        }
    if process.returncode != 0:
        quota_before_dispatch = bool(_QUOTA_RE.search(combined_error)) and not base[
            "turn_started"
        ]
        return {
            **base,
            "status": (
                "quota_window_interruption"
                if quota_before_dispatch
                else "cli_or_service_error"
            ),
            "verdict": None,
            "reason_codes": [
                "CODEX_SUBSCRIPTION_QUOTA_WINDOW_UNAVAILABLE"
                if quota_before_dispatch
                else "CODEX_CLI_OR_SERVICE_NONZERO_EXIT"
            ],
        }
    if malformed_lines:
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
    if parsed_final is None:
        return {
            **base,
            "status": "malformed_response",
            "verdict": None,
            "reason_codes": ["CODEX_FINAL_RESPONSE_SCHEMA_MISMATCH"],
        }
    return {
        **base,
        "status": "completed",
        "verdict": parsed_final["verdict"],
        "reason_codes": parsed_final["reason_codes"],
        "final_response": parsed_final,
    }


def run_codex_cli_judge(
    source: str,
    *,
    program_id: str,
    binding: Mapping[str, Any],
    total_timeout_seconds: float = 120.0,
    invoke: Callable[[Mapping[str, Any], float], Mapping[str, Any]] | None = None,
) -> Mapping[str, Any]:
    """Run the five frozen judgments under one deadline and without retries."""

    if total_timeout_seconds < 0:
        raise ValueError("LLM total timeout must be nonnegative")
    requests = build_judgment_requests(
        source, program_id=program_id, binding=binding
    )
    dispatch = invoke or (
        lambda request, remaining: codex_cli_invoke(
            request,
            binding=binding,
            timeout_seconds=remaining,
        )
    )
    deadline = time.monotonic() + total_timeout_seconds
    judgments: list[Mapping[str, Any]] = []
    next_judgment_index: int | None = None
    for index, request in enumerate(requests):
        remaining = deadline - time.monotonic()
        if remaining <= 0:
            judgments.extend(
                {
                    "program_id": pending["program_id"],
                    "judgment_index": pending["judgment_index"],
                    "status": "timeout",
                    "verdict": None,
                    "reason_codes": ["LLM_SHARED_WALL_BUDGET_EXHAUSTED"],
                    "dispatched": False,
                }
                for pending in requests[index:]
            )
            break
        try:
            result = dict(dispatch(request, remaining))
        except TimeoutError:
            result = {
                "status": "timeout",
                "verdict": None,
                "reason_codes": ["CODEX_CLI_JUDGMENT_TIMEOUT"],
                "dispatched": True,
            }
        except Exception as error:
            result = {
                "status": "cli_or_service_error",
                "verdict": None,
                "reason_codes": [f"CODEX_CLI_{type(error).__name__.upper()}"],
                "dispatched": False,
            }
        result.setdefault("program_id", program_id)
        result.setdefault("judgment_index", index)
        judgments.append(result)
        if result.get("status") == "quota_window_interruption":
            next_judgment_index = index
            break

    completed = [
        str(item["verdict"])
        for item in judgments
        if item.get("status") == "completed" and item.get("verdict") in _VERDICTS
    ]
    verdict_counts = Counter(completed)
    status_counts = Counter(str(item.get("status")) for item in judgments)
    completed_records = [
        item for item in judgments if item.get("status") == "completed"
    ]
    source_location_report_count = sum(
        bool(item.get("final_response", {}).get("source_locations"))
        for item in completed_records
        if isinstance(item.get("final_response"), Mapping)
    )
    source_location_span_count = sum(
        len(item.get("final_response", {}).get("source_locations", ()))
        for item in completed_records
        if isinstance(item.get("final_response"), Mapping)
    )
    if completed:
        ordered = verdict_counts.most_common()
        if len(ordered) > 1 and ordered[0][1] == ordered[1][1]:
            verdict, reasons = "unknown", ["LLM_JUDGMENT_TIE"]
        else:
            verdict, reasons = ordered[0][0], []
    else:
        verdict = "unknown"
        reasons = [
            "NO_VALID_LLM_JUDGMENT",
            *sorted(
                {
                    reason
                    for item in judgments
                    for reason in item.get("reason_codes", ())
                    if isinstance(reason, str)
                }
            ),
        ]

    if next_judgment_index is not None:
        execution_status = "quota_window_interruption"
    elif not completed and status_counts["timeout"] == len(judgments):
        execution_status = "timeout"
    elif not completed and status_counts["cli_or_service_error"]:
        execution_status = "cli_or_service_error"
    else:
        execution_status = "ok"
    return {
        "verdict": verdict,
        "reason_codes": reasons,
        "states": JUDGMENTS_PER_PROGRAM,
        "transitions": len(judgments),
        "model_time_ns": 0,
        "judgments": judgments,
        "execution_status": execution_status,
        "aggregation": {
            "valid_count": len(completed),
            "verdict_counts": dict(sorted(verdict_counts.items())),
            "judgment_status_counts": dict(sorted(status_counts.items())),
            "policy": "plurality-with-tie-to-unknown/v4-codex-cli",
            "scheduled_count": JUDGMENTS_PER_PROGRAM,
            "attempted_or_retained_count": len(judgments),
            "not_yet_dispatched_count": JUDGMENTS_PER_PROGRAM - len(judgments),
            "next_judgment_index_after_quota": next_judgment_index,
            "shared_wall_budget_seconds": total_timeout_seconds,
            "retry_count": 0,
            "source_location_schema_valid_count": sum(
                isinstance(item.get("final_response"), Mapping)
                for item in completed_records
            ),
            "source_location_report_count": source_location_report_count,
            "source_location_span_count": source_location_span_count,
        },
    }
