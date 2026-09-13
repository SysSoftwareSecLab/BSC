"""Approval-gated method-first/oracle-later execution for EXP-S4-007."""

from __future__ import annotations

import hashlib
import json
import subprocess
from collections import Counter
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Mapping

from bisafecode.stage4_baseline_correction.context import (
    build_full_context,
    repository_root,
    sha256_file,
)
from bisafecode.stage4_baseline_correction.pipeline import (
    GENERATION_ROOT,
    ORACLE_ROOT,
    validate_generation_inputs,
    validate_seal,
)

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
from .gpt_opaque import (
    audit_embedded_context,
    build_binding,
    discover_cli_identity,
    run_gpt_opaque_judge,
)
from .independent_random import run_independent_random_dynamic
from .opaque import opaque_case_id, presentation_key


ASSET_RELATIVE = "03_experiments/contracts/EXP-S4-002_CONTROLLED_UNSEEN_FORMAL_FREEZE/assets"
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
    return subprocess.check_output(["git", "rev-parse", "HEAD"], cwd=root, text=True).strip()


def _utc() -> str:
    return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")


def _program_ids() -> tuple[str, ...]:
    return tuple(f"EXP-S4-002-P{index:03d}" for index in range(1, 61))


def _ordered_attempts(manifest: Mapping[str, Any]) -> list[Mapping[str, Any]]:
    attempts = list(manifest["attempts"])
    return sorted(attempts, key=lambda item: presentation_key(str(item["source_sha256"])))


