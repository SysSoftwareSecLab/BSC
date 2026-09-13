"""Frozen, C4-isolated contract for the EXP-S2-033 C3-v2 FCL batch.

The batch is a candidate-construction screen.  It does not provide an
independent oracle label, a continuous collision certificate, or a paper
performance result.
"""

from __future__ import annotations

import json
import os
from pathlib import Path, PurePosixPath
from typing import Any, Dict, Mapping, Optional, Sequence

from .c3_v2_prefilter import generate_c3_v2_candidates, load_c3_v2_spec
from .collision_candidate_generation import (
    canonical_json_bytes,
    load_generation_inputs,
    sha256_path,
    state_tsv_bytes,
)
from .collision_candidate_screening import expected_pair_universe, expected_scenarios


BATCH_SCHEMA = "bisafecode.c3-v2-prefilter-batch/v0.2"
BATCH_STATUS = "PREPARED_FK_PROXY_ONLY_NOT_FCL_SCREENED"
RUN_SCHEMA = "bisafecode.c3-v2-fcl-batch-run/v0.1"
RESULT_SCHEMA = "bisafecode.c3-v2-fcl-batch-result/v0.1"


def _require_regular(path: Path, label: str) -> Path:
    if path.is_symlink() or not path.is_file():
        raise ValueError(f"{label} must be an existing regular non-symlink file")
    return path


def _repository_path(repo_root: Path, relative_text: Any, label: str) -> Path:
    if not isinstance(relative_text, str) or not relative_text:
        raise ValueError(f"{label} path must be a non-empty string")
    relative = PurePosixPath(relative_text)
    if relative.is_absolute() or "." in relative.parts or ".." in relative.parts:
        raise ValueError(f"{label} path must stay inside the repository")
    path = _require_regular(repo_root / Path(*relative.parts), label)
    try:
        path.resolve().relative_to(repo_root.resolve())
    except ValueError as error:
        raise ValueError(f"{label} resolves outside the repository") from error
    return path


def _batch_path(batch_dir: Path, relative_text: Any, label: str) -> Path:
    if not isinstance(relative_text, str) or not relative_text:
        raise ValueError(f"{label} path must be a non-empty string")
    relative = PurePosixPath(relative_text)
    if relative.is_absolute() or "." in relative.parts or ".." in relative.parts:
        raise ValueError(f"{label} path must stay inside the batch")
    path = _require_regular(batch_dir / Path(*relative.parts), label)
    try:
        path.resolve().relative_to(batch_dir.resolve())
    except ValueError as error:
        raise ValueError(f"{label} resolves outside the batch") from error
    return path


def file_identity(path: Path, *, relative_to: Optional[Path] = None) -> Dict[str, Any]:
    path = _require_regular(path, "identity input")
    display = path.resolve().relative_to(relative_to.resolve()).as_posix() if relative_to else str(path.resolve())
    return {"path": display, "size_bytes": path.stat().st_size, "sha256": sha256_path(path)}


def _validate_identity(path: Path, record: Any, label: str) -> None:
    if not isinstance(record, Mapping) or set(record) != {"path", "size_bytes", "sha256"}:
        raise ValueError(f"{label} identity has unexpected fields")
    if path.stat().st_size != record["size_bytes"] or sha256_path(path) != record["sha256"]:
        raise ValueError(f"{label} identity mismatch")


