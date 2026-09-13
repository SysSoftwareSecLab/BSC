"""Approval-gated Raw→Derived→Paper execution path for EXP-S4-002.

FREEZE_ONLY_NOT_FORMAL_STAGE4_EVIDENCE

All public formal functions require a verified Mac approval before creating an
output root.  This freeze round does not call them.
"""

from __future__ import annotations

import hashlib
import json
from pathlib import Path
from typing import Any, Mapping, Sequence

from . import DERIVED_STATUS, FORMAL_RAW_STATUS, LOCKED_RAW_STATUS
from .admission import check_admission, validate_exact_population
from .gate import FormalGateLocked, assert_phase_preconditions, verify_mac_approval
from .generator import FAMILY_DEFINITIONS, render_formal_candidate
from .identity import compute_asset_identity, compute_toolchain_identity, sha256_file
from .methods import COMMON_BUDGET, execute_method, method_ids
from .metrics import analyze_all_methods
from .oracle import execute_oracle
from .provenance import build_run_manifest
from .records import validate_attempt_record, validate_method_record, validate_oracle_record
from .schedule import exact_schedule
from .seals import build_phase_seal, write_phase_seal


RAW_RELATIVE = "03_experiments/raw/EXP-S4-002_CONTROLLED_UNSEEN_PROGRAM_CORRECTNESS"
DERIVED_RELATIVE = "03_experiments/derived/EXP-S4-002_CONTROLLED_UNSEEN_PROGRAM_CORRECTNESS"
PAPER_RELATIVE = "03_experiments/results/EXP-S4-002_CONTROLLED_UNSEEN_PROGRAM_CORRECTNESS"


def program_ids() -> tuple[str, ...]:
    return tuple(f"EXP-S4-002-P{index:03d}" for index in range(1, 61))


def _write_json(path: Path, value: Any) -> None:
    if path.exists():
        raise FormalGateLocked(f"create-once output already exists: {path}")
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value, indent=2, sort_keys=True, default=str) + "\n", encoding="utf-8")


def _load_json(path: Path) -> Any:
    return json.loads(path.read_text(encoding="utf-8"))


def _approval(approval: Mapping[str, Any], *, root: Path, commit: str) -> None:
    verify_mac_approval(approval, root=root, expected_commit=commit)


def _generation_manifest(root: Path) -> Mapping[str, Any]:
    return _load_json(root / RAW_RELATIVE / "locked_manifest.json")


def validate_locked_manifest(manifest: Mapping[str, Any]) -> None:
    if manifest.get("evidence_status") != LOCKED_RAW_STATUS:
        raise FormalGateLocked("locked manifest evidence marker mismatch")
    if manifest.get("program_ids") != list(program_ids()) or manifest.get("N") != 60:
        raise FormalGateLocked("locked manifest population mismatch")
    if manifest.get("population_audit", {}).get("status") != "pass":
        raise FormalGateLocked("locked manifest contains a rejected scheduled slot")
    attempts = manifest.get("attempts")
    if not isinstance(attempts, list) or len(attempts) != 60:
        raise FormalGateLocked("locked attempt inventory incomplete")
    errors = [error for item in attempts for error in validate_attempt_record(item)]
    if errors:
        raise FormalGateLocked("invalid generation attempts: " + "; ".join(errors[:5]))


