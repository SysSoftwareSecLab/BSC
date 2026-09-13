"""Strict Raw-to-Derived joins for Stage 4 result preparation.

PREP_ONLY_NOT_FORMAL_STAGE4_EVIDENCE

Raw method records remain label-blind.  This module is the first permitted
place where separately sealed method records and independent oracle records
are joined by exact program identity.  It performs no verifier/oracle run and
does not write a Paper artifact.
"""

from __future__ import annotations

from collections.abc import Iterable, Mapping, Sequence
from typing import Any

from . import PREP_STATUS
from .scalability import SCALABILITY_FACTORS


FORBIDDEN_METHOD_LABEL_KEYS = {
    "oracle_label",
    "oracle_verdict",
    "expected_verdict",
    "target_class",
    "target_verdict",
}


def _find_forbidden(value: Any, path: str = "record") -> list[str]:
    found: list[str] = []
    if isinstance(value, Mapping):
        for key, item in value.items():
            child = f"{path}.{key}"
            if str(key).lower() in FORBIDDEN_METHOD_LABEL_KEYS:
                found.append(child)
            found.extend(_find_forbidden(item, child))
    elif isinstance(value, list):
        for index, item in enumerate(value):
            found.extend(_find_forbidden(item, f"{path}[{index}]"))
    return found


def _index_programs(
    program_metadata: Mapping[str, Mapping[str, Any]],
) -> dict[str, Mapping[str, Any]]:
    indexed: dict[str, Mapping[str, Any]] = {}
    required = {"source_sha256", "family_id", "seed", "split"}
    for program_id, metadata in program_metadata.items():
        missing = sorted(required - set(metadata))
        if missing:
            raise ValueError(f"program {program_id} metadata missing {missing}")
        indexed[str(program_id)] = metadata
    if not indexed:
        raise ValueError("Raw-to-Derived requires at least one admitted program")
    return indexed


def _method_key(record: Mapping[str, Any]) -> tuple[str, str]:
    method_id = record.get("method_id", record.get("variant_id"))
    if not isinstance(method_id, str) or not method_id:
        raise ValueError("method record lacks method_id/variant_id")
    program_id = record.get("program_id")
    if not isinstance(program_id, str) or not program_id:
        raise ValueError("method record lacks program_id")
    return program_id, method_id


def _index_methods(
    records: Iterable[Mapping[str, Any]],
    *,
    programs: Mapping[str, Mapping[str, Any]],
    required_method_ids: Sequence[str],
) -> dict[tuple[str, str], Mapping[str, Any]]:
    indexed: dict[tuple[str, str], Mapping[str, Any]] = {}
    required_methods = set(required_method_ids)
    for record in records:
        forbidden = _find_forbidden(record)
        if forbidden:
            raise ValueError(
                "raw method record contains oracle/target fields: " + ",".join(forbidden)
            )
        key = _method_key(record)
        program_id, method_id = key
        if program_id not in programs or method_id not in required_methods:
            raise ValueError("method record references an unexpected program or method")
        if key in indexed:
            raise ValueError(f"duplicate method record: {key}")
        if record.get("source_sha256") != programs[program_id]["source_sha256"]:
            raise ValueError("method/program SHA-256 mismatch")
        if "budget" not in record:
            raise ValueError("method record lacks its frozen budget")
        indexed[key] = record

    expected = {
        (program_id, method_id)
        for program_id in programs
        for method_id in required_method_ids
    }
    missing = expected - set(indexed)
    extra = set(indexed) - expected
    if missing or extra:
        raise ValueError(
            f"method record matrix is not exact: missing={sorted(missing)} extra={sorted(extra)}"
        )
    return indexed


def _index_oracle(
    records: Iterable[Mapping[str, Any]],
    *,
    programs: Mapping[str, Mapping[str, Any]],
) -> dict[str, Mapping[str, Any]]:
    indexed: dict[str, Mapping[str, Any]] = {}
    for record in records:
        program_id = record.get("program_id")
        if not isinstance(program_id, str) or program_id not in programs:
            raise ValueError("oracle record references an unexpected program")
        if program_id in indexed:
            raise ValueError("duplicate oracle record")
        status, verdict = record.get("status"), record.get("verdict")
        if status == "ok" and verdict not in {"safe", "unsafe", "unknown", "invalid"}:
            raise ValueError("ok oracle record lacks a four-valued oracle verdict")
        if status != "ok" and verdict is not None:
            raise ValueError("non-ok oracle record verdict must be null")
        if record.get("source_sha256") != programs[program_id]["source_sha256"]:
            raise ValueError("oracle/program SHA-256 mismatch")
        indexed[program_id] = record
    if set(indexed) != set(programs):
        raise ValueError("Raw-to-Derived requires exactly one oracle record per program")
    return indexed


def _value(record: Mapping[str, Any], *keys: str) -> Any:
    for key in keys:
        if key in record:
            return record[key]
    return None


