"""Fail-closed schema and cross-file validation for Stage 4 records.

PREP_ONLY_NOT_FORMAL_STAGE4_EVIDENCE

The validator uses the standard library so the data contract does not depend
on an unpinned JSON-schema runtime.  Declarative schemas are shipped beside the
fixture, while these checks enforce hashes, split/family policy, forbidden
label fields, and deduplication invariants that JSON Schema cannot express.
"""

from __future__ import annotations

import hashlib
import json
import re
from pathlib import Path
from typing import Any, Dict, Iterable, List, Mapping, Optional, Sequence

from . import GRAMMAR_VERSION, PREP_STATUS, SCHEMA_VERSION
from .canonicalize import canonicalize_source
from .generator import (
    OBJECTS,
    RESOURCES,
    TRAJECTORIES,
    TRAJECTORY_DURATION_NS,
    GeneratedCandidate,
    generator_sha256,
)
from .time_bounds import source_time_upper_bound_ns
from .toolchain import compute_toolchain_identity


SHA256_RE = re.compile(r"^[0-9a-f]{64}$")
SPLITS = {
    "design_set",
    "development_fixture",
    "controlled_unseen_test",
    "natural_llm_unseen_test",
    "blind_handwritten_test",
}
VERDICTS = {"verified-within-bounds", "violated", "unknown", "invalid"}
RUN_STATUSES = {"ok", "exception", "resource_truncated", "missing"}
FORBIDDEN_GENERATOR_KEYS = {
    "expected_verdict",
    "expected_label",
    "target_class",
    "target_verdict",
    "oracle_label",
    "verifier_verdict",
}


def _length_stratum(action_count: int) -> str:
    if action_count <= 5:
        return "short_1_5"
    if action_count <= 9:
        return "medium_6_9"
    return "long_10_16"


def _time_stratum(bound_ns: int) -> str:
    if bound_ns <= 100_000_000:
        return "short_le_0.1s"
    if bound_ns <= 500_000_000:
        return "medium_gt_0.1s_le_0.5s"
    return "long_gt_0.5s_le_5s"


def _resource_counts(candidate: GeneratedCandidate) -> Mapping[str, int]:
    counts = candidate.canonical.structure["action_kind_counts"]
    return {
        "objects": int(any(name in counts for name in ("close", "open", "transfer_authority"))),
        "logical_resources": int(any(name in counts for name in ("acquire", "release"))),
        "trajectory_references": int(counts.get("move", 0) > 0) * 2,
    }


def make_program_record(
    *,
    candidate: GeneratedCandidate,
    program_id: str,
    source_path: str,
    leakage_report: Mapping[str, Any],
) -> Mapping[str, Any]:
    structure = dict(candidate.canonical.structure)
    action_count = int(structure["source_action_count"])
    record = {
        "evidence_status": PREP_STATUS,
        "schema": SCHEMA_VERSION,
        "program_id": program_id,
        "generator": {
            "name": "bisafecode.stage4_unseen.generator",
            "version_sha256": candidate.generator_sha256,
            "seed": candidate.seed,
            "label_inputs_consumed": False,
        },
        "toolchain": candidate.toolchain_identity,
        "grammar_version": candidate.grammar_version,
        "family_id": candidate.family_id,
        "split": candidate.split,
        "source_path": source_path,
        "source_sha256": candidate.canonical.source_sha256,
        "canonical_ast_hash": candidate.canonical.canonical_ast_hash,
        "structural_family_signature_hash": candidate.canonical.structural_family_signature_hash,
        "time_shift_structural_signature_hash": candidate.canonical.time_shift_structural_signature_hash,
        "status": leakage_report["status"],
        "rejection_reasons": list(leakage_report["rejection_reasons"]),
        "structure": structure,
        "strata": {
            "length": _length_stratum(action_count),
            "concurrency": "parallel_width_2"
            if structure["max_concurrency_width"] == 2
            else "sequential_width_1",
            "time_bound": _time_stratum(candidate.declared_time_bound_ns),
            "loop_bound": "none"
            if structure["loop_count"] == 0
            else "bounded_2_4",
            "resources": _resource_counts(candidate),
        },
        "bounds": {
            "declared_program_time_upper_bound_ns": candidate.declared_time_bound_ns,
            "computed_structural_time_upper_bound_ns": candidate.computed_structural_time_bound_ns,
            "time_bound_margin_ns": candidate.declared_time_bound_ns
            - candidate.computed_structural_time_bound_ns,
            "grammar_loop_bound_max": candidate.parameters["loop_bound"],
            "parallel_width_max": structure["max_concurrency_width"],
        },
        "parser_context": {
            "finite_inputs": {
                "mode": ["safe", "fast"],
                "ready": [False, True],
            },
            "object_ids": list(OBJECTS),
            "resource_ids": list(RESOURCES),
            "trajectories": [
                {"sha256": trajectory, "duration_ns": TRAJECTORY_DURATION_NS}
                for trajectory in TRAJECTORIES
            ],
            "environment_hash": hashlib.sha256(
                b"bisafecode-stage4-unseen-fixed-symbolic-environment-v1"
            ).hexdigest(),
            "max_loop_bound": 4,
            "semantic_status": "PARSER_CONTEXT_ONLY_PROPERTY_AND_ORACLE_ADAPTER_REMAIN_APPROVAL_BOUND",
        },
        "leakage_findings": list(leakage_report["findings"]),
    }
    return record