def generate_locked(*, root: Path, approval: Mapping[str, Any], commit: str) -> Mapping[str, Any]:
    _approval(approval, root=root, commit=commit)
    raw_root = root / RAW_RELATIVE
    ids = program_ids()
    assert_phase_preconditions(
        "locked_generation", raw_root=raw_root, approval_verified=True, repository_root=root, program_ids=ids
    )
    # Creation starts only after every authorization/identity check above.
    raw_root.mkdir(parents=True)
    canonical_references = _load_json(root / "03_experiments/results/EXP-S4-001_UNSEEN_PROGRAM_CORRECTNESS_PREP/CANONICAL_REFERENCE_SIGNATURES.json")
    fixture_exclusions = _load_json(root / "03_experiments/results/EXP-S4-001_UNSEEN_PROGRAM_CORRECTNESS_PREP/development_fixture/FIXTURE_EXCLUSION_CATALOG.json")
    attempts = []
    sealed_files = []
    for program_id, (family_id, seed) in zip(ids, exact_schedule()):
        candidate = render_formal_candidate(family_id, seed)
        admission = check_admission(
            candidate,
            canonical_references=canonical_references,
            fixture_exclusions=fixture_exclusions,
            prior_attempts=attempts,
        )
        source_relative = f"locked_programs/{program_id}.py"
        source_path = raw_root / source_relative
        source_path.parent.mkdir(parents=True, exist_ok=True)
        source_path.write_text(candidate.source, encoding="utf-8")
        record = {
            "evidence_status": LOCKED_RAW_STATUS,
            "program_id": program_id,
            "family_id": family_id,
            "seed": seed,
            "split": candidate.split,
            "source_path": source_relative,
            "source_sha256": candidate.canonical.source_sha256,
            "canonical_ast_hash": candidate.canonical.canonical_ast_hash,
            "structural_family_signature_hash": candidate.canonical.structural_family_signature_hash,
            "generator_sha256": candidate.generator_sha256,
            "toolchain_root_sha256": candidate.toolchain_identity["root_sha256"],
            "bounds": {"declared_time_bound_ns": candidate.declared_time_bound_ns},
            "status": admission["status"],
            "rejection_reasons": admission["rejection_reasons"],
        }
        record["run_manifest"] = build_run_manifest(
            repository_root=root,
            approval=approval,
            commit=commit,
            program_id=program_id,
            scene_id="bisafecode-controlled-scene-v1",
            method_id="generation",
            seed=seed,
            status=record["status"],
            verdict=None,
            reason_codes=record["rejection_reasons"],
            oracle_status="not_created",
            adjudication_status="not_applicable",
        )
        if validate_attempt_record(record):
            raise FormalGateLocked("generator produced an invalid attempt record")
        attempt_relative = f"attempts/{program_id}.json"
        _write_json(raw_root / attempt_relative, record)
        attempts.append(record)
        sealed_files.extend((source_relative, attempt_relative))
    population = validate_exact_population(attempts)
    manifest = {
        "evidence_status": LOCKED_RAW_STATUS,
        "schema": "bisafecode.stage4.controlled.locked-manifest/v1",
        "N": 60,
        "program_ids": list(ids),
        "schedule": [{"program_id": pid, "family_id": family, "seed": seed} for pid, (family, seed) in zip(ids, exact_schedule())],
        "attempts": attempts,
        "population_audit": population,
        "asset_root_sha256": compute_asset_identity(root)["root_sha256"],
        "toolchain_root_sha256": compute_toolchain_identity(root)["root_sha256"],
        "replacement_or_resampling": False,
    }
    _write_json(raw_root / "locked_manifest.json", manifest)
    sealed_files.append("locked_manifest.json")
    seal = build_phase_seal(
        phase="generation",
        data_root=raw_root,
        file_paths=sealed_files,
        record_count=60,
        program_ids=ids,
        predecessor_roots={},
        repository_root=root,
    )
    write_phase_seal(raw_root / "seals/generation.json", seal)
    if population["status"] != "pass":
        raise FormalGateLocked("exact scheduled generation failed closed; no methods may run")
    return seal


