"""Approval-gated Raw→Derived execution for EXP-S4-006."""

from __future__ import annotations

import hashlib
import json
import subprocess
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Mapping

from . import (
    CONTRACT_RELATIVE,
    DERIVED_RELATIVE,
    EXPERIMENT_ID,
    METHOD_IDS,
    METHOD_LLM,
    METHOD_RANDOM,
    RAW_RELATIVE,
    SOURCE_RAW_RELATIVE,
)
from .context import repository_root, sha256_file
from .llm_full_context import build_binding, discover_cli_identity, run_full_context_judge
from .random_timed import run_random_timed_schedule


GENERATION_ROOT = "8209e83fe05519888c5721727b83bb1cdc7a5b1bb8e02a692a9a41d7bb14fa1f"
ORACLE_ROOT = "22499bf22b8ffe59d2963a67a7cedbdd0a8194e8927c54c95aa529a1d7c1b87b"
RAW_STATUS = "FORMAL_RAW_PENDING_MAC_REVIEW"
DERIVED_STATUS = "DERIVED_PENDING_MAC_REVIEW"


class FormalGateLocked(RuntimeError):
    pass


def _load(path: Path) -> Any:
    return json.loads(path.read_text(encoding="utf-8"))


def _write_once(path: Path, value: Any) -> None:
    if path.exists():
        raise FormalGateLocked(f"create-once output exists: {path}")
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps(value, indent=2, sort_keys=True, ensure_ascii=False, default=str) + "\n",
        encoding="utf-8",
    )


def _head(root: Path) -> str:
    return subprocess.check_output(
        ["git", "rev-parse", "HEAD"], cwd=root, text=True
    ).strip()


def _utc() -> str:
    return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")


def _program_ids() -> tuple[str, ...]:
    return tuple(f"EXP-S4-002-P{index:03d}" for index in range(1, 61))


def validate_seal(root: Path, relative: str, expected_root: str) -> Mapping[str, Any]:
    seal_path = root / relative
    seal = _load(seal_path)
    if seal.get("root_sha256") != expected_root:
        raise FormalGateLocked(f"sealed input root mismatch: {relative}")
    data_root = seal_path.parents[1]
    files = seal.get("files")
    if not isinstance(files, list) or not files:
        raise FormalGateLocked(f"sealed input inventory missing: {relative}")
    for item in files:
        path = data_root / str(item.get("path"))
        if not path.is_file() or sha256_file(path) != item.get("sha256"):
            raise FormalGateLocked(f"sealed input file mismatch: {item.get('path')}")
    return seal


def validate_generation_inputs(root: Path) -> Mapping[str, Any]:
    validate_seal(
        root,
        f"{SOURCE_RAW_RELATIVE}/seals/generation.json",
        GENERATION_ROOT,
    )
    manifest = _load(root / SOURCE_RAW_RELATIVE / "locked_manifest.json")
    if manifest.get("N") != 60 or manifest.get("program_ids") != list(_program_ids()):
        raise FormalGateLocked("sealed EXP-S4-002 population mismatch")
    for item in manifest.get("attempts", []):
        source = root / SOURCE_RAW_RELATIVE / str(item.get("source_path"))
        if not source.is_file() or sha256_file(source) != item.get("source_sha256"):
            raise FormalGateLocked(f"source identity mismatch: {item.get('program_id')}")
    return manifest


def validate_approval(
    approval: Mapping[str, Any], *, root: Path, expected_commit: str
) -> None:
    required = {
        "schema": "bisafecode.stage4.baseline-correction.approval/v1",
        "evidence_status": "LOCAL_EXECUTION_AUTHORIZATION_NOT_PAPER_EVIDENCE",
        "experiment_id": EXPERIMENT_ID,
        "approved": True,
        "reviewer": "Anonymous User",
        "freeze_commit": expected_commit,
        "authorized_methods": list(METHOD_IDS),
        "reuse_sealed_oracle_for_derived": True,
        "paper_export_authorized": False,
        "status": "AUTHORIZED",
        "protocol_sha256": sha256_file(root / CONTRACT_RELATIVE / "PROTOCOL.json"),
        "llm_binding": dict(build_binding(root=root)),
    }
    if dict(approval) != required:
        raise FormalGateLocked("approval does not exactly bind the freeze commit and protocol")


def freeze_check(root: Path | None = None) -> Mapping[str, Any]:
    root = (root or repository_root()).resolve()
    manifest = validate_generation_inputs(root)
    context_binding = build_binding(root=root)
    cli = discover_cli_identity()
    output_absent = not (root / RAW_RELATIVE).exists() and not (root / DERIVED_RELATIVE).exists()
    return {
        "experiment_id": EXPERIMENT_ID,
        "freeze_commit": _head(root),
        "population_count": len(manifest["program_ids"]),
        "generation_root_sha256": GENERATION_ROOT,
        "oracle_root_sha256_bound_for_later_derived": ORACLE_ROOT,
        "protocol_sha256": sha256_file(root / CONTRACT_RELATIVE / "PROTOCOL.json"),
        "llm_binding": context_binding,
        "cli_identity": cli,
        "formal_outputs_absent": output_absent,
        "ready": (
            output_absent
            and cli.get("codex_cli_version") == context_binding["codex_cli_version"]
            and cli.get("login_status") == "Logged in using ChatGPT"
        ),
        "formal_runs_this_check": 0,
    }


