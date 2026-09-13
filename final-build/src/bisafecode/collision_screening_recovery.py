"""Fail-closed recovery state for frozen C1--C4 candidate screening.

The module never invokes FCL.  It validates immutable candidate attempts,
enforces the preregistered case/rank order, and builds the no-FCL final report.
"""

from __future__ import annotations

from dataclasses import dataclass
import hashlib
import json
import os
from pathlib import Path, PurePosixPath
import subprocess
from typing import Any, Mapping


PROTOCOL_SCHEMA = "bisafecode.collision-screening-recovery-protocol/v0.1"
PROTOCOL_STATUS = "FROZEN_AFTER_C4_ENDPOINT_GATE_BEFORE_FULL_SCREENING"
RUN_IDENTITY_SCHEMA = "bisafecode.collision-screening-recovery-run/v0.1"
ATTEMPT_RESULT_SCHEMA = "bisafecode.collision-screening-candidate-attempt/v0.1"
ATTEMPT_MANIFEST_SCHEMA = "bisafecode.collision-screening-attempt-manifest/v0.1"
ACK_SCHEMA = "bisafecode.collision-screening-infrastructure-ack/v0.1"
FINAL_REPORT_SCHEMA = "bisafecode.collision-candidate-screening/v0.4"
TERMINAL_SCIENTIFIC = {"ACCEPTED", "REJECTED_BY_FROZEN_PREDICATE"}
RETRYABLE = {"FAIL_INFRASTRUCTURE_TIMEOUT"}
TERMINAL_FAILURE = {"FAIL_PROBE_NONZERO", "FAIL_OUTPUT_OR_EVALUATION"}


def sha256_path(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def canonical_json_bytes(record: Any) -> bytes:
    return (
        json.dumps(record, sort_keys=True, separators=(",", ":"), ensure_ascii=True, allow_nan=False)
        + "\n"
    ).encode("ascii")


def write_exclusive(path: Path, payload: bytes) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    descriptor = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o644)
    with os.fdopen(descriptor, "wb") as stream:
        stream.write(payload)


def _require_sha256(value: Any, field: str) -> str:
    if not isinstance(value, str) or len(value) != 64 or any(c not in "0123456789abcdef" for c in value):
        raise ValueError(f"{field} must be a lowercase SHA-256 digest")
    return value


def _require_regular(path: Path, field: str) -> Path:
    if path.is_symlink() or not path.is_file():
        raise ValueError(f"{field} must be a regular non-symlink file")
    return path


def _repository_path(root: Path, text: Any, field: str) -> Path:
    if not isinstance(text, str) or not text:
        raise ValueError(f"{field} path must be non-empty")
    relative = PurePosixPath(text)
    if relative.is_absolute() or "." in relative.parts or ".." in relative.parts:
        raise ValueError(f"{field} must stay within the repository")
    path = _require_regular(root / Path(*relative.parts), field)
    try:
        path.resolve().relative_to(root.resolve())
    except ValueError as error:
        raise ValueError(f"{field} resolves outside the repository") from error
    return path


@dataclass(frozen=True)
class ScreeningRecoveryProtocol:
    record: Mapping[str, Any]
    file_sha256: str

    @property
    def case_order(self) -> tuple[str, ...]:
        return tuple(self.record["scientific_protocol_unchanged"]["case_order"])

    @property
    def maximum_attempts(self) -> int:
        return int(self.record["execution"]["maximum_attempts_per_candidate"])


