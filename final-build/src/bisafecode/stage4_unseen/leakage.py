"""Canonical-case leakage and within-corpus deduplication checks.

PREP_ONLY_NOT_FORMAL_STAGE4_EVIDENCE

All findings are computed before oracle or verifier execution.  Conservative
near matches are rejected or escalated for Mac review; a seed mismatch never
counts as isolation evidence.
"""

from __future__ import annotations

import hashlib
from pathlib import Path
from typing import Any, Dict, Iterable, List, Mapping, Optional, Sequence

from . import PREP_STATUS
from .canonicalize import canonicalize_source, token_edit_distance


BLOCKING_CODES = {
    "CANONICAL_SOURCE_SHA256_MATCH",
    "CANONICAL_AST_HASH_MATCH",
    "CANONICAL_STRUCTURAL_FAMILY_MATCH",
    "CANONICAL_TIME_SHIFT_STRUCTURAL_MATCH",
    "CANONICAL_NEAR_STRUCTURE",
    "DUPLICATE_SOURCE_SHA256",
    "DUPLICATE_CANONICAL_AST_HASH",
    "DUPLICATE_STRUCTURAL_FAMILY_SIGNATURE",
    "CROSS_SPLIT_STRUCTURAL_FAMILY_MATCH",
    "FIXTURE_SOURCE_REUSE",
    "FIXTURE_CANONICAL_REUSE",
    "FIXTURE_STRUCTURAL_FAMILY_REUSE",
    "FAMILY_SPLIT_VIOLATION",
}


def reference_entry(case_id: str, path: Path, root: Path) -> Mapping[str, Any]:
    source = path.read_text(encoding="utf-8")
    canonical = canonicalize_source(source)
    return {
        "evidence_status": PREP_STATUS,
        "case_id": case_id,
        "source_path": path.relative_to(root).as_posix(),
        "source_sha256": hashlib.sha256(path.read_bytes()).hexdigest(),
        "canonical_ast_hash": canonical.canonical_ast_hash,
        "structural_family_signature_hash": canonical.structural_family_signature_hash,
        "time_shift_structural_signature_hash": canonical.time_shift_structural_signature_hash,
        "action_tokens": list(canonical.action_tokens),
    }


def build_reference_catalog(
    root: Path, case_paths: Mapping[str, str]
) -> Mapping[str, Any]:
    return {
        "evidence_status": PREP_STATUS,
        "schema": "bisafecode.stage4.unseen.canonical-reference-catalog/v2",
        "references": [
            reference_entry(case_id, root / relative_path, root)
            for case_id, relative_path in sorted(case_paths.items())
        ],
    }