def validate_approval(
    approval: Mapping[str, Any], *, root: Path, expected_commit: str
) -> None:
    required = {
        "schema": "bisafecode.stage4.baseline-independence-replication.approval/v1",
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
    tokens = [opaque_case_id(str(item["source_sha256"])) for item in manifest["attempts"]]
    binding = build_binding(root=root)
    context_audit = audit_embedded_context(build_full_context(root))
    cli = discover_cli_identity()
    outputs_absent = not (root / RAW_RELATIVE).exists() and not (root / DERIVED_RELATIVE).exists()
    return {
        "experiment_id": EXPERIMENT_ID,
        "freeze_commit": _head(root),
        "population_count": len(tokens),
        "opaque_token_count": len(set(tokens)),
        "original_program_id_in_gpt_prompt": False,
        "opaque_token_in_gpt_prompt": False,
        "embedded_context_metadata_audit": context_audit,
        "random_forbidden_component_imports": [],
        "generation_root_sha256": GENERATION_ROOT,
        "oracle_root_sha256_bound_for_derived_only": ORACLE_ROOT,
        "protocol_sha256": sha256_file(root / CONTRACT_RELATIVE / "PROTOCOL.json"),
        "llm_binding": binding,
        "cli_identity": cli,
        "formal_outputs_absent": outputs_absent,
        "ready": (
            outputs_absent
            and len(tokens) == len(set(tokens)) == 60
            and context_audit["passed"]
            and cli.get("codex_cli_version") == binding["codex_cli_version"]
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
        "method_phase_oracle_access": False,
        "created_utc": _utc(),
        "paper_result_eligible": False,
    }


def _ensure_raw_binding(root: Path, commit: str) -> None:
    path = root / RAW_RELATIVE / "input_binding.json"
    if path.exists():
        existing = _load(path)
        for key, value in _input_binding(root, commit).items():
            if key != "created_utc" and existing.get(key) != value:
                raise FormalGateLocked("existing EXP-S4-007 input binding mismatch")
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
    expected_files = len(program_ids) * 2
    if len(files) != expected_files:
        raise FormalGateLocked(f"method inventory incomplete: {len(files)}/{expected_files}")
    payload = "\n".join(
        f"{item['path']}\0{item['sha256']}\0{item['size_bytes']}" for item in files
    ) + "\n"
    seal = {
        "evidence_status": RAW_STATUS,
        "experiment_id": EXPERIMENT_ID,
        "phase": method_id,
        "record_count": len(program_ids),
        "program_ids": list(program_ids),
        "files": files,
        "root_sha256": hashlib.sha256(payload.encode("utf-8")).hexdigest(),
        "source_generation_root_sha256": GENERATION_ROOT,
        "method_outputs_created_before_oracle_join": True,
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
    seal_path = root / RAW_RELATIVE / "seals" / f"{method_id}.json"
    if seal_path.exists():
        raise FormalGateLocked(f"formal method is already sealed: {method_id}")
    if method_id == METHOD_LLM:
        cli = discover_cli_identity()
        binding = build_binding(root=root)
        if (
            cli.get("codex_cli_version") != binding["codex_cli_version"]
            or cli.get("login_status") != "Logged in using ChatGPT"
        ):
            raise FormalGateLocked("live Codex CLI identity does not match frozen binding")
    _ensure_raw_binding(root, commit)
    method_root = root / RAW_RELATIVE / "methods" / method_id
    completed_now = 0
    retained_existing = 0
    for item in _ordered_attempts(manifest):
        program_id = str(item["program_id"])
        source_sha = str(item["source_sha256"])
        record_path = method_root / f"{program_id}.record.json"
        artifact_path = method_root / f"{program_id}.artifact.json"
        if record_path.exists() or artifact_path.exists():
            if not (record_path.exists() and artifact_path.exists()):
                raise FormalGateLocked(f"partial create-once pair exists: {program_id}")
            record = _load(record_path)
            if (
                record.get("program_id") != program_id
                or record.get("source_sha256") != source_sha
                or record.get("freeze_commit") != commit
            ):
                raise FormalGateLocked(f"existing create-once record binding mismatch: {program_id}")
            retained_existing += 1
            continue
        source = (root / SOURCE_RAW_RELATIVE / str(item["source_path"])).read_text(encoding="utf-8")
        if method_id == METHOD_RANDOM:
            artifact = dict(run_independent_random_dynamic(
                source,
                source_sha256=source_sha,
                asset_root=root / ASSET_RELATIVE,
            ))
            record = {
                "evidence_status": RAW_STATUS,
                "experiment_id": EXPERIMENT_ID,
                "method_id": method_id,
                "program_id": program_id,
                "source_sha256": source_sha,
                "status": artifact["status"],
                "prediction": artifact["predicted_class"],
                "reason_codes": artifact["reason_codes"],
                "scheduled_rollouts": artifact["scheduled_rollouts"],
                "completed_rollouts": artifact["completed_rollouts"],
                "violation_rollouts": artifact["violation_rollouts"],
                "safe_completion_rollouts": artifact["safe_completion_rollouts"],
                "runtime_ms": artifact["elapsed_ms"],
                "formal_engine_or_property_checker_reused": False,
                "no_exhaustive_claim": True,
            }
        else:
            token = opaque_case_id(source_sha)
            artifact = dict(run_gpt_opaque_judge(
                source,
                opaque_case_id=token,
                binding=approval["llm_binding"],
            ))
            record = {
                "evidence_status": RAW_STATUS,
                "experiment_id": EXPERIMENT_ID,
                "method_id": method_id,
                "program_id": program_id,
                "opaque_case_id": token,
                "source_sha256": source_sha,
                "status": artifact["status"],
                "prediction": artifact["predicted_verdict"],
                "reason_codes": artifact["reason_codes"],
                "aggregation": artifact["aggregation"],
                "original_program_id_sent_to_model": False,
                "opaque_case_id_sent_to_model": False,
                "timeout_is_not_judgment_error": True,
            }
        record["freeze_commit"] = commit
        record["paper_result_eligible"] = False
        _write_once(artifact_path, artifact)
        _write_once(record_path, record)
        completed_now += 1
        if method_id == METHOD_LLM and artifact["status"] in {
            "quota_window_interruption", "isolation_violation"
        }:
            return {
                "method_id": method_id,
                "completed_now": completed_now,
                "retained_existing": retained_existing,
                "sealed": False,
                "stop_reason": artifact["status"],
            }
    seal = _seal_method(root, method_id, _program_ids())
    return {
        "method_id": method_id,
        "completed_now": completed_now,
        "retained_existing": retained_existing,
        "sealed": True,
        "seal": seal,
    }


def derive(*, approval: Mapping[str, Any], root: Path | None = None) -> Mapping[str, Any]:
    root = (root or repository_root()).resolve()
    commit = _head(root)
    validate_approval(approval, root=root, expected_commit=commit)
    validate_generation_inputs(root)
    for method_id in METHOD_IDS:
        if not (root / RAW_RELATIVE / "seals" / f"{method_id}.json").is_file():
            raise FormalGateLocked(f"method is not sealed: {method_id}")
    validate_seal(root, f"{SOURCE_RAW_RELATIVE}/seals/oracle.json", ORACLE_ROOT)
    derived_root = root / DERIVED_RELATIVE
    if derived_root.exists():
        raise FormalGateLocked("EXP-S4-007 Derived output is create-once")
    ids = _program_ids()
    labels = {
        program_id: _load(
            root / SOURCE_RAW_RELATIVE / "oracle" / f"{program_id}.record.json"
        )["verdict"]
        for program_id in ids
    }
    manifest = _load(root / SOURCE_RAW_RELATIVE / "locked_manifest.json")
    families = {
        str(item["program_id"]): str(item["family_id"])
        for item in manifest["attempts"]
    }
    summaries: dict[str, Any] = {}
    for method_id in METHOD_IDS:
        records = [
            _load(root / RAW_RELATIVE / "methods" / method_id / f"{program_id}.record.json")
            for program_id in ids
        ]
        correct = unknown = false_safe = false_alarm = 0
        status_counts: dict[str, int] = {}
        per_family: dict[str, dict[str, int]] = {}
        for record in records:
            status_counts[record["status"]] = status_counts.get(record["status"], 0) + 1
            normalized = {
                "verified-within-bounds": "safe",
                "violated": "unsafe",
                "safe": "safe",
                "unsafe": "unsafe",
            }.get(record["prediction"])
            oracle = labels[record["program_id"]]
            correct += int(normalized == oracle)
            unknown += int(normalized is None)
            false_safe += int(normalized == "safe" and oracle == "unsafe")
            false_alarm += int(normalized == "unsafe" and oracle == "safe")
            cell = per_family.setdefault(
                families[record["program_id"]],
                {"N": 0, "correct": 0, "false_safe": 0, "false_alarm": 0},
            )
            cell["N"] += 1
            cell["correct"] += int(normalized == oracle)
            cell["false_safe"] += int(normalized == "safe" and oracle == "unsafe")
            cell["false_alarm"] += int(normalized == "unsafe" and oracle == "safe")
        summary: dict[str, Any] = {
            "N": len(ids),
            "correct": correct,
            "accuracy": correct / len(ids),
            "false_safe": false_safe,
            "false_alarm": false_alarm,
            "unknown_or_invalid_predictions": unknown,
            "status_counts": dict(sorted(status_counts.items())),
            "per_family": dict(sorted(per_family.items())),
        }
        if method_id == METHOD_LLM:
            accounting = Counter()
            completed_correct = 0
            for record in records:
                oracle = labels[record["program_id"]]
                artifact = _load(
                    root / RAW_RELATIVE / "methods" / method_id
                    / f"{record['program_id']}.artifact.json"
                )
                for judgment in artifact["judgments"]:
                    status = str(judgment.get("status"))
                    accounting[status] += 1
                    if status == "completed":
                        normalized = {
                            "verified-within-bounds": "safe",
                            "violated": "unsafe",
                        }.get(judgment.get("verdict"))
                        completed_correct += int(normalized == oracle)
            completed = accounting["completed"]
            summary["judgment_accounting"] = {
                **dict(sorted(accounting.items())),
                "completed_correct": completed_correct,
                "conditional_completed_accuracy": (
                    completed_correct / completed if completed else None
                ),
                "timeout_counted_as_judgment_error": False,
            }
        summaries[method_id] = summary
    result = {
        "evidence_status": DERIVED_STATUS,
        "experiment_id": EXPERIMENT_ID,
        "freeze_commit": commit,
        "source_generation_root_sha256": GENERATION_ROOT,
        "reused_oracle_root_sha256": ORACLE_ROOT,
        "oracle_join_occurred_only_after_both_method_seals": True,
        "methods": summaries,
        "historical_raw_overwritten": False,
        "paper_result_eligible": False,
        "paper_acceptance_required": True,
    }
    _write_once(derived_root / "metric_summary.json", result)
    return result