def load_recovery_protocol(path: Path, repository_root: Path) -> ScreeningRecoveryProtocol:
    _require_regular(path, "recovery protocol")
    record = json.loads(path.read_text(encoding="utf-8"))
    required = {
        "schema", "status", "experiment_id", "purpose", "paper_result_eligible",
        "scientific_protocol_unchanged", "repository_inputs", "external_inputs",
        "execution", "evidence_boundary", "prohibited_operations_during_preflight",
    }
    if not isinstance(record, Mapping) or set(record) != required:
        raise ValueError("recovery protocol root keys differ from the frozen schema")
    if record["schema"] != PROTOCOL_SCHEMA or record["status"] != PROTOCOL_STATUS:
        raise ValueError("unsupported or already-executed recovery protocol")
    if record["experiment_id"] != "EXP-S2-032" or record["paper_result_eligible"] is not False:
        raise ValueError("recovery protocol identity or evidence boundary differs")
    unchanged = record["scientific_protocol_unchanged"]
    if set(unchanged) != {
        "candidate_definitions", "candidate_order", "case_order", "thresholds", "pair_universes",
        "attachment_transform", "c4_terminal_support_contact_amendment",
        "first_accepted_candidate_selection", "independent_oracle_label_authority",
    }:
        raise ValueError("scientific-protocol invariants differ from the frozen schema")
    if unchanged["case_order"] != ["C1", "C2", "C3", "C4"]:
        raise ValueError("case order changed")
    if any(unchanged[key] is not True for key in unchanged if key != "case_order"):
        raise ValueError("recovery protocol changes a scientific selection invariant")
    execution = record["execution"]
    expected_execution = {
        "one_candidate_per_runner_process": True,
        "maximum_attempts_per_candidate": 2,
        "attempt_directories_are_immutable": True,
        "retry_requires_explicit_acknowledgement": True,
        "retryable_prior_outcomes": ["NO_RESULT_EXTERNAL_INTERRUPTION", "FAIL_INFRASTRUCTURE_TIMEOUT"],
        "completed_probe_nonzero_is_retryable": False,
        "completed_output_or_evaluation_failure_is_retryable": False,
        "later_candidate_forbidden_after_first_acceptance": True,
        "out_of_order_candidate_execution_forbidden": True,
        "no_fcl_finalizer": True,
        "incomplete_run_cannot_be_finalized": True,
        "no_match_result": "FAIL_NO_MATCH_WITH_THRESHOLDS_UNCHANGED",
    }
    if execution != expected_execution:
        raise ValueError("recovery execution policy differs from the frozen contract")
    boundary = record["evidence_boundary"]
    expected_boundary = {
        "screening_backend_role": "candidate-construction-only",
        "screening_is_continuous_certificate": False,
        "candidate_selection_is_oracle_label": False,
        "independent_oracle_executed": False,
        "complete_bmc_result": False,
        "robot_hardware_executed": False,
        "paper_result_eligible": False,
    }
    if boundary != expected_boundary:
        raise ValueError("recovery protocol broadens the evidence boundary")
    repository_inputs = record["repository_inputs"]
    if not isinstance(repository_inputs, Mapping) or not repository_inputs:
        raise ValueError("repository input identities are required")
    for name, identity in repository_inputs.items():
        if not isinstance(identity, Mapping) or set(identity) != {"path", "sha256", "size_bytes"}:
            raise ValueError(f"repository input identity is incomplete: {name}")
        source = _repository_path(repository_root, identity["path"], name)
        if source.stat().st_size != identity["size_bytes"] or sha256_path(source) != _require_sha256(identity["sha256"], name):
            raise ValueError(f"repository input identity mismatch: {name}")
    external = record["external_inputs"]
    expected_external = {
        "candidate_batch_manifest_sha256", "probe_binary_sha256",
        "resolved_urdf_sha256", "resolved_urdf_canonical_suffix",
        "active_srdf_sha256", "active_srdf_canonical_suffix",
    }
    if not isinstance(external, Mapping) or set(external) != expected_external:
        raise ValueError("external input identities differ from the frozen schema")
    for name in (
        "candidate_batch_manifest_sha256", "probe_binary_sha256",
        "resolved_urdf_sha256", "active_srdf_sha256",
    ):
        _require_sha256(external[name], name)
    expected_suffixes = {
        "resolved_urdf_canonical_suffix": "gate_a_probe/outputs/openarm_v1_bimanual_resolved.urdf",
        "active_srdf_canonical_suffix": "gate_a_probe/gate_a2_acm/active_srdf_copy.srdf",
    }
    if any(external[name] != value for name, value in expected_suffixes.items()):
        raise ValueError("external input canonical source differs from the frozen provenance")
    return ScreeningRecoveryProtocol(record=record, file_sha256=sha256_path(path))