def run_methods(*, root: Path, approval: Mapping[str, Any], commit: str) -> Mapping[str, Any]:
    _approval(approval, root=root, commit=commit)
    raw_root = root / RAW_RELATIVE
    ids = program_ids()
    predecessors = assert_phase_preconditions(
        "methods", raw_root=raw_root, approval_verified=True, repository_root=root, program_ids=ids
    )
    manifest = _generation_manifest(root)
    validate_locked_manifest(manifest)
    results = {}
    for method_id in method_ids():
        method_root = raw_root / "methods" / method_id
        if method_root.exists():
            raise FormalGateLocked(f"method output is create-once: {method_id}")
        sealed_files = []
        records = []
        for item in manifest["attempts"]:
            source = (raw_root / item["source_path"]).read_text(encoding="utf-8")
            stdout_relative = f"methods/{method_id}/{item['program_id']}.stdout.log"
            stderr_relative = f"methods/{method_id}/{item['program_id']}.stderr.log"
            complete = execute_method(
                method_id,
                source,
                program_id=item["program_id"],
                stdout_path=raw_root / stdout_relative,
                stderr_path=raw_root / stderr_relative,
                repository_root=root,
                llm_binding=approval["llm_baseline"] if method_id == "llm_as_judge" else None,
            )
            artifact = complete.pop("artifact")
            complete["source_sha256"] = item["source_sha256"]
            complete["stdout_path"] = stdout_relative
            complete["stderr_path"] = stderr_relative
            complete["run_manifest"] = build_run_manifest(
                repository_root=root,
                approval=approval,
                commit=commit,
                program_id=item["program_id"],
                scene_id="bisafecode-controlled-scene-v1",
                method_id=method_id,
                seed=item["seed"],
                status=complete["status"],
                verdict=complete["verdict"],
                reason_codes=complete["reason_codes"],
                oracle_status="not_created",
                adjudication_status="blind_method_phase",
            )
            record_relative = f"methods/{method_id}/{item['program_id']}.record.json"
            artifact_relative = f"methods/{method_id}/{item['program_id']}.artifact.json"
            _write_json(raw_root / record_relative, complete)
            _write_json(raw_root / artifact_relative, artifact)
            errors = validate_method_record(complete)
            if errors:
                raise FormalGateLocked("method record invalid: " + "; ".join(errors))
            sealed_files.extend((record_relative, artifact_relative, stdout_relative, stderr_relative))
            records.append(complete)
        seal = build_phase_seal(
            phase=method_id,
            data_root=raw_root,
            file_paths=sealed_files,
            record_count=60,
            program_ids=ids,
            predecessor_roots={"generation": predecessors["generation"]["root_sha256"]},
            repository_root=root,
        )
        write_phase_seal(raw_root / f"seals/{method_id}.json", seal)
        results[method_id] = seal
    return results


def run_oracle(*, root: Path, approval: Mapping[str, Any], commit: str) -> Mapping[str, Any]:
    _approval(approval, root=root, commit=commit)
    raw_root = root / RAW_RELATIVE
    ids = program_ids()
    predecessors = assert_phase_preconditions(
        "oracle", raw_root=raw_root, approval_verified=True, repository_root=root, program_ids=ids
    )
    manifest = _generation_manifest(root)
    validate_locked_manifest(manifest)
    sealed_files = []
    for item in manifest["attempts"]:
        program_id = item["program_id"]
        source = (raw_root / item["source_path"]).read_text(encoding="utf-8")
        stdout_relative = f"oracle/{program_id}.stdout.log"
        stderr_relative = f"oracle/{program_id}.stderr.log"
        execution = execute_oracle(
            source,
            program_id=program_id,
            stdout_path=raw_root / stdout_relative,
            stderr_path=raw_root / stderr_relative,
            repository_root=root,
            budget=COMMON_BUDGET,
        )
        evidence_relative = f"oracle/{program_id}.evidence.json"
        traces_relative = f"oracle/{program_id}.traces.json"
        _write_json(raw_root / traces_relative, execution["traces"])
        _write_json(raw_root / evidence_relative, execution["evidence"])
        result = execution["evidence"]
        logical_value = result.get("logical") if isinstance(result, dict) else None
        geometry_value = result.get("geometry_production") if isinstance(result, dict) else None
        logical_hash_value = logical_value if logical_value is not None else result
        geometry_hash_value = geometry_value if geometry_value is not None else result
        record = {
            "evidence_status": FORMAL_RAW_STATUS,
            "program_id": program_id,
            "source_sha256": item["source_sha256"],
            "status": execution["status"],
            "verdict": execution["verdict"],
            "runtime_ms": execution["runtime_ms"],
            "peak_memory_bytes": execution["peak_memory_bytes"],
            "timeout": execution["timeout"],
            "worker_exit_code": execution["worker_exit_code"],
            "budget": dict(COMMON_BUDGET),
            "reason_codes": execution["reason_codes"],
            "logical_evidence_sha256": hashlib.sha256(json.dumps(logical_hash_value, sort_keys=True, separators=(",", ":")).encode("utf-8")).hexdigest(),
            "geometry_evidence_sha256": hashlib.sha256(json.dumps(geometry_hash_value, sort_keys=True, separators=(",", ":")).encode("utf-8")).hexdigest(),
            "asset_root_sha256": compute_asset_identity(root)["root_sha256"],
            "stdout_path": stdout_relative,
            "stderr_path": stderr_relative,
            "stdout_sha256": execution["stdout_sha256"],
            "stderr_sha256": execution["stderr_sha256"],
            "verifier_outputs_visible": False,
        }
        record["run_manifest"] = build_run_manifest(
            repository_root=root,
            approval=approval,
            commit=commit,
            program_id=program_id,
            scene_id="bisafecode-controlled-scene-v1",
            method_id="independent_oracle",
            seed=item["seed"],
            status=record["status"],
            verdict=record["verdict"],
            reason_codes=record["reason_codes"],
            oracle_status=record["status"],
            adjudication_status=("completed" if record["status"] == "ok" else "failed_closed"),
        )
        record_relative = f"oracle/{program_id}.record.json"
        _write_json(raw_root / record_relative, record)
        errors = validate_oracle_record(record)
        if errors:
            raise FormalGateLocked("oracle record invalid: " + "; ".join(errors))
        sealed_files.extend((traces_relative, evidence_relative, record_relative, stdout_relative, stderr_relative))
    oracle_predecessors = {name: seal["root_sha256"] for name, seal in predecessors.items()}
    seal = build_phase_seal(
        phase="oracle",
        data_root=raw_root,
        file_paths=sealed_files,
        record_count=60,
        program_ids=ids,
        predecessor_roots=oracle_predecessors,
        repository_root=root,
    )
    write_phase_seal(raw_root / "seals/oracle.json", seal)
    return seal