def _forbidden_paths(value: Any, prefix: str = "$") -> Iterable[str]:
    if isinstance(value, dict):
        for key, item in value.items():
            path = "{}.{}".format(prefix, key)
            if key.lower() in FORBIDDEN_GENERATOR_KEYS:
                yield path
            yield from _forbidden_paths(item, path)
    elif isinstance(value, list):
        for index, item in enumerate(value):
            yield from _forbidden_paths(item, "{}[{}]".format(prefix, index))


def _safe_relative(path_value: str) -> bool:
    path = Path(path_value)
    return not path.is_absolute() and ".." not in path.parts


def validate_program_record(
    record: Mapping[str, Any],
    *,
    root: Optional[Path] = None,
    family_assignments: Optional[Mapping[str, Sequence[str]]] = None,
) -> List[str]:
    errors: List[str] = []
    required = {
        "evidence_status",
        "schema",
        "program_id",
        "generator",
        "toolchain",
        "grammar_version",
        "family_id",
        "split",
        "source_path",
        "source_sha256",
        "canonical_ast_hash",
        "structural_family_signature_hash",
        "status",
        "rejection_reasons",
        "structure",
        "strata",
        "bounds",
        "parser_context",
        "time_shift_structural_signature_hash",
        "leakage_findings",
    }
    missing = sorted(required - set(record))
    if missing:
        errors.append("missing required fields: {}".format(", ".join(missing)))
    if record.get("evidence_status") != PREP_STATUS:
        errors.append("evidence_status must be prep-only marker")
    if record.get("schema") != SCHEMA_VERSION:
        errors.append("unexpected program-record schema")
    if record.get("grammar_version") != GRAMMAR_VERSION:
        errors.append("unexpected grammar_version")
    if record.get("split") not in SPLITS:
        errors.append("invalid split")
    if record.get("status") not in {"accepted", "rejected"}:
        errors.append("invalid generation status")
    reasons = record.get("rejection_reasons")
    if not isinstance(reasons, list):
        errors.append("rejection_reasons must be a list")
    elif record.get("status") == "accepted" and reasons:
        errors.append("accepted record cannot have rejection reasons")
    elif record.get("status") == "rejected" and not reasons:
        errors.append("rejected record must have a rejection reason")
    generator = record.get("generator")
    if not isinstance(generator, dict):
        errors.append("generator must be an object")
    else:
        if not SHA256_RE.match(str(generator.get("version_sha256", ""))):
            errors.append("generator.version_sha256 must be lowercase SHA-256")
        elif generator.get("version_sha256") != generator_sha256():
            errors.append("generator.version_sha256 does not match generator file")
        seed = generator.get("seed")
        if isinstance(seed, bool) or not isinstance(seed, int) or seed < 0:
            errors.append("generator.seed must be a nonnegative integer")
        if generator.get("label_inputs_consumed") is not False:
            errors.append("generator must declare label_inputs_consumed=false")
    toolchain = record.get("toolchain")
    if not isinstance(toolchain, dict):
        errors.append("toolchain must be an object")
    else:
        try:
            expected_toolchain = compute_toolchain_identity()
        except (FileNotFoundError, OSError) as error:
            errors.append("toolchain identity cannot be recomputed: {}".format(error))
        else:
            if toolchain != expected_toolchain:
                errors.append("toolchain identity does not match complete prep toolchain")
    for key in (
        "source_sha256",
        "canonical_ast_hash",
        "structural_family_signature_hash",
    ):
        if not SHA256_RE.match(str(record.get(key, ""))):
            errors.append("{} must be lowercase SHA-256".format(key))
    time_hash = record.get("time_shift_structural_signature_hash")
    if time_hash is not None and not SHA256_RE.match(str(time_hash)):
        errors.append(
            "time_shift_structural_signature_hash must be null or lowercase SHA-256"
        )
    bounds = record.get("bounds")
    if not isinstance(bounds, dict):
        errors.append("bounds must be an object")
    else:
        declared = bounds.get("declared_program_time_upper_bound_ns")
        computed = bounds.get("computed_structural_time_upper_bound_ns")
        margin = bounds.get("time_bound_margin_ns")
        if any(isinstance(value, bool) or not isinstance(value, int) for value in (declared, computed, margin)):
            errors.append("time-bound fields must be integers")
        elif declared < computed or margin != declared - computed or margin < 0:
            errors.append("declared time bound is below or inconsistent with structural bound")
    source_path = record.get("source_path")
    if not isinstance(source_path, str) or not _safe_relative(source_path):
        errors.append("source_path must be a safe relative path")
    forbidden = list(_forbidden_paths(record))
    if forbidden:
        errors.append("forbidden label/target fields: {}".format(", ".join(forbidden)))
    if family_assignments is not None:
        split = str(record.get("split"))
        allowed = set(family_assignments.get(split, ()))
        if record.get("family_id") not in allowed:
            errors.append("family_id is not assigned to split")
    parser_context = record.get("parser_context")
    if not isinstance(parser_context, dict):
        errors.append("parser_context must be an object")
    elif parser_context.get("semantic_status") != (
        "PARSER_CONTEXT_ONLY_PROPERTY_AND_ORACLE_ADAPTER_REMAIN_APPROVAL_BOUND"
    ):
        errors.append("parser_context semantic status is missing")
    if root is not None and isinstance(source_path, str) and _safe_relative(source_path):
        path = root / source_path
        if not path.is_file():
            errors.append("source file does not exist")
        else:
            source_bytes = path.read_bytes()
            source_sha = hashlib.sha256(source_bytes).hexdigest()
            if source_sha != record.get("source_sha256"):
                errors.append("source_sha256 does not match source file")
            try:
                canonical = canonicalize_source(source_bytes.decode("utf-8"))
            except (UnicodeDecodeError, ValueError) as error:
                errors.append("source canonicalization failed: {}".format(error))
            else:
                if canonical.canonical_ast_hash != record.get("canonical_ast_hash"):
                    errors.append("canonical_ast_hash does not match source")
                if canonical.structural_family_signature_hash != record.get(
                    "structural_family_signature_hash"
                ):
                    errors.append("structural_family_signature_hash does not match source")
                if canonical.time_shift_structural_signature_hash != record.get(
                    "time_shift_structural_signature_hash"
                ):
                    errors.append(
                        "time_shift_structural_signature_hash does not match source"
                    )
                try:
                    trajectory_durations = {
                        item["sha256"]: item["duration_ns"]
                        for item in record["parser_context"]["trajectories"]
                    }
                    structural_bound = source_time_upper_bound_ns(
                        source_bytes.decode("utf-8"), trajectory_durations
                    )
                except (KeyError, TypeError, ValueError) as error:
                    errors.append("structural time-bound audit failed: {}".format(error))
                else:
                    if isinstance(bounds, dict) and structural_bound != bounds.get(
                        "computed_structural_time_upper_bound_ns"
                    ):
                        errors.append("computed structural time bound does not match source")
    return errors