def file_identity(path: Path) -> dict[str, Any]:
    _require_regular(path, str(path))
    return {"path": str(path.resolve()), "sha256": sha256_path(path), "size_bytes": path.stat().st_size}


def validate_canonical_external_file(
    path: Path,
    *,
    expected_sha256: str,
    canonical_suffix: str,
    field: str,
) -> Path:
    """Bind a content identity to its frozen provenance path without requiring global path uniqueness."""
    source = _require_regular(path, field)
    suffix = PurePosixPath(canonical_suffix)
    if suffix.is_absolute() or not suffix.parts or "." in suffix.parts or ".." in suffix.parts:
        raise ValueError(f"{field} canonical suffix is invalid")
    resolved_parts = source.resolve().parts
    if len(resolved_parts) < len(suffix.parts) or tuple(resolved_parts[-len(suffix.parts):]) != suffix.parts:
        raise ValueError(f"{field} is a byte-identical copy but not the frozen canonical source")
    if sha256_path(source) != _require_sha256(expected_sha256, field):
        raise ValueError(f"{field} hash differs from the frozen canonical source")
    return source


def validate_file_identity(record: Mapping[str, Any], field: str) -> Path:
    if not isinstance(record, Mapping) or set(record) != {"path", "sha256", "size_bytes"}:
        raise ValueError(f"{field} identity keys differ")
    path = _require_regular(Path(record["path"]), field)
    if path.stat().st_size != record["size_bytes"] or sha256_path(path) != _require_sha256(record["sha256"], field):
        raise ValueError(f"{field} identity mismatch")
    return path


def load_run_identity(output_root: Path, protocol: ScreeningRecoveryProtocol) -> Mapping[str, Any]:
    path = _require_regular(output_root / "run_identity.json", "run identity")
    record = json.loads(path.read_text(encoding="utf-8"))
    required = {
        "schema", "experiment_id", "protocol_file_sha256", "repository_head", "repository_clean",
        "batch_dir", "batch_manifest", "probe_binary", "urdf", "srdf", "world",
        "empty_attachments", "inventory", "carried_attachment", "case_order",
        "paper_result_eligible", "prohibited_operations_executed",
    }
    if not isinstance(record, Mapping) or set(record) != required:
        raise ValueError("run identity keys differ from the frozen schema")
    if record["schema"] != RUN_IDENTITY_SCHEMA or record["experiment_id"] != "EXP-S2-032":
        raise ValueError("run identity schema or experiment differs")
    if record["protocol_file_sha256"] != protocol.file_sha256 or record["case_order"] != list(protocol.case_order):
        raise ValueError("run identity targets a different recovery protocol")
    if record["repository_clean"] is not True or record["paper_result_eligible"] is not False:
        raise ValueError("run identity does not preserve the clean engineering boundary")
    if record["prohibited_operations_executed"] is not False:
        raise ValueError("run identity reports prohibited operations")
    for field in ("batch_manifest", "probe_binary", "urdf", "srdf", "world", "empty_attachments", "inventory", "carried_attachment"):
        validate_file_identity(record[field], field)
    batch_dir = Path(record["batch_dir"])
    if batch_dir.is_symlink() or not batch_dir.is_dir():
        raise ValueError("candidate batch directory is missing")
    return record


def validate_repository_state(repository_root: Path, identity: Mapping[str, Any]) -> None:
    head = subprocess.run(
        ["git", "-C", str(repository_root), "rev-parse", "HEAD"],
        check=True, capture_output=True, text=True,
    ).stdout.strip()
    status = subprocess.run(
        ["git", "-C", str(repository_root), "status", "--porcelain"],
        check=True, capture_output=True, text=True,
    ).stdout.strip()
    if head != identity["repository_head"]:
        raise ValueError("repository HEAD changed after screening preparation")
    if status:
        raise ValueError("repository worktree changed after screening preparation")