def build_tidy_run_rows(
    *,
    experiment_id: str,
    program_metadata: Mapping[str, Mapping[str, Any]],
    method_records: Iterable[Mapping[str, Any]],
    oracle_records: Iterable[Mapping[str, Any]],
    required_method_ids: Sequence[str],
) -> list[dict[str, Any]]:
    """Build exactly one Derived row per admitted program×method run."""

    if not experiment_id:
        raise ValueError("experiment_id is required")
    if not required_method_ids or len(set(required_method_ids)) != len(required_method_ids):
        raise ValueError("required_method_ids must be nonempty and unique")
    programs = _index_programs(program_metadata)
    methods = _index_methods(
        method_records,
        programs=programs,
        required_method_ids=required_method_ids,
    )
    oracle = _index_oracle(oracle_records, programs=programs)

    rows: list[dict[str, Any]] = []
    for program_id in sorted(programs):
        metadata = programs[program_id]
        oracle_record = oracle[program_id]
        for method_id in required_method_ids:
            method = methods[(program_id, method_id)]
            status = str(method.get("status"))
            oracle_status = str(oracle_record.get("status"))
            oracle_verdict = oracle_record.get("verdict")
            row = {
                "prep_status": PREP_STATUS,
                "paper_result_eligible": False,
                "experiment_id": experiment_id,
                "program_id": program_id,
                "source_sha256": metadata["source_sha256"],
                "family_id": metadata["family_id"],
                "seed": metadata["seed"],
                "split": metadata["split"],
                "method_id": method_id,
                "status": status,
                "verdict": method.get("verdict"),
                "reason_codes": list(method.get("reason_codes") or []),
                "runtime_ms": _value(method, "runtime_ms"),
                "peak_rss_bytes": _value(
                    method, "peak_rss_bytes", "peak_memory_bytes"
                ),
                "explored_states": _value(method, "explored_states", "states"),
                "explored_transitions": _value(
                    method, "explored_transitions", "transitions"
                ),
                "model_time_ns": _value(method, "model_time_ns"),
                "timeout": bool(method.get("timeout", status == "timeout")),
                "budget": method["budget"],
                "oracle_status": oracle_status,
                "oracle_verdict": oracle_verdict,
                "oracle_reason_codes": list(oracle_record.get("reason_codes") or []),
                "oracle_binary_valid": oracle_status == "ok"
                and oracle_verdict in {"safe", "unsafe"},
            }
            complexity = metadata.get("complexity", {})
            if not isinstance(complexity, Mapping):
                raise ValueError("program complexity metadata must be an object")
            for factor in SCALABILITY_FACTORS:
                row[factor] = complexity.get(factor)
            rows.append(row)
    return rows


def validate_tidy_run_rows(
    rows: Sequence[Mapping[str, Any]],
    *,
    expected_program_count: int,
    expected_method_count: int,
) -> None:
    expected = expected_program_count * expected_method_count
    if len(rows) != expected:
        raise ValueError(f"Derived tidy row count must be {expected}, observed {len(rows)}")
    keys = [(row.get("program_id"), row.get("method_id")) for row in rows]
    if len(set(keys)) != len(keys):
        raise ValueError("Derived tidy rows contain duplicate program×method keys")
    for row in rows:
        if row.get("prep_status") != PREP_STATUS:
            raise ValueError("Derived preparation marker is missing")
        if row.get("paper_result_eligible") is not False:
            raise ValueError("prepare-only Derived row cannot be paper eligible")
        if row.get("status") == "timeout" and row.get("timeout") is not True:
            raise ValueError("timeout row lost its timeout flag")


def build_derived_preview(
    *,
    experiment_id: str,
    program_metadata: Mapping[str, Mapping[str, Any]],
    method_records: Iterable[Mapping[str, Any]],
    oracle_records: Iterable[Mapping[str, Any]],
    required_method_ids: Sequence[str],
) -> dict[str, Any]:
    rows = build_tidy_run_rows(
        experiment_id=experiment_id,
        program_metadata=program_metadata,
        method_records=method_records,
        oracle_records=oracle_records,
        required_method_ids=required_method_ids,
    )
    validate_tidy_run_rows(
        rows,
        expected_program_count=len(program_metadata),
        expected_method_count=len(required_method_ids),
    )
    status_counts: dict[str, int] = {}
    for row in rows:
        status = str(row["status"])
        status_counts[status] = status_counts.get(status, 0) + 1
    return {
        "schema": "bisafecode.stage4.raw-to-derived-preview/v1",
        "prep_status": PREP_STATUS,
        "paper_result_eligible": False,
        "experiment_id": experiment_id,
        "scheduled_program_count": len(program_metadata),
        "required_method_ids": list(required_method_ids),
        "tidy_row_count": len(rows),
        "status_counts": dict(sorted(status_counts.items())),
        "tidy_rows": rows,
        "paper_export": "FAIL_CLOSED_PENDING_FORMAL_DERIVED_MAC_REVIEW",
    }