def validate_manifest(
    manifest: Mapping[str, Any],
    *,
    root: Optional[Path] = None,
    family_assignments: Optional[Mapping[str, Sequence[str]]] = None,
    fixture_exclusions: Optional[Mapping[str, Any]] = None,
) -> List[str]:
    errors: List[str] = []
    if manifest.get("evidence_status") != PREP_STATUS:
        errors.append("manifest evidence_status must be prep-only marker")
    if manifest.get("schema") != "bisafecode.stage4.unseen.program-manifest/v2":
        errors.append("unexpected manifest schema")
    records = manifest.get("records")
    if not isinstance(records, list):
        return errors + ["manifest.records must be a list"]
    seen: Dict[str, Dict[str, str]] = {
        "program_id": {},
        "source_sha256": {},
        "canonical_ast_hash": {},
    }
    seen_structure: Dict[str, Mapping[str, Any]] = {}
    fixture_sets = {
        key: set((fixture_exclusions or {}).get(key, ()))
        for key in (
            "source_sha256",
            "canonical_ast_hash",
            "structural_family_signature_hash",
        )
    }
    for index, record in enumerate(records):
        if not isinstance(record, dict):
            errors.append("record {} must be an object".format(index))
            continue
        record_errors = validate_program_record(
            record, root=root, family_assignments=family_assignments
        )
        errors.extend("record {}: {}".format(index, error) for error in record_errors)
        if record.get("status") != "accepted":
            continue
        for key, identities in seen.items():
            value = str(record.get(key, ""))
            if value in identities:
                errors.append(
                    "accepted duplicate {} between {} and {}".format(
                        key, identities[value], record.get("program_id")
                    )
                )
            else:
                identities[value] = str(record.get("program_id"))
        signature = str(record.get("structural_family_signature_hash", ""))
        prior_structure = seen_structure.get(signature)
        if prior_structure is not None and prior_structure.get("split") != record.get(
            "split"
        ):
            errors.append(
                "accepted cross-split duplicate structural_family_signature_hash between {} and {}".format(
                    prior_structure.get("program_id"), record.get("program_id")
                )
            )
        elif prior_structure is not None and prior_structure.get("family_id") != record.get(
            "family_id"
        ):
            errors.append(
                "accepted cross-family duplicate structural_family_signature_hash between {} and {}".format(
                    prior_structure.get("program_id"), record.get("program_id")
                )
            )
        else:
            seen_structure[signature] = record
        if record.get("split") in {
            "controlled_unseen_test",
            "natural_llm_unseen_test",
        }:
            for key, values in fixture_sets.items():
                if record.get(key) in values:
                    errors.append(
                        "locked record {} reuses fixture {}".format(
                            record.get("program_id"), key
                        )
                    )
    counts = manifest.get("counts")
    actual_counts = {
        "attempted": len(records),
        "accepted": sum(record.get("status") == "accepted" for record in records),
        "rejected": sum(record.get("status") == "rejected" for record in records),
    }
    if counts != actual_counts:
        errors.append("manifest counts do not match records")
    return errors