def _attempt_directories(output_root: Path, case_id: str, candidate_id: str) -> list[Path]:
    root = output_root / "attempts" / case_id / candidate_id
    if not root.exists():
        return []
    if root.is_symlink() or not root.is_dir():
        raise ValueError(f"candidate attempt root is not a regular directory: {candidate_id}")
    attempts = sorted(root.iterdir())
    if any(path.is_symlink() or not path.is_dir() for path in attempts):
        raise ValueError(f"candidate attempt root contains a non-directory: {candidate_id}")
    names = [path.name for path in attempts]
    if names not in ([], ["attempt_001"], ["attempt_001", "attempt_002"]):
        raise ValueError(f"candidate attempt sequence violates the maximum-two policy: {candidate_id}")
    return attempts


def build_attempt_evidence_manifest(attempt_root: Path) -> dict[str, Any]:
    excluded = {"attempt_result.json", "attempt_evidence_manifest.json", "infrastructure_interruption_acknowledgement.json"}
    files = []
    for path in sorted(attempt_root.rglob("*")):
        if path.is_symlink():
            raise ValueError("attempt evidence may not contain symlinks")
        if path.is_dir():
            continue
        relative = path.relative_to(attempt_root).as_posix()
        if relative in excluded:
            continue
        files.append({"path": relative, "sha256": sha256_path(path), "size_bytes": path.stat().st_size})
    return {"schema": ATTEMPT_MANIFEST_SCHEMA, "file_count": len(files), "files": files}


def _validate_attempt_manifest(attempt_root: Path, identity: Mapping[str, Any]) -> None:
    if not isinstance(identity, Mapping) or set(identity) != {"path", "sha256", "size_bytes"}:
        raise ValueError("attempt evidence-manifest identity differs")
    if identity["path"] != "attempt_evidence_manifest.json":
        raise ValueError("attempt evidence-manifest path differs")
    path = _require_regular(attempt_root / identity["path"], "attempt evidence manifest")
    if path.stat().st_size != identity["size_bytes"] or sha256_path(path) != identity["sha256"]:
        raise ValueError("attempt evidence-manifest identity mismatch")
    manifest = json.loads(path.read_text(encoding="utf-8"))
    recomputed = build_attempt_evidence_manifest(attempt_root)
    if manifest != recomputed:
        raise ValueError("attempt evidence manifest does not close the preserved files")


def _load_attempt_result(attempt_root: Path, candidate: Mapping[str, Any]) -> Mapping[str, Any] | None:
    path = attempt_root / "attempt_result.json"
    if not path.exists():
        return None
    _require_regular(path, "attempt result")
    record = json.loads(path.read_text(encoding="utf-8"))
    required = {
        "schema", "case_id", "candidate_id", "rank", "attempt", "status", "reason",
        "retryable_under_frozen_policy", "paper_result_eligible", "evaluation", "runs",
        "outputs", "evidence_manifest",
    }
    if not isinstance(record, Mapping) or set(record) != required:
        raise ValueError("attempt-result keys differ from the frozen schema")
    attempt_number = int(attempt_root.name.removeprefix("attempt_"))
    if (
        record["schema"] != ATTEMPT_RESULT_SCHEMA
        or record["case_id"] != candidate["case_id"]
        or record["candidate_id"] != candidate["candidate_id"]
        or record["rank"] != candidate["rank"]
        or record["attempt"] != attempt_number
        or record["paper_result_eligible"] is not False
    ):
        raise ValueError("attempt-result identity differs from the frozen candidate")
    status = record["status"]
    if status not in TERMINAL_SCIENTIFIC | RETRYABLE | TERMINAL_FAILURE:
        raise ValueError("attempt-result status is unsupported")
    expected_retryable = status in RETRYABLE
    if record["retryable_under_frozen_policy"] is not expected_retryable:
        raise ValueError("attempt-result retryability is inconsistent")
    if not isinstance(record["runs"], Mapping) or not isinstance(record["outputs"], Mapping):
        raise ValueError("attempt-result runs and outputs must be mappings")
    if status in TERMINAL_SCIENTIFIC:
        if record["reason"] is not None:
            raise ValueError("scientific attempt may not carry a failure reason")
        if not isinstance(record["evaluation"], Mapping) or record["evaluation"].get("accepted") is not (status == "ACCEPTED"):
            raise ValueError("scientific attempt status disagrees with the evaluation")
        evaluation_path = _require_regular(attempt_root / "evaluation.json", "candidate evaluation")
        if json.loads(evaluation_path.read_text(encoding="utf-8")) != record["evaluation"]:
            raise ValueError("attempt-result evaluation differs from the preserved evaluation")
        expected_scopes = {"empty", "carried"} if candidate["case_id"] == "C4" else {
            "carried" if candidate["case_id"] == "C2" else "empty"
        }
        if set(record["runs"]) != expected_scopes or set(record["outputs"]) != expected_scopes:
            raise ValueError("scientific attempt scope coverage differs from the frozen case")
    elif record["evaluation"] is not None:
        raise ValueError("failed infrastructure/probe attempt may not contain an evaluation")
    elif not isinstance(record["reason"], str) or not record["reason"].strip():
        raise ValueError("failed attempt must preserve a non-empty reason")
    _validate_attempt_manifest(attempt_root, record["evidence_manifest"])
    return record