def derive(*, root: Path, approval: Mapping[str, Any], commit: str) -> Mapping[str, Any]:
    _approval(approval, root=root, commit=commit)
    raw_root = root / RAW_RELATIVE
    ids = program_ids()
    predecessors = assert_phase_preconditions(
        "derived", raw_root=raw_root, approval_verified=True, repository_root=root, program_ids=ids
    )
    manifest = _generation_manifest(root)
    validate_locked_manifest(manifest)
    labels = {}
    oracle_outcomes = {}
    for program_id in ids:
        record = _load_json(raw_root / f"oracle/{program_id}.record.json")
        if record["status"] == "ok" and record["verdict"] in {"safe", "unsafe"}:
            labels[program_id] = record["verdict"]
        if record["status"] == "ok" and record["verdict"] in {"safe", "unsafe", "unknown", "invalid"}:
            oracle_outcomes[program_id] = record["verdict"]
    records_by_method = {
        method_id: [_load_json(raw_root / f"methods/{method_id}/{program_id}.record.json") for program_id in ids]
        for method_id in method_ids()
    }
    family_by_program = {
        str(item["program_id"]): str(item["family_id"])
        for item in manifest["attempts"]
    }
    stratum_by_program = {
        program_id: str(FAMILY_DEFINITIONS[family_id]["scientific_stratum"])
        for program_id, family_id in family_by_program.items()
    }
    summary = analyze_all_methods(
        admitted_program_ids=ids,
        oracle_labels=labels,
        records_by_method=records_by_method,
        oracle_outcomes=oracle_outcomes,
        generator_family_by_program=family_by_program,
        property_stratum_by_program=stratum_by_program,
    )
    summary["evidence_status"] = DERIVED_STATUS
    summary["run_manifest"] = build_run_manifest(
        repository_root=root,
        approval=approval,
        commit=commit,
        program_id="ALL_60",
        scene_id="bisafecode-controlled-scene-v1",
        method_id="derive",
        seed=None,
        status="ok",
        verdict=None,
        reason_codes=[],
        oracle_status="sealed",
        adjudication_status="pending_mac_review",
    )
    derived_root = root / DERIVED_RELATIVE
    if derived_root.exists():
        raise FormalGateLocked("Derived root is create-once")
    _write_json(derived_root / "metric_summary.json", summary)
    predecessor_roots = {name: seal["root_sha256"] for name, seal in predecessors.items()}
    seal = build_phase_seal(
        phase="derived",
        data_root=derived_root,
        file_paths=("metric_summary.json",),
        record_count=60,
        program_ids=ids,
        predecessor_roots=predecessor_roots,
        repository_root=root,
    )
    write_phase_seal(derived_root / "derived.seal.json", seal)
    return seal


def paper_export(*, root: Path, approval: Mapping[str, Any], commit: str) -> Mapping[str, Any]:
    """Remain fail-closed until atomic authority finalization is implemented."""

    del root, approval, commit
    raise FormalGateLocked(
        "PAPER_EXPORT_NOT_IMPLEMENTED_ATOMIC_AUTHORITY_FINALIZATION_REQUIRED"
    )
