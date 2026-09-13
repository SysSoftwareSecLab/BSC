"""Mechanical validators for EXP-S4-002 identities and phase records.

FREEZE_ONLY_NOT_FORMAL_STAGE4_EVIDENCE
"""

from __future__ import annotations

import re
from typing import Any, List, Mapping

from . import EXPERIMENT_ID, FORMAL_RAW_STATUS, FREEZE_STATUS, LOCKED_RAW_STATUS, SPLIT
from .methods import method_ids
from .provenance import validate_run_manifest
from .schedule import is_scheduled


SHA256_RE = re.compile(r"^[0-9a-f]{64}$")
COMMIT_RE = re.compile(r"^[0-9a-f]{40}$")
FOUR_VERDICTS = {"verified-within-bounds", "violated", "unknown", "invalid"}
RUN_STATUSES = {
    "ok",
    "timeout",
    "missing",
    "exception",
    "provider_failure",
    "cli_or_service_error",
    "quota_window_interruption",
    "resource_truncated",
}
FORBIDDEN_LABEL_KEYS = {
    "expected_verdict",
    "oracle_label",
    "target_class",
    "target_verdict",
    "verifier_result",
}


def _is_sha(value: Any) -> bool:
    return isinstance(value, str) and bool(SHA256_RE.fullmatch(value))


def _forbidden_keys(value: Any):
    if isinstance(value, dict):
        for key, item in value.items():
            if str(key).lower() in FORBIDDEN_LABEL_KEYS:
                yield str(key)
            yield from _forbidden_keys(item)
    elif isinstance(value, list):
        for item in value:
            yield from _forbidden_keys(item)


def validate_attempt_record(record: Mapping[str, Any]) -> List[str]:
    errors: List[str] = []
    if record.get("evidence_status") != LOCKED_RAW_STATUS:
        errors.append("attempt evidence status mismatch")
    family_id, seed = record.get("family_id"), record.get("seed")
    if not is_scheduled(str(family_id), seed):
        errors.append("attempt is outside exact schedule")
    if record.get("split") != SPLIT:
        errors.append("attempt split mismatch")
    for key in (
        "source_sha256",
        "canonical_ast_hash",
        "structural_family_signature_hash",
        "generator_sha256",
        "toolchain_root_sha256",
    ):
        if not _is_sha(record.get(key)):
            errors.append(f"{key} must be SHA-256")
    forbidden = sorted(set(_forbidden_keys(record)))
    if forbidden:
        errors.append("forbidden label/result keys: " + ",".join(forbidden))
    status = record.get("status")
    reasons = record.get("rejection_reasons")
    if status not in {"accepted", "rejected"} or not isinstance(reasons, list):
        errors.append("invalid admission status/reasons")
    elif status == "accepted" and reasons:
        errors.append("accepted attempt has rejection reasons")
    elif status == "rejected" and not reasons:
        errors.append("rejected attempt lacks reason")
    errors.extend(f"run_manifest:{error}" for error in validate_run_manifest(record.get("run_manifest", {})))
    return errors


def validate_method_record(record: Mapping[str, Any]) -> List[str]:
    errors: List[str] = []
    if record.get("evidence_status") != FORMAL_RAW_STATUS:
        errors.append("method evidence status mismatch")
    if record.get("method_id") not in set(method_ids()):
        errors.append("unknown method_id")
    status = record.get("status")
    if status not in RUN_STATUSES:
        errors.append("invalid method status")
    verdict = record.get("verdict")
    if status == "ok" and verdict not in FOUR_VERDICTS:
        errors.append("ok method record requires four-valued verdict")
    if status != "ok" and verdict is not None:
        errors.append("non-ok method record verdict must be null")
    if not _is_sha(record.get("source_sha256")):
        errors.append("source_sha256 must be SHA-256")
    for key in (
        "runtime_ms",
        "peak_memory_bytes",
        "states",
        "transitions",
        "model_time_ns",
        "timeout",
        "reason_codes",
        "stdout_sha256",
        "stderr_sha256",
        "stdout_path",
        "stderr_path",
        "stdout_size_bytes",
        "stderr_size_bytes",
        "budget",
        "worker_exit_code",
    ):
        if key not in record:
            errors.append(f"method record missing {key}")
    if record.get("timeout") is not (status == "timeout"):
        errors.append("timeout flag inconsistent with status")
    errors.extend(f"run_manifest:{error}" for error in validate_run_manifest(record.get("run_manifest", {})))
    return errors