def _ack_file_closure(attempt_root: Path) -> list[dict[str, Any]]:
    files = []
    for path in sorted(attempt_root.rglob("*")):
        if path.is_symlink():
            raise ValueError("interrupted attempt may not contain symlinks")
        if path.is_dir() or path.name == "infrastructure_interruption_acknowledgement.json":
            continue
        files.append({
            "path": path.relative_to(attempt_root).as_posix(),
            "sha256": sha256_path(path),
            "size_bytes": path.stat().st_size,
        })
    return files


def _load_ack(attempt_root: Path, candidate: Mapping[str, Any], prior_result: Mapping[str, Any] | None) -> Mapping[str, Any] | None:
    path = attempt_root / "infrastructure_interruption_acknowledgement.json"
    if not path.exists():
        return None
    _require_regular(path, "interruption acknowledgement")
    record = json.loads(path.read_text(encoding="utf-8"))
    required = {
        "schema", "case_id", "candidate_id", "attempt", "prior_outcome", "authorized_by",
        "observed_at_utc", "no_live_process_confirmed", "partial_evidence",
    }
    if not isinstance(record, Mapping) or set(record) != required:
        raise ValueError("interruption acknowledgement keys differ")
    prior_outcome = "NO_RESULT_EXTERNAL_INTERRUPTION" if prior_result is None else prior_result["status"]
    if (
        record["schema"] != ACK_SCHEMA
        or record["case_id"] != candidate["case_id"]
        or record["candidate_id"] != candidate["candidate_id"]
        or record["attempt"] != int(attempt_root.name.removeprefix("attempt_"))
        or record["prior_outcome"] != prior_outcome
        or prior_outcome not in {"NO_RESULT_EXTERNAL_INTERRUPTION", "FAIL_INFRASTRUCTURE_TIMEOUT"}
        or record["no_live_process_confirmed"] is not True
        or not isinstance(record["authorized_by"], str) or not record["authorized_by"].strip()
        or not isinstance(record["observed_at_utc"], str) or not record["observed_at_utc"].strip()
    ):
        raise ValueError("interruption acknowledgement does not authorize the frozen retry")
    if record["partial_evidence"] != _ack_file_closure(attempt_root):
        raise ValueError("interruption acknowledgement does not close the preserved partial evidence")
    return record


