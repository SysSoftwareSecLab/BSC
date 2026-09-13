"""Create-once, tool-disabled natural-LLM authoring adapter."""

from __future__ import annotations

import json
import os
import signal
import subprocess
import tempfile
import time
from pathlib import Path
from typing import Any, Mapping

from . import CONTRACT_RELATIVE
from .contracts import load_json


MODEL_SELECTOR = "gpt-5.6-sol"
REASONING_EFFORT = "high"
CODEX_CLI_VERSION = "codex-cli 0.148.0-alpha.9"
ATTEMPT_TIMEOUT_SECONDS = 120.0
ALLOWED_ITEM_TYPES = {"agent_message", "reasoning", "error"}


def build_requests(root: Path) -> list[Mapping[str, Any]]:
    cards_document = load_json(root, "HUMAN_TASK_CARDS.json")
    cards = {card["card_id"]: card for card in cards_document["cards"]}
    manual = (root / CONTRACT_RELATIVE / "PUBLIC_AUTHORING_MANUAL.md").read_text(encoding="utf-8")
    system_prompt = (root / CONTRACT_RELATIVE / "LLM_GENERATOR_PROMPT.txt").read_text(encoding="utf-8")
    return [
        {
            "attempt_ordinal": ordinal,
            "task_card_id": card_id,
            "system_prompt": system_prompt,
            "public_authoring_manual": manual,
            "task_card": cards[card_id],
        }
        for ordinal, card_id in enumerate(cards_document["llm_assignment"], 1)
    ]


def prompt_bytes(request: Mapping[str, Any]) -> bytes:
    scientific_inputs = {
        "public_authoring_manual": request["public_authoring_manual"],
        "task_card": request["task_card"],
    }
    return (
        str(request["system_prompt"]).rstrip()
        + "\n\nAUTHORING_INPUT_JSON\n"
        + json.dumps(scientific_inputs, sort_keys=True, separators=(",", ":"), ensure_ascii=False)
        + "\n"
    ).encode("utf-8")


def command(schema_path: Path, output_path: Path, workdir: Path) -> list[str]:
    return [
        "codex", "exec", "--model", MODEL_SELECTOR,
        "-c", "model_reasoning_effort=high",
        "-c", 'web_search="disabled"',
        "-c", "agents.enabled=false",
        "-c", "features.shell_tool=false",
        "-c", "mcp_servers={}",
        "--ignore-user-config", "--ignore-rules", "--sandbox", "read-only",
        "--ephemeral", "--skip-git-repo-check", "--cd", str(workdir), "--json",
        "--output-schema", str(schema_path), "--output-last-message", str(output_path), "-",
    ]


def audit_stream(raw_jsonl: str) -> Mapping[str, Any]:
    malformed: list[int] = []
    item_types: list[str] = []
    for line_number, line in enumerate(raw_jsonl.splitlines(), 1):
        try:
            event = json.loads(line)
        except json.JSONDecodeError:
            malformed.append(line_number)
            continue
        if not isinstance(event, dict) or event.get("type") not in {"item.started", "item.completed"}:
            continue
        item = event.get("item")
        if isinstance(item, dict):
            item_types.append(str(item.get("type")))
    disallowed = sorted(set(item_types) - ALLOWED_ITEM_TYPES)
    return {
        "observed_item_types": sorted(set(item_types)),
        "disallowed_item_types": disallowed,
        "tool_or_file_event_count": sum(item not in ALLOWED_ITEM_TYPES for item in item_types),
        "malformed_jsonl_line_numbers": malformed,
        "passed": not disallowed,
    }


def invoke_once(request: Mapping[str, Any], *, root: Path, timeout_seconds: float = ATTEMPT_TIMEOUT_SECONDS) -> Mapping[str, Any]:
    started = time.monotonic()
    with tempfile.TemporaryDirectory(prefix="bisafecode-s4-008-author-") as directory:
        workdir = Path(directory).resolve()
        schema_path = workdir / "generator_output.schema.json"
        output_path = workdir / "final.json"
        schema_path.write_bytes((root / CONTRACT_RELATIVE / "generator_output.schema.json").read_bytes())
        argv = command(schema_path, output_path, workdir)
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
                "status": "llm_service_or_quota_interruption",
                "error": f"{type(error).__name__}:{error}",
                "dispatched": False,
            }
        timed_out = False
        try:
            stdout, stderr = process.communicate(input=prompt_bytes(request), timeout=timeout_seconds)
        except subprocess.TimeoutExpired:
            timed_out = True
            try:
                os.killpg(process.pid, signal.SIGKILL)
            except (ProcessLookupError, PermissionError):
                process.kill()
            stdout, stderr = process.communicate()
        raw_jsonl = stdout.decode("utf-8", "replace")
        stderr_text = stderr.decode("utf-8", "replace")
        final_raw = output_path.read_text(encoding="utf-8") if output_path.is_file() else ""
    stream = audit_stream(raw_jsonl)
    result: dict[str, Any] = {
        "attempt_ordinal": request["attempt_ordinal"],
        "task_card_id": request["task_card_id"],
        "model_selector": MODEL_SELECTOR,
        "reasoning_effort": REASONING_EFFORT,
        "elapsed_seconds": time.monotonic() - started,
        "dispatched": True,
        "exit_code": process.returncode,
        "raw_jsonl": raw_jsonl,
        "stderr": stderr_text,
        "stream_boundary_audit": stream,
        "final_response_raw": final_raw,
    }
    if not stream["passed"]:
        result["status"] = "provenance_ineligible"
        return result
    if timed_out:
        result["status"] = "llm_timeout"
        return result
    if process.returncode != 0:
        result["status"] = "llm_service_or_quota_interruption"
        return result
    try:
        final = json.loads(final_raw)
    except json.JSONDecodeError:
        result["status"] = "llm_malformed"
        return result
    if not isinstance(final, dict) or not isinstance(final.get("program_source"), str):
        result["status"] = "llm_malformed"
        return result
    result["status"] = "completed"
    result["program_source"] = final["program_source"]
    result["brief_author_note"] = str(final.get("brief_author_note", ""))
    return result
