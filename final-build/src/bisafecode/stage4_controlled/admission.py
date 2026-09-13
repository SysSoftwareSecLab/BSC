"""Fail-closed admission and deduplication for the future locked population.

FREEZE_ONLY_NOT_FORMAL_STAGE4_EVIDENCE
"""

from __future__ import annotations

from collections import Counter
from typing import Any, Mapping, Optional, Sequence

from bisafecode.stage4_unseen.leakage import check_candidate_leakage

from . import FREEZE_STATUS, SPLIT
from .generator import ControlledCandidate, FAMILY_DEFINITIONS
from .schedule import exact_schedule


def check_admission(
    candidate: ControlledCandidate,
    *,
    canonical_references: Mapping[str, Any],
    fixture_exclusions: Mapping[str, Any],
    prior_attempts: Sequence[Mapping[str, Any]] = (),
) -> Mapping[str, Any]:
    """Apply frozen exclusions before any verifier or oracle output exists."""

    base = check_candidate_leakage(
        source=candidate.source,
        family_id=candidate.family_id,
        split=candidate.split,
        references=canonical_references,
        family_assignments={SPLIT: tuple(FAMILY_DEFINITIONS)},
        prior_records=prior_attempts,
        fixture_exclusions=fixture_exclusions,
    )
    findings = list(base["findings"])
    codes = {item["code"] for item in findings if item["blocking"]}
    for prior in prior_attempts:
        program_id = prior.get("program_id")
        for field, code in (
            ("source_sha256", "DUPLICATE_ATTEMPT_SOURCE_SHA256"),
            ("canonical_ast_hash", "DUPLICATE_ATTEMPT_CANONICAL_AST_HASH"),
        ):
            if prior.get(field) == getattr(candidate.canonical, field):
                codes.add(code)
                findings.append(
                    {
                        "blocking": True,
                        "code": code,
                        "details": {"prior_program_id": program_id},
                    }
                )
    if candidate.split != SPLIT:
        codes.add("SPLIT_NOT_CONTROLLED_UNSEEN_TEST")
    return {
        "evidence_status": FREEZE_STATUS,
        "schema": "bisafecode.stage4.controlled.admission-report/v1",
        "status": "rejected" if codes else "accepted",
        "rejection_reasons": sorted(codes),
        "findings": findings,
        "checked_before_verifier_and_oracle": True,
    }


def validate_exact_population(attempts: Sequence[Mapping[str, Any]]) -> Mapping[str, Any]:
    """Validate exact N=60 with no extension, substitution, or resampling."""

    expected = exact_schedule()
    observed = tuple(
        (str(record.get("family_id")), record.get("seed")) for record in attempts
    )
    reasons = []
    if observed != expected:
        reasons.append("SCHEDULE_ORDER_OR_SLOT_MISMATCH")
    counts = Counter(str(record.get("family_id")) for record in attempts)
    if any(counts[family] != 10 for family, _ in expected[::10]):
        reasons.append("FAMILY_COUNT_NOT_EXACTLY_TEN")
    rejected = sum(record.get("status") == "rejected" for record in attempts)
    if rejected:
        reasons.append("SCHEDULED_REJECTION_PREVENTS_EXACT_N60")
    if len(attempts) != 60:
        reasons.append("ATTEMPT_COUNT_NOT_EXACTLY_60")
    return {
        "evidence_status": FREEZE_STATUS,
        "schema": "bisafecode.stage4.controlled.population-audit/v1",
        "status": "pass" if not reasons else "fail_closed",
        "reasons": sorted(set(reasons)),
        "attempted": len(attempts),
        "accepted": sum(record.get("status") == "accepted" for record in attempts),
        "rejected": rejected,
        "replacement_or_extension_performed": False,
    }