def _input_binding(root: Path, commit: str) -> Mapping[str, Any]:
    return {
        "evidence_status": RAW_STATUS,
        "experiment_id": EXPERIMENT_ID,
        "freeze_commit": commit,
        "source_experiment": "EXP-S4-002_CONTROLLED_UNSEEN_PROGRAM_CORRECTNESS",
        "generation_root_sha256": GENERATION_ROOT,
        "oracle_root_sha256_for_later_derived": ORACLE_ROOT,
        "protocol_sha256": sha256_file(root / CONTRACT_RELATIVE / "PROTOCOL.json"),
        "created_utc": _utc(),
        "paper_result_eligible": False,
    }


def _ensure_raw_binding(root: Path, commit: str) -> None:
    path = root / RAW_RELATIVE / "input_binding.json"
    if path.exists():
        existing = _load(path)
        for key, value in _input_binding(root, commit).items():
            if key != "created_utc" and existing.get(key) != value:
                raise FormalGateLocked("existing EXP-S4-006 input binding mismatch")
        return
    _write_once(path, _input_binding(root, commit))


def _seal_method(root: Path, method_id: str, program_ids: tuple[str, ...]) -> Mapping[str, Any]:
    method_root = root / RAW_RELATIVE / "methods" / method_id
    files = []
    for path in sorted(method_root.glob("*")):
        if path.is_file():
            files.append({
                "path": str(path.relative_to(root / RAW_RELATIVE)),
                "sha256": sha256_file(path),
                "size_bytes": path.stat().st_size,
            })
    payload = "\n".join(f"{item['path']}\0{item['sha256']}\0{item['size_bytes']}" for item in files) + "\n"
    seal = {
        "evidence_status": RAW_STATUS,
        "experiment_id": EXPERIMENT_ID,
        "phase": method_id,
        "record_count": len(program_ids),
        "program_ids": list(program_ids),
        "files": files,
        "root_sha256": hashlib.sha256(payload.encode("utf-8")).hexdigest(),
        "source_generation_root_sha256": GENERATION_ROOT,
    }
    _write_once(root / RAW_RELATIVE / "seals" / f"{method_id}.json", seal)
    return seal


def run_method(
    method_id: str,
    *,
    approval: Mapping[str, Any],
    root: Path | None = None,
) -> Mapping[str, Any]:
    root = (root or repository_root()).resolve()
    commit = _head(root)
    validate_approval(approval, root=root, expected_commit=commit)
    manifest = validate_generation_inputs(root)
    if method_id not in METHOD_IDS:
        raise FormalGateLocked("method is not authorized")
    method_root = root / RAW_RELATIVE / "methods" / method_id
    seal_path = root / RAW_RELATIVE / "seals" / f"{method_id}.json"
    if method_root.exists() or seal_path.exists():
        raise FormalGateLocked(f"formal method output is create-once: {method_id}")
    if method_id == METHOD_LLM:
        cli = discover_cli_identity()
        if cli.get("codex_cli_version") != build_binding(root=root)["codex_cli_version"] or cli.get("login_status") != "Logged in using ChatGPT":
            raise FormalGateLocked("live Codex CLI identity does not match the frozen binding")
    _ensure_raw_binding(root, commit)
    program_ids = _program_ids()
    records = []
    for item in manifest["attempts"]:
        program_id = str(item["program_id"])
        source = (root / SOURCE_RAW_RELATIVE / str(item["source_path"])).read_text(encoding="utf-8")
        if method_id == METHOD_RANDOM:
            artifact = dict(run_random_timed_schedule(source, program_id=program_id))
            record = {
                "evidence_status": RAW_STATUS,
                "experiment_id": EXPERIMENT_ID,
                "method_id": method_id,
                "program_id": program_id,
                "source_sha256": item["source_sha256"],
                "status": artifact["status"],
                "prediction": artifact["predicted_class"],
                "reason_codes": artifact["reason_codes"],
                "scheduled_rollouts": artifact["scheduled_rollouts"],
                "completed_rollouts": artifact["completed_rollouts"],
                "violation_rollouts": artifact["violation_rollouts"],
                "safe_completion_rollouts": artifact["safe_completion_rollouts"],
                "inconclusive_rollouts": artifact["inconclusive_rollouts"],
                "runtime_ms": artifact["elapsed_ms"],
                "no_exhaustive_claim": True,
            }
        else:
            artifact = dict(run_full_context_judge(
                source,
                program_id=program_id,
                binding=approval["llm_binding"],
            ))
            record = {
                "evidence_status": RAW_STATUS,
                "experiment_id": EXPERIMENT_ID,
                "method_id": method_id,
                "program_id": program_id,
                "source_sha256": item["source_sha256"],
                "status": artifact["status"],
                "prediction": artifact["predicted_verdict"],
                "reason_codes": artifact["reason_codes"],
                "aggregation": artifact["aggregation"],
                "timeout_is_not_judgment_error": True,
            }
        record["freeze_commit"] = commit
        record["paper_result_eligible"] = False
        _write_once(method_root / f"{program_id}.record.json", record)
        _write_once(method_root / f"{program_id}.artifact.json", artifact)
        records.append(record)
    seal = _seal_method(root, method_id, program_ids)
    return {"method_id": method_id, "records": len(records), "seal": seal}