def validate_run_record(record: Mapping[str, Any]) -> List[str]:
    errors: List[str] = []
    from . import FORMAL_STATUS

    if record.get("evidence_status") not in {PREP_STATUS, FORMAL_STATUS}:
        errors.append("run record evidence_status is invalid")
    if record.get("schema") != "bisafecode.stage4.unseen.run-record/v2":
        errors.append("unexpected run-record schema")
    if record.get("phase") not in {"oracle", "verifier"}:
        errors.append("phase must be oracle or verifier")
    status = record.get("status")
    if status not in RUN_STATUSES:
        errors.append("invalid run status")
    verdict = record.get("verdict")
    if status == "ok" and verdict not in VERDICTS:
        errors.append("ok record requires a four-valued verdict")
    if status != "ok" and verdict is not None:
        errors.append("non-ok record cannot carry a verdict")
    if record.get("phase") == "verifier" and "oracle_label" in record:
        errors.append("verifier record cannot contain oracle_label")
    if record.get("phase") == "oracle" and "verifier_verdict" in record:
        errors.append("oracle record cannot contain verifier_verdict")
    if "runtime_ms" not in record:
        errors.append("runtime_ms is required")
    runtime_ms = record.get("runtime_ms")
    if runtime_ms is not None and (
        isinstance(runtime_ms, bool)
        or not isinstance(runtime_ms, (int, float))
        or runtime_ms < 0
    ):
        errors.append("runtime_ms must be a nonnegative number or null")
    return errors


def load_json(path: Path) -> Any:
    return json.loads(path.read_text(encoding="utf-8"))