def write_interruption_acknowledgement(
    attempt_root: Path,
    candidate: Mapping[str, Any],
    *,
    authorized_by: str,
    observed_at_utc: str,
) -> Mapping[str, Any]:
    if attempt_root.name != "attempt_001":
        raise ValueError("only attempt 1 can authorize the one permitted retry")
    if (attempt_root.parent / "attempt_002").exists():
        raise ValueError("attempt 2 already exists")
    result = _load_attempt_result(attempt_root, candidate)
    if result is not None and result["status"] != "FAIL_INFRASTRUCTURE_TIMEOUT":
        raise ValueError("completed non-timeout attempts cannot be retried")
    record = {
        "schema": ACK_SCHEMA,
        "case_id": candidate["case_id"],
        "candidate_id": candidate["candidate_id"],
        "attempt": 1,
        "prior_outcome": "NO_RESULT_EXTERNAL_INTERRUPTION" if result is None else result["status"],
        "authorized_by": authorized_by,
        "observed_at_utc": observed_at_utc,
        "no_live_process_confirmed": True,
        "partial_evidence": _ack_file_closure(attempt_root),
    }
    write_exclusive(attempt_root / "infrastructure_interruption_acknowledgement.json", canonical_json_bytes(record))
    return record


def _candidate_order(manifest: Mapping[str, Any], case_order: tuple[str, ...]) -> dict[str, list[Mapping[str, Any]]]:
    candidates = manifest.get("candidates")
    if not isinstance(candidates, list):
        raise ValueError("candidate manifest lacks candidates")
    result = {}
    seen = set()
    for case_id in case_order:
        values = sorted((item for item in candidates if item.get("case_id") == case_id), key=lambda item: item.get("rank"))
        if not values or [item.get("rank") for item in values] != list(range(len(values))):
            raise ValueError(f"candidate ranks are not contiguous for {case_id}")
        for item in values:
            candidate_id = item.get("candidate_id")
            if not isinstance(candidate_id, str) or candidate_id in seen:
                raise ValueError("candidate identities are missing or duplicated")
            seen.add(candidate_id)
            if item.get("structural_status") not in {"READY", "REJECTED_PRE_SCREEN"}:
                raise ValueError("candidate structural status is unsupported")
        result[case_id] = values
    if len(seen) != len(candidates):
        raise ValueError("candidate manifest contains an unknown case")
    return result