def load_c3_v2_prefilter_batch(repo_root: Path, batch_dir: Path, spec_path: Path) -> Dict[str, Any]:
    """Bind the four state tables to the frozen specification before FCL."""

    manifest_path = _require_regular(batch_dir / "c3_v2_prefilter_batch_manifest.json", "C3-v2 batch manifest")
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    expected_keys = {
        "schema", "status", "paper_result_eligible", "c4_modified",
        "formal_collision_backend_executed", "oracle_executed", "specification",
        "generation_specification", "report", "state_tables", "selected_candidate_order",
    }
    if not isinstance(manifest, Mapping) or set(manifest) != expected_keys:
        raise ValueError("C3-v2 batch manifest fields differ from the frozen v0.2 schema")
    if manifest["schema"] != BATCH_SCHEMA or manifest["status"] != BATCH_STATUS:
        raise ValueError("C3-v2 batch schema or status is not frozen for FCL")
    if any(manifest[field] is not False for field in (
        "paper_result_eligible", "c4_modified", "formal_collision_backend_executed", "oracle_executed"
    )):
        raise ValueError("C3-v2 batch boundary flags are invalid")

    specification = load_c3_v2_spec(spec_path)
    expected_spec_relative = spec_path.resolve().relative_to(repo_root.resolve()).as_posix()
    if manifest["specification"].get("path") != expected_spec_relative:
        raise ValueError("C3-v2 batch references another specification")
    _validate_identity(spec_path, manifest["specification"], "C3-v2 specification")
    generation_path = _repository_path(
        repo_root, specification["source"]["generation_spec"], "base generation specification"
    )
    if manifest["generation_specification"].get("path") != specification["source"]["generation_spec"]:
        raise ValueError("C3-v2 batch references another base generation specification")
    _validate_identity(generation_path, manifest["generation_specification"], "base generation specification")
    inputs = load_generation_inputs(repo_root, generation_path)

    report_path = _batch_path(batch_dir, manifest["report"].get("path"), "C3-v2 prefilter report")
    _validate_identity(report_path, manifest["report"], "C3-v2 prefilter report")
    report = json.loads(report_path.read_text(encoding="utf-8"))
    selected_order = specification["kinematic_prefilter"]["selected_candidate_order_for_fcl"]
    if manifest["selected_candidate_order"] != selected_order or report.get("selected_for_future_fcl") != selected_order:
        raise ValueError("C3-v2 selected FCL order differs from the frozen specification")
    if set(manifest["state_tables"]) != set(selected_order):
        raise ValueError("C3-v2 batch state-table set differs from the frozen selection")

    generated = {item.candidate_id: item for item in generate_c3_v2_candidates(inputs, specification)}
    report_candidates = {item["candidate_id"]: item for item in report.get("candidates", [])}
    execution_plan = []
    for order_index, candidate_id in enumerate(selected_order):
        candidate = generated.get(candidate_id)
        if candidate is None or candidate.case_id != "C3" or candidate.scope_id != "robot-fixture-empty":
            raise ValueError(f"invalid frozen C3-v2 candidate identity: {candidate_id}")
        if candidate.attached_object_active:
            raise ValueError(f"C3-v2 candidate unexpectedly activates an attachment: {candidate_id}")
        record = manifest["state_tables"][candidate_id]
        state_path = _batch_path(batch_dir, record.get("path"), f"C3-v2 state table {candidate_id}")
        _validate_identity(state_path, record, f"C3-v2 state table {candidate_id}")
        if state_path.read_bytes() != state_tsv_bytes(candidate, inputs):
            raise ValueError(f"C3-v2 state table differs from deterministic generation: {candidate_id}")
        scenarios = expected_scenarios(state_path, candidate_id)
        reported = report_candidates.get(candidate_id)
        if not isinstance(reported, Mapping) or reported.get("sample_count") != len(scenarios):
            raise ValueError(f"C3-v2 report and state sample counts differ: {candidate_id}")
        execution_plan.append({
            "order_index": order_index,
            "candidate_id": candidate_id,
            "state_path": state_path,
            "state_identity": file_identity(state_path, relative_to=batch_dir),
            "sample_count": len(scenarios),
        })
    return {
        "manifest": manifest,
        "manifest_path": manifest_path,
        "manifest_identity": file_identity(manifest_path, relative_to=batch_dir),
        "specification": specification,
        "inputs": inputs,
        "report": report,
        "execution_plan": execution_plan,
    }


