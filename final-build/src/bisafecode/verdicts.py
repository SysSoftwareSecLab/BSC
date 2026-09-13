"""Strict four-valued result schema and non-gaming evaluation metrics."""

from __future__ import annotations

from dataclasses import dataclass
from enum import Enum
from typing import Iterable, Optional, Tuple


class Verdict(str, Enum):
    VERIFIED = "verified-within-bounds"
    VIOLATED = "violated"
    UNKNOWN = "unknown"
    INVALID = "invalid"


class OracleLabel(str, Enum):
    SAFE = "safe"
    UNSAFE = "unsafe"
    INVALID = "invalid"
    INDETERMINATE = "indeterminate"


@dataclass(frozen=True)
class CaseResult:
    case_id: str
    oracle: OracleLabel
    verdict: Verdict
    unknown_reason: Optional[str] = None
    invalid_reason: Optional[str] = None

    def __post_init__(self) -> None:
        if self.verdict is Verdict.UNKNOWN and not self.unknown_reason:
            raise ValueError("unknown verdict requires unknown_reason")
        if self.verdict is Verdict.INVALID and not self.invalid_reason:
            raise ValueError("invalid verdict requires invalid_reason")


@dataclass(frozen=True)
class Summary:
    n_total: int
    n_valid_oracle: int
    n_safe: int
    n_unsafe: int
    n_decided: int
    n_unknown: int
    n_invalid_on_valid: int
    verified_on_safe: int
    violated_on_unsafe: int
    verified_on_unsafe: int
    violated_on_safe: int
    coverage: Optional[float]
    unknown_rate: Optional[float]
    invalid_rate: Optional[float]
    unsafe_recall: Optional[float]
    false_safe_rate: Optional[float]
    false_alarm_rate: Optional[float]
    safe_acceptance: Optional[float]
    overall_correct: Optional[float]
    agreement_on_decided: Optional[float]
    excluded_case_ids: Tuple[str, ...]


def _rate(num: int, den: int) -> Optional[float]:
    return num / den if den else None


def summarize(results: Iterable[CaseResult]) -> Summary:
    rows = tuple(results)
    eligible = tuple(
        row for row in rows if row.oracle in {OracleLabel.SAFE, OracleLabel.UNSAFE}
    )
    excluded = tuple(
        row.case_id
        for row in rows
        if row.oracle not in {OracleLabel.SAFE, OracleLabel.UNSAFE}
    )

    safe = tuple(row for row in eligible if row.oracle is OracleLabel.SAFE)
    unsafe = tuple(row for row in eligible if row.oracle is OracleLabel.UNSAFE)
    decided = tuple(
        row for row in eligible if row.verdict in {Verdict.VERIFIED, Verdict.VIOLATED}
    )

    verified_on_safe = sum(row.verdict is Verdict.VERIFIED for row in safe)
    violated_on_unsafe = sum(row.verdict is Verdict.VIOLATED for row in unsafe)
    verified_on_unsafe = sum(row.verdict is Verdict.VERIFIED for row in unsafe)
    violated_on_safe = sum(row.verdict is Verdict.VIOLATED for row in safe)
    n_unknown = sum(row.verdict is Verdict.UNKNOWN for row in eligible)
    n_invalid_on_valid = sum(row.verdict is Verdict.INVALID for row in eligible)
    n_correct = verified_on_safe + violated_on_unsafe

    return Summary(
        n_total=len(rows),
        n_valid_oracle=len(eligible),
        n_safe=len(safe),
        n_unsafe=len(unsafe),
        n_decided=len(decided),
        n_unknown=n_unknown,
        n_invalid_on_valid=n_invalid_on_valid,
        verified_on_safe=verified_on_safe,
        violated_on_unsafe=violated_on_unsafe,
        verified_on_unsafe=verified_on_unsafe,
        violated_on_safe=violated_on_safe,
        coverage=_rate(len(decided), len(eligible)),
        unknown_rate=_rate(n_unknown, len(eligible)),
        invalid_rate=_rate(n_invalid_on_valid, len(eligible)),
        unsafe_recall=_rate(violated_on_unsafe, len(unsafe)),
        false_safe_rate=_rate(verified_on_unsafe, len(unsafe)),
        false_alarm_rate=_rate(violated_on_safe, len(safe)),
        safe_acceptance=_rate(verified_on_safe, len(safe)),
        overall_correct=_rate(n_correct, len(eligible)),
        agreement_on_decided=_rate(n_correct, len(decided)),
        excluded_case_ids=excluded,
    )