def inspect_screening_progress(
    manifest: Mapping[str, Any],
    output_root: Path,
    protocol: ScreeningRecoveryProtocol,
) -> dict[str, Any]:
    load_run_identity(output_root, protocol)
    order = _candidate_order(manifest, protocol.case_order)
    all_attempts = {
        item["candidate_id"]: _attempt_directories(output_root, case_id, item["candidate_id"])
        for case_id in protocol.case_order for item in order[case_id]
    }
    attempts_root = output_root / "attempts"
    if attempts_root.exists():
        if attempts_root.is_symlink() or not attempts_root.is_dir():
            raise ValueError("attempts root is not a regular directory")
        expected_paths = {
            path.resolve()
            for paths in all_attempts.values()
            for path in paths
        }
        observed_paths = set()
        expected_cases = set(protocol.case_order)
        expected_candidates = {
            case_id: {item["candidate_id"] for item in order[case_id]}
            for case_id in protocol.case_order
        }
        for case_root in attempts_root.iterdir():
            if case_root.is_symlink() or not case_root.is_dir() or case_root.name not in expected_cases:
                raise ValueError("attempts root contains an unknown or malformed case directory")
            for candidate_root in case_root.iterdir():
                if (
                    candidate_root.is_symlink()
                    or not candidate_root.is_dir()
                    or candidate_root.name not in expected_candidates[case_root.name]
                ):
                    raise ValueError("attempts root contains an unknown or malformed candidate attempt")
                for attempt_root in candidate_root.iterdir():
                    if (
                        attempt_root.is_symlink()
                        or not attempt_root.is_dir()
                        or attempt_root.name not in {"attempt_001", "attempt_002"}
                    ):
                        raise ValueError("attempts root contains an unknown or malformed candidate attempt")
                    observed_paths.add(attempt_root.resolve())
        if observed_paths != expected_paths:
            raise ValueError("attempts root contains an unknown or malformed candidate attempt")

    def forbid(candidates: list[Mapping[str, Any]], reason: str) -> None:
        offenders = [item["candidate_id"] for item in candidates if all_attempts[item["candidate_id"]]]
        if offenders:
            raise ValueError(f"out-of-order candidate attempts after {reason}: {offenders}")

    selected: dict[str, str | None] = {}
    completed_attempts = []
    for case_index, case_id in enumerate(protocol.case_order):
        candidates = order[case_id]
        selected_id = None
        for index, candidate in enumerate(candidates):
            attempts = all_attempts[candidate["candidate_id"]]
            if candidate["structural_status"] != "READY":
                if attempts:
                    raise ValueError("pre-screen-rejected candidate may not have attempts")
                continue
            if selected_id is not None:
                if attempts:
                    raise ValueError("later candidate executed after first acceptance")
                continue
            if not attempts:
                remaining = candidates[index + 1:] + [item for later in protocol.case_order[case_index + 1:] for item in order[later]]
                forbid(remaining, f"next work item {candidate['candidate_id']}")
                return {
                    "status": "NEXT_WORK_ITEM",
                    "case_id": case_id,
                    "candidate_id": candidate["candidate_id"],
                    "rank": candidate["rank"],
                    "attempt": 1,
                    "selected_candidates": selected,
                    "completed_attempt_count": len(completed_attempts),
                }
            first = _load_attempt_result(attempts[0], candidate)
            first_ack = _load_ack(attempts[0], candidate, first)
            if len(attempts) == 2 and first_ack is None:
                raise ValueError("attempt 2 lacks a valid explicit interruption acknowledgement")
            active_result = first
            active_attempt = 1
            if len(attempts) == 2:
                if first is not None and first["status"] not in RETRYABLE:
                    raise ValueError("attempt 2 follows a non-retryable completed outcome")
                active_result = _load_attempt_result(attempts[1], candidate)
                if _load_ack(attempts[1], candidate, active_result) is not None:
                    raise ValueError("attempt 2 cannot authorize a third attempt")
                active_attempt = 2
            if active_result is None:
                remaining = candidates[index + 1:] + [item for later in protocol.case_order[case_index + 1:] for item in order[later]]
                forbid(remaining, f"incomplete {candidate['candidate_id']}")
                if active_attempt == 1 and first_ack is not None:
                    return {
                        "status": "NEXT_WORK_ITEM", "case_id": case_id,
                        "candidate_id": candidate["candidate_id"], "rank": candidate["rank"],
                        "attempt": 2, "selected_candidates": selected,
                        "completed_attempt_count": len(completed_attempts),
                    }
                return {
                    "status": "WAITING_INTERRUPTION_ACK" if active_attempt == 1 else "BLOCKED_RETRY_EXHAUSTED",
                    "case_id": case_id, "candidate_id": candidate["candidate_id"],
                    "rank": candidate["rank"], "attempt": active_attempt,
                    "selected_candidates": selected, "completed_attempt_count": len(completed_attempts),
                }
            completed_attempts.append({
                "case_id": case_id,
                "candidate_id": candidate["candidate_id"],
                "rank": candidate["rank"],
                "attempt": active_attempt,
                "status": active_result["status"],
                "result_path": str((attempts[active_attempt - 1] / "attempt_result.json").resolve()),
                "result_sha256": sha256_path(attempts[active_attempt - 1] / "attempt_result.json"),
            })
            if active_result["status"] == "ACCEPTED":
                selected_id = candidate["candidate_id"]
                forbid(candidates[index + 1:], f"accepted {selected_id}")
            elif active_result["status"] == "REJECTED_BY_FROZEN_PREDICATE":
                continue
            elif active_result["status"] == "FAIL_INFRASTRUCTURE_TIMEOUT":
                remaining = candidates[index + 1:] + [item for later in protocol.case_order[case_index + 1:] for item in order[later]]
                forbid(remaining, f"timeout {candidate['candidate_id']}")
                if active_attempt == 1 and first_ack is not None:
                    return {
                        "status": "NEXT_WORK_ITEM", "case_id": case_id,
                        "candidate_id": candidate["candidate_id"], "rank": candidate["rank"],
                        "attempt": 2, "selected_candidates": selected,
                        "completed_attempt_count": len(completed_attempts),
                    }
                return {
                    "status": "WAITING_INTERRUPTION_ACK" if active_attempt == 1 else "BLOCKED_RETRY_EXHAUSTED",
                    "case_id": case_id, "candidate_id": candidate["candidate_id"],
                    "rank": candidate["rank"], "attempt": active_attempt,
                    "selected_candidates": selected, "completed_attempt_count": len(completed_attempts),
                }
            else:
                remaining = candidates[index + 1:] + [item for later in protocol.case_order[case_index + 1:] for item in order[later]]
                forbid(remaining, f"terminal failure {candidate['candidate_id']}")
                return {
                    "status": "BLOCKED_TERMINAL_FAILURE", "case_id": case_id,
                    "candidate_id": candidate["candidate_id"], "rank": candidate["rank"],
                    "attempt": active_attempt, "failure_status": active_result["status"],
                    "selected_candidates": selected, "completed_attempt_count": len(completed_attempts),
                }
        selected[case_id] = selected_id
    return {
        "status": "COMPLETE",
        "selected_candidates": selected,
        "completed_attempt_count": len(completed_attempts),
        "completed_attempts": completed_attempts,
    }