def validate_c3_v2_fcl_inputs(
    repo_root: Path,
    loaded: Mapping[str, Any],
    *,
    probe_binary: Path,
    urdf: Path,
    srdf: Path,
) -> Dict[str, Any]:
    """Validate the exact model, geometry inventory, and frozen thresholds."""

    probe_binary = _require_regular(probe_binary, "FCL probe binary")
    if not os.access(probe_binary, os.X_OK):
        raise ValueError("FCL probe binary is not executable")
    if probe_binary.name != "openarm_fcl_pair_probe":
        raise ValueError("FCL probe binary basename differs from the frozen backend target")
    urdf = _require_regular(urdf, "resolved URDF")
    srdf = _require_regular(srdf, "active SRDF")
    inputs = loaded["inputs"]
    members = inputs.source_member_hashes
    if sha256_path(urdf) != members["gate_a_probe/outputs/openarm_v1_bimanual_resolved.urdf"]:
        raise ValueError("resolved URDF hash differs from the frozen model")
    if sha256_path(srdf) != members["gate_a_probe/gate_a2_acm/active_srdf_copy.srdf"]:
        raise ValueError("active SRDF hash differs from the frozen model")

    repository = inputs.specification["repository_inputs"]
    bound_files = {}
    for role, path_key, hash_key in (
        ("world", "world_path", "world_sha256"),
        ("empty_attachments", "empty_attachments_path", "empty_attachments_sha256"),
        ("inventory", "inventory_path", "inventory_file_sha256"),
    ):
        path = _repository_path(repo_root, repository[path_key], role)
        if sha256_path(path) != repository[hash_key]:
            raise ValueError(f"{role} hash differs from the frozen generation input")
        bound_files[role] = {"path_object": path, **file_identity(path, relative_to=repo_root)}
    inventory = json.loads(bound_files["inventory"]["path_object"].read_text(encoding="utf-8"))
    pair_count = len(expected_pair_universe(inventory, "robot-fixture-empty"))
    if pair_count <= 0:
        raise ValueError("C3-v2 empty-scope pair universe is empty")

    common = inputs.specification["acceptance"]["common"]
    predicate = loaded["specification"]["formal_fcl_construction_predicate"]
    expected_thresholds = {
        "endpoint_minimum_strictly_greater_than_m": float(common["endpoint_positive_buffer_m"]),
        "interior_cross_arm_witness_at_or_below_m": -float(common["penetration_witness_depth_m"]),
        "non_target_minimum_before_or_at_witness_strictly_greater_than_m": 0.0,
    }
    if any(float(predicate[key]) != value for key, value in expected_thresholds.items()):
        raise ValueError("C3-v2 FCL thresholds differ from the frozen base predicate")
    return {
        "probe_binary": file_identity(probe_binary),
        "probe_source": file_identity(
            _repository_path(
                repo_root,
                "cpp/openarm_fcl_pair_probe/src/openarm_fcl_pair_probe.cpp",
                "FCL probe source",
            ),
            relative_to=repo_root,
        ),
        "probe_cmake": file_identity(
            _repository_path(
                repo_root,
                "cpp/openarm_fcl_pair_probe/CMakeLists.txt",
                "FCL probe CMake input",
            ),
            relative_to=repo_root,
        ),
        "urdf": file_identity(urdf),
        "srdf": file_identity(srdf),
        "world": {key: value for key, value in bound_files["world"].items() if key != "path_object"},
        "empty_attachments": {
            key: value for key, value in bound_files["empty_attachments"].items() if key != "path_object"
        },
        "inventory": {key: value for key, value in bound_files["inventory"].items() if key != "path_object"},
        "world_path": bound_files["world"]["path_object"],
        "empty_attachments_path": bound_files["empty_attachments"]["path_object"],
        "inventory_path": bound_files["inventory"]["path_object"],
        "inventory_record": inventory,
        "checked_pair_count_per_sample": pair_count,
        "thresholds": expected_thresholds,
    }


def classify_c3_v2_batch(evaluations: Sequence[Mapping[str, Any]], selected_order: Sequence[str]) -> Dict[str, Any]:
    """Return the only allowed terminal or partial state for a sequential batch."""

    if len(evaluations) > len(selected_order):
        raise ValueError("C3-v2 batch contains too many evaluations")
    for index, evaluation in enumerate(evaluations):
        if evaluation.get("candidate_id") != selected_order[index]:
            raise ValueError("C3-v2 evaluation order differs from the frozen order")
        if not isinstance(evaluation.get("accepted"), bool):
            raise ValueError("C3-v2 evaluation is missing a Boolean accepted field")
        if evaluation["accepted"] and index != len(evaluations) - 1:
            raise ValueError("C3-v2 batch continued after the first accepted candidate")
    accepted = next((item for item in evaluations if item["accepted"]), None)
    if accepted is not None:
        return {
            "status": "PASS_C3_V2_FCL_CONSTRUCTION_CANDIDATE",
            "selected_candidate": accepted["candidate_id"],
            "terminal": True,
        }
    if len(evaluations) == len(selected_order):
        return {
            "status": "FAIL_NO_C3_V2_MATCH_WITH_THRESHOLDS_UNCHANGED",
            "selected_candidate": None,
            "terminal": True,
        }
    return {
        "status": "NEXT_C3_V2_FCL_CANDIDATE",
        "selected_candidate": None,
        "next_candidate": selected_order[len(evaluations)],
        "terminal": False,
    }


def canonical_record(value: Mapping[str, Any]) -> bytes:
    return canonical_json_bytes(value)