def validate_oracle_record(record: Mapping[str, Any]) -> List[str]:
    errors: List[str] = []
    if record.get("evidence_status") != FORMAL_RAW_STATUS:
        errors.append("oracle evidence status mismatch")
    status = record.get("status")
    if status not in RUN_STATUSES:
        errors.append("invalid oracle status")
    verdict = record.get("verdict")
    if status == "ok" and verdict not in {"safe", "unsafe", "unknown", "invalid"}:
        errors.append("ok oracle record requires program verdict")
    if status != "ok" and verdict is not None:
        errors.append("failed oracle verdict must be null")
    if not _is_sha(record.get("source_sha256")):
        errors.append("source_sha256 must be SHA-256")
    for key in (
        "runtime_ms", "peak_memory_bytes", "timeout", "reason_codes",
        "logical_evidence_sha256", "geometry_evidence_sha256",
        "stdout_sha256", "stderr_sha256", "stdout_path", "stderr_path",
        "asset_root_sha256", "verifier_outputs_visible", "run_manifest",
        "worker_exit_code", "budget",
    ):
        if key not in record:
            errors.append(f"oracle record missing {key}")
    if record.get("timeout") is not (status == "timeout"):
        errors.append("oracle timeout flag inconsistent")
    if record.get("verifier_outputs_visible") is not False:
        errors.append("oracle verifier blindness violated")
    errors.extend(f"run_manifest:{error}" for error in validate_run_manifest(record.get("run_manifest", {})))
    return errors


def validate_approval_record(approval: Mapping[str, Any]) -> List[str]:
    errors: List[str] = []
    if approval.get("schema") != "bisafecode.stage4.controlled.mac-approval/v1":
        errors.append("approval schema mismatch")
    if approval.get("evidence_status") != FREEZE_STATUS:
        errors.append("approval evidence marker mismatch")
    if approval.get("experiment_id") != EXPERIMENT_ID:
        errors.append("approval experiment mismatch")
    if approval.get("reviewer") != "Mac":
        errors.append("approval reviewer must be Mac")
    for key in ("formal_run_id", "operator_id"):
        if not isinstance(approval.get(key), str) or not approval.get(key):
            errors.append(f"approval {key} missing")
    if approval.get("approved") is not True or approval.get("status") != (
        "APPROVED_FOR_LOCKED_GENERATION_AND_FORMAL_RUN"
    ):
        errors.append("Mac formal approval is absent")
    if not isinstance(approval.get("freeze_commit"), str) or not COMMIT_RE.fullmatch(
        approval["freeze_commit"]
    ):
        errors.append("freeze_commit must be exact Git object ID")
    for key in ("asset_root_sha256", "contract_root_sha256", "toolchain_root_sha256"):
        if not _is_sha(approval.get(key)):
            errors.append(f"approval {key} must be SHA-256")
    llm = approval.get("llm_baseline")
    if not isinstance(llm, dict):
        errors.append("llm baseline binding missing")
    else:
        model_id = llm.get("model_id")
        if not isinstance(model_id, str) or not model_id or "unavailable" in model_id:
            errors.append("exact LLM baseline model ID missing")
        if llm.get("provider_adapter_id") != "codex_cli_jsonl_v1":
            errors.append("LLM Codex CLI adapter missing or unsupported")
        exact_llm_values = {
            "model_id": "gpt-5.5",
            "model_selector": "gpt-5.5",
            "reasoning_effort": "high",
            "interface": "codex exec",
            "codex_command": "codex",
            "codex_cli_version": "codex-cli 0.148.0-alpha.9",
            "authentication": "chatgpt_subscription",
            "login_status": "Logged in using ChatGPT",
            "api_key_env_removed": "OPENAI_API_KEY",
            "invocation_origin": "chatgpt_authenticated_codex_cli",
            "paper_label": "Codex-CLI-mediated GPT-5.5 LLM-as-judge baseline",
            "reproducibility_wording": "model-family-pinned and protocol-reproducible",
        }
        for key, expected in exact_llm_values.items():
            if llm.get(key) != expected:
                errors.append(f"LLM {key} binding mismatch")
        for key in (
            "prompt_sha256",
            "cli_invocation_schema_sha256",
            "judge_output_schema_sha256",
            "aggregation_policy_sha256",
        ):
            if not _is_sha(llm.get(key)):
                errors.append(f"LLM {key} missing")
        for key in ("codex_cli_version", "authentication", "invocation_origin"):
            if not isinstance(llm.get(key), str) or not llm.get(key):
                errors.append(f"LLM {key} missing")
        if llm.get("invocation_origin") == "current_codex_conversation":
            errors.append("current Codex conversation cannot be the LLM baseline")
        required_true = (
            "fresh_process_per_judgment",
            "raw_jsonl_retention",
            "final_response_retention",
        )
        required_false = (
            "current_codex_conversation_allowed",
            "resume_allowed",
            "user_config_loaded",
            "web_search_enabled",
            "shell_tool_enabled",
            "subagents_enabled",
        )
        for key in required_true:
            if llm.get(key) is not True:
                errors.append(f"LLM {key} must be true")
        for key in required_false:
            if llm.get(key) is not False:
                errors.append(f"LLM {key} must be false")
    return errors