def _near_threshold(token_count: int) -> int:
    if token_count <= 1:
        return 0
    if token_count <= 4:
        return 1
    return max(1, token_count // 5)


def _finding(code: str, **details: Any) -> Mapping[str, Any]:
    return {
        "code": code,
        "blocking": code in BLOCKING_CODES,
        "details": details,
    }


def _hash_set(catalog: Optional[Mapping[str, Any]], key: str) -> set:
    if not catalog:
        return set()
    values = catalog.get(key, [])
    return {value for value in values if value}


def check_candidate_leakage(
    *,
    source: str,
    family_id: str,
    split: str,
    references: Mapping[str, Any],
    family_assignments: Mapping[str, Sequence[str]],
    prior_records: Sequence[Mapping[str, Any]] = (),
    fixture_exclusions: Optional[Mapping[str, Any]] = None,
) -> Mapping[str, Any]:
    canonical = canonicalize_source(source)
    findings: List[Mapping[str, Any]] = []

    allowed_families = set(family_assignments.get(split, ()))
    if family_id not in allowed_families:
        findings.append(
            _finding(
                "FAMILY_SPLIT_VIOLATION",
                family_id=family_id,
                split=split,
            )
        )

    for reference in references.get("references", []):
        case_id = reference["case_id"]
        if canonical.source_sha256 == reference["source_sha256"]:
            findings.append(_finding("CANONICAL_SOURCE_SHA256_MATCH", case_id=case_id))
        if canonical.canonical_ast_hash == reference["canonical_ast_hash"]:
            findings.append(_finding("CANONICAL_AST_HASH_MATCH", case_id=case_id))
        if canonical.structural_family_signature_hash == reference[
            "structural_family_signature_hash"
        ]:
            findings.append(
                _finding("CANONICAL_STRUCTURAL_FAMILY_MATCH", case_id=case_id)
            )
        reference_time_hash = reference.get("time_shift_structural_signature_hash")
        if (
            reference_time_hash
            and canonical.time_shift_structural_signature_hash
            and canonical.time_shift_structural_signature_hash == reference_time_hash
        ):
            findings.append(
                _finding("CANONICAL_TIME_SHIFT_STRUCTURAL_MATCH", case_id=case_id)
            )
        reference_tokens = tuple(reference.get("action_tokens", ()))
        distance = token_edit_distance(canonical.action_tokens, reference_tokens)
        threshold = _near_threshold(len(reference_tokens))
        if distance <= threshold and canonical.structural_family_signature_hash != reference.get(
            "structural_family_signature_hash"
        ):
            findings.append(
                _finding(
                    "CANONICAL_NEAR_STRUCTURE",
                    case_id=case_id,
                    token_edit_distance=distance,
                    threshold=threshold,
                )
            )

    for record in prior_records:
        if record.get("status") != "accepted":
            continue
        program_id = record.get("program_id")
        if canonical.source_sha256 == record.get("source_sha256"):
            findings.append(
                _finding("DUPLICATE_SOURCE_SHA256", prior_program_id=program_id)
            )
        if canonical.canonical_ast_hash == record.get("canonical_ast_hash"):
            findings.append(
                _finding("DUPLICATE_CANONICAL_AST_HASH", prior_program_id=program_id)
            )
        same_structure = canonical.structural_family_signature_hash == record.get(
            "structural_family_signature_hash"
        )
        if same_structure and split != record.get("split"):
            findings.append(
                _finding(
                    "CROSS_SPLIT_STRUCTURAL_FAMILY_MATCH",
                    prior_program_id=program_id,
                    prior_split=record.get("split"),
                )
            )
        elif same_structure and family_id != record.get("family_id"):
            findings.append(
                _finding(
                    "DUPLICATE_STRUCTURAL_FAMILY_SIGNATURE",
                    prior_program_id=program_id,
                )
            )

    if canonical.source_sha256 in _hash_set(fixture_exclusions, "source_sha256"):
        findings.append(_finding("FIXTURE_SOURCE_REUSE"))
    if canonical.canonical_ast_hash in _hash_set(
        fixture_exclusions, "canonical_ast_hash"
    ):
        findings.append(_finding("FIXTURE_CANONICAL_REUSE"))
    if canonical.structural_family_signature_hash in _hash_set(
        fixture_exclusions, "structural_family_signature_hash"
    ):
        findings.append(_finding("FIXTURE_STRUCTURAL_FAMILY_REUSE"))

    deduplicated: List[Mapping[str, Any]] = []
    seen = set()
    for finding in findings:
        identity = (
            finding["code"],
            tuple(sorted((key, str(value)) for key, value in finding["details"].items())),
        )
        if identity not in seen:
            deduplicated.append(finding)
            seen.add(identity)
    rejection_reasons = sorted(
        {finding["code"] for finding in deduplicated if finding["blocking"]}
    )
    return {
        "evidence_status": PREP_STATUS,
        "schema": "bisafecode.stage4.unseen.leakage-report/v2",
        "source_sha256": canonical.source_sha256,
        "canonical_ast_hash": canonical.canonical_ast_hash,
        "structural_family_signature_hash": canonical.structural_family_signature_hash,
        "time_shift_structural_signature_hash": canonical.time_shift_structural_signature_hash,
        "status": "rejected" if rejection_reasons else "accepted",
        "rejection_reasons": rejection_reasons,
        "findings": deduplicated,
    }


def fixture_exclusion_catalog(records: Iterable[Mapping[str, Any]]) -> Mapping[str, Any]:
    records = list(records)
    return {
        "evidence_status": PREP_STATUS,
        "schema": "bisafecode.stage4.unseen.fixture-exclusions/v2",
        "rule": "Every fixture attempt is excluded from every future formal set, regardless of acceptance status.",
        "program_ids": sorted(
            {str(record["program_id"]) for record in records if record.get("program_id")}
        ),
        "source_sha256": sorted(
            {str(record["source_sha256"]) for record in records if record.get("source_sha256")}
        ),
        "canonical_ast_hash": sorted(
            {
                str(record["canonical_ast_hash"])
                for record in records
                if record.get("canonical_ast_hash")
            }
        ),
        "structural_family_signature_hash": sorted(
            {
                str(record["structural_family_signature_hash"])
                for record in records
                if record.get("structural_family_signature_hash")
            }
        ),
    }