def derive(
    *, approval: Mapping[str, Any], root: Path | None = None
) -> Mapping[str, Any]:
    root = (root or repository_root()).resolve()
    commit = _head(root)
    validate_approval(approval, root=root, expected_commit=commit)
    validate_generation_inputs(root)
    validate_seal(root, f"{SOURCE_RAW_RELATIVE}/seals/oracle.json", ORACLE_ROOT)
    derived_root = root / DERIVED_RELATIVE
    if derived_root.exists():
        raise FormalGateLocked("EXP-S4-006 Derived output is create-once")
    ids = _program_ids()
    labels = {
        program_id: _load(root / SOURCE_RAW_RELATIVE / "oracle" / f"{program_id}.record.json")["verdict"]
        for program_id in ids
    }
    manifest = _load(root / SOURCE_RAW_RELATIVE / "locked_manifest.json")
    family = {str(item["program_id"]): str(item["family_id"]) for item in manifest["attempts"]}
    summaries = {}
    for method_id in METHOD_IDS:
        records = [_load(root / RAW_RELATIVE / "methods" / method_id / f"{program_id}.record.json") for program_id in ids]
        correct = 0
        unknown = 0
        status_counts: dict[str, int] = {}
        per_family: dict[str, dict[str, int]] = {}
        for record in records:
            status_counts[record["status"]] = status_counts.get(record["status"], 0) + 1
            prediction = record["prediction"]
            normalized = {
                "verified-within-bounds": "safe",
                "violated": "unsafe",
                "safe": "safe",
                "unsafe": "unsafe",
            }.get(prediction)
            is_correct = normalized == labels[record["program_id"]]
            correct += int(is_correct)
            unknown += int(normalized is None)
            cell = per_family.setdefault(family[record["program_id"]], {"N": 0, "correct": 0, "unknown_or_invalid": 0})
            cell["N"] += 1
            cell["correct"] += int(is_correct)
            cell["unknown_or_invalid"] += int(normalized is None)
        summary: dict[str, Any] = {
            "N": len(ids),
            "correct": correct,
            "accuracy": correct / len(ids),
            "unknown_or_invalid_predictions": unknown,
            "status_counts": dict(sorted(status_counts.items())),
            "per_family": dict(sorted(per_family.items())),
        }
        if method_id == METHOD_LLM:
            completed_total = completed_correct = timeouts = refusals = malformed = service = quota = 0
            for record in records:
                oracle = labels[record["program_id"]]
                artifact = _load(root / RAW_RELATIVE / "methods" / method_id / f"{record['program_id']}.artifact.json")
                for judgment in artifact["judgments"]:
                    status = judgment.get("status")
                    timeouts += int(status == "timeout")
                    refusals += int(status == "refusal")
                    malformed += int(status == "malformed_response")
                    service += int(status == "cli_or_service_error")
                    quota += int(status == "quota_window_interruption")
                    if status == "completed":
                        completed_total += 1
                        normalized = {"verified-within-bounds": "safe", "violated": "unsafe"}.get(judgment.get("verdict"))
                        completed_correct += int(normalized == oracle)
            summary["judgment_accounting"] = {
                "completed": completed_total,
                "completed_correct": completed_correct,
                "conditional_completed_accuracy": completed_correct / completed_total if completed_total else None,
                "timeout": timeouts,
                "refusal": refusals,
                "malformed_response": malformed,
                "cli_or_service_error": service,
                "quota_window_interruption": quota,
                "timeout_counted_as_judgment_error": False,
            }
        summaries[method_id] = summary
    result = {
        "evidence_status": DERIVED_STATUS,
        "experiment_id": EXPERIMENT_ID,
        "freeze_commit": commit,
        "source_generation_root_sha256": GENERATION_ROOT,
        "reused_oracle_root_sha256": ORACLE_ROOT,
        "methods": summaries,
        "post_hoc_correction_disclosed": True,
        "old_raw_overwritten": False,
        "paper_result_eligible": False,
        "paper_acceptance_required": True,
    }
    _write_once(derived_root / "metric_summary.json", result)
    return result