def build_final_report(
    manifest: Mapping[str, Any],
    output_root: Path,
    protocol: ScreeningRecoveryProtocol,
) -> dict[str, Any]:
    progress = inspect_screening_progress(manifest, output_root, protocol)
    if progress["status"] != "COMPLETE":
        raise ValueError(f"cannot finalize incomplete screening: {progress['status']}")
    order = _candidate_order(manifest, protocol.case_order)
    selected = progress["selected_candidates"]
    candidate_results = []
    total_attempts = 0
    infrastructure_retries = 0
    for case_id in protocol.case_order:
        selected_id = selected[case_id]
        selected_rank = next((item["rank"] for item in order[case_id] if item["candidate_id"] == selected_id), None)
        for candidate in order[case_id]:
            attempts = _attempt_directories(output_root, case_id, candidate["candidate_id"])
            if candidate["structural_status"] != "READY":
                candidate_results.append({
                    "case_id": case_id, "candidate_id": candidate["candidate_id"], "rank": candidate["rank"],
                    "status": "REJECTED_PRE_SCREEN", "reasons": candidate.get("structural_reasons", []),
                })
                continue
            if selected_rank is not None and candidate["rank"] > selected_rank:
                candidate_results.append({
                    "case_id": case_id, "candidate_id": candidate["candidate_id"], "rank": candidate["rank"],
                    "status": "NOT_RUN_AFTER_FIRST_MATCH",
                })
                continue
            total_attempts += len(attempts)
            infrastructure_retries += max(0, len(attempts) - 1)
            final_attempt = attempts[-1]
            result = _load_attempt_result(final_attempt, candidate)
            if result is None or result["status"] not in TERMINAL_SCIENTIFIC:
                raise ValueError("complete screening contains a non-scientific terminal attempt")
            candidate_results.append({
                "case_id": case_id, "candidate_id": candidate["candidate_id"], "rank": candidate["rank"],
                "status": result["status"], "attempt": result["attempt"],
                "attempt_result_sha256": sha256_path(final_attempt / "attempt_result.json"),
                "evaluation": result["evaluation"], "runs": result["runs"], "outputs": result["outputs"],
            })
    overall = "PASS" if all(selected.values()) else "FAIL_NO_MATCH_WITH_THRESHOLDS_UNCHANGED"
    identity = load_run_identity(output_root, protocol)
    return {
        "schema": FINAL_REPORT_SCHEMA,
        "status": overall,
        "paper_result_eligible": False,
        "screening_backend_role": "candidate-construction-only",
        "screening_is_continuous_certificate": False,
        "independent_oracle_executed": False,
        "protocol_file_sha256": protocol.file_sha256,
        "batch_manifest_sha256": identity["batch_manifest"]["sha256"],
        "selected_candidates": selected,
        "total_probe_attempts": total_attempts,
        "infrastructure_retry_count": infrastructure_retries,
        "candidate_results": candidate_results,
        "prohibited_operations_executed": False,
    }
