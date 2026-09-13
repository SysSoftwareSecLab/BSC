"""Fail-closed admission checks for evidence-bound safety-contract fields.

This module does not decide whether a physical contract is true.  It checks
whether a candidate contract preserves or conservatively strengthens values
that were already derived or independently evidenced.  A missing evidence
value yields ``unknown-contract-evidence``; a contradiction yields
``invalid-contract``.  Neither disposition is releasable.
"""

from __future__ import annotations

import math
from dataclasses import dataclass
from enum import Enum
from typing import Any, Mapping, Sequence


class ContractAdmissionStatus(str, Enum):
    ADMITTED = "admitted"
    INVALID = "invalid-contract"
    UNKNOWN = "unknown-contract-evidence"


@dataclass(frozen=True)
class ContractAdmission:
    status: ContractAdmissionStatus
    reasons: tuple[str, ...] = ()

    @property
    def release_allowed(self) -> bool:
        return self.status is ContractAdmissionStatus.ADMITTED


def _result(*, invalid: Sequence[str] = (), unknown: Sequence[str] = ()) -> ContractAdmission:
    invalid_reasons = tuple(sorted(set(invalid)))
    unknown_reasons = tuple(sorted(set(unknown)))
    if invalid_reasons:
        return ContractAdmission(ContractAdmissionStatus.INVALID, invalid_reasons)
    if unknown_reasons:
        return ContractAdmission(ContractAdmissionStatus.UNKNOWN, unknown_reasons)
    return ContractAdmission(ContractAdmissionStatus.ADMITTED)


def qualify_pair_scope(
    candidate_pairs: Sequence[Mapping[str, Any]],
    reference_pairs: Sequence[Mapping[str, Any]],
) -> ContractAdmission:
    """Admit exact or more conservative pair coverage.

    A reference-checked pair may not be excluded.  A reference exclusion may
    be changed to checked (more conservative), but retaining the exclusion
    requires the same rationale/evidence binding.
    """

    invalid: list[str] = []
    unknown: list[str] = []

    def indexed(rows: Sequence[Mapping[str, Any]], label: str) -> dict[tuple[str, str], Mapping[str, Any]]:
        result: dict[tuple[str, str], Mapping[str, Any]] = {}
        for row in rows:
            raw_pair = row.get("entity_pair")
            if not isinstance(raw_pair, (list, tuple)) or len(raw_pair) != 2:
                invalid.append(f"{label}:malformed-pair")
                continue
            pair = tuple(sorted((str(raw_pair[0]), str(raw_pair[1]))))
            if pair in result:
                invalid.append(f"{label}:duplicate-pair:{pair[0]}:{pair[1]}")
            result[pair] = row
        return result

    candidate = indexed(candidate_pairs, "candidate")
    reference = indexed(reference_pairs, "reference")
    missing = sorted(set(reference) - set(candidate))
    extra = sorted(set(candidate) - set(reference))
    invalid.extend(f"pair-missing:{a}:{b}" for a, b in missing)
    invalid.extend(f"pair-extra:{a}:{b}" for a, b in extra)

    for pair in sorted(set(candidate) & set(reference)):
        candidate_row = candidate[pair]
        reference_row = reference[pair]
        candidate_disposition = candidate_row.get("disposition")
        reference_disposition = reference_row.get("disposition")
        pair_id = f"{pair[0]}:{pair[1]}"
        if reference_disposition == "checked":
            if candidate_disposition != "checked":
                invalid.append(f"checked-pair-weakened:{pair_id}")
        elif reference_disposition == "excluded":
            if candidate_disposition not in {"excluded", "checked"}:
                invalid.append(f"unsupported-pair-disposition:{pair_id}")
            elif candidate_disposition == "excluded":
                for field in ("evidence_ref", "rationale"):
                    if not candidate_row.get(field):
                        unknown.append(f"exclusion-evidence-missing:{pair_id}:{field}")
                    elif candidate_row.get(field) != reference_row.get(field):
                        invalid.append(f"exclusion-evidence-drift:{pair_id}:{field}")
        else:
            invalid.append(f"unsupported-reference-disposition:{pair_id}")
    return _result(invalid=invalid, unknown=unknown)


SPEED_FIELDS = (
    "left_joint_m_per_rad",
    "right_joint_m_per_rad",
    "left_gripper_m_per_m",
    "right_gripper_m_per_m",
)


def qualify_speed_envelopes(
    candidate_entities: Sequence[Mapping[str, Any]],
    reference_entities: Sequence[Mapping[str, Any]],
) -> ContractAdmission:
    """Reject any coefficient below the evidence-derived reference envelope."""

    invalid: list[str] = []
    unknown: list[str] = []
    candidate = {str(row.get("entity_id")): row for row in candidate_entities}
    reference = {str(row.get("entity_id")): row for row in reference_entities}
    if len(candidate) != len(candidate_entities):
        invalid.append("candidate:duplicate-entity")
    if set(candidate) != set(reference):
        invalid.append("speed-envelope-entity-set-mismatch")

    def values(row: Mapping[str, Any], field: str) -> tuple[float, ...] | None:
        value = row.get(field)
        if value is None:
            return None
        raw = value if isinstance(value, (list, tuple)) else (value,)
        try:
            converted = tuple(float(item) for item in raw)
        except (TypeError, ValueError):
            return ()
        return converted

    for entity_id in sorted(set(candidate) & set(reference)):
        candidate_row = candidate[entity_id]
        reference_row = reference[entity_id]
        if candidate_row.get("motion_source") != reference_row.get("motion_source"):
            invalid.append(f"motion-source-drift:{entity_id}")
        for field in SPEED_FIELDS:
            candidate_values = values(candidate_row, field)
            reference_values = values(reference_row, field)
            if candidate_values is None:
                unknown.append(f"speed-evidence-missing:{entity_id}:{field}")
                continue
            if reference_values is None or not reference_values:
                invalid.append(f"reference-speed-malformed:{entity_id}:{field}")
                continue
            if len(candidate_values) != len(reference_values):
                invalid.append(f"speed-shape-drift:{entity_id}:{field}")
                continue
            for index, (candidate_value, reference_value) in enumerate(
                zip(candidate_values, reference_values)
            ):
                if not math.isfinite(candidate_value) or candidate_value < 0.0:
                    invalid.append(f"speed-invalid:{entity_id}:{field}:{index}")
                elif candidate_value < reference_value:
                    invalid.append(f"speed-underbound:{entity_id}:{field}:{index}")
    return _result(invalid=invalid, unknown=unknown)


def qualify_distance_error_bounds(
    candidate_entities: Sequence[Mapping[str, Any]],
    reference_entities: Sequence[Mapping[str, Any]],
) -> ContractAdmission:
    """Reject collision-distance error bounds below their evidence reference.

    Larger non-negative bounds are conservative because they widen the
    uncertainty interval used by the continuous collision checker.  A missing
    candidate value is unknown rather than admitted; malformed, non-finite,
    negative, or underbounding values are invalid.
    """

    invalid: list[str] = []
    unknown: list[str] = []

    def indexed(
        rows: Sequence[Mapping[str, Any]], label: str
    ) -> dict[str, Mapping[str, Any]]:
        result: dict[str, Mapping[str, Any]] = {}
        for row in rows:
            entity_id = row.get("entity_id")
            if not isinstance(entity_id, str) or not entity_id:
                invalid.append(f"{label}:malformed-entity")
                continue
            if entity_id in result:
                invalid.append(f"{label}:duplicate-entity:{entity_id}")
            result[entity_id] = row
        return result

    candidate = indexed(candidate_entities, "candidate")
    reference = indexed(reference_entities, "reference")
    if set(candidate) != set(reference):
        invalid.append("distance-error-entity-set-mismatch")

    for entity_id in sorted(set(candidate) & set(reference)):
        candidate_value = candidate[entity_id].get("distance_error_bound_m")
        reference_value = reference[entity_id].get("distance_error_bound_m")
        if candidate_value is None:
            unknown.append(f"distance-error-evidence-missing:{entity_id}")
            continue
        try:
            candidate_bound = float(candidate_value)
        except (TypeError, ValueError):
            invalid.append(f"distance-error-invalid:{entity_id}")
            continue
        try:
            reference_bound = float(reference_value)
        except (TypeError, ValueError):
            invalid.append(f"reference-distance-error-malformed:{entity_id}")
            continue
        if not math.isfinite(reference_bound) or reference_bound < 0.0:
            invalid.append(f"reference-distance-error-malformed:{entity_id}")
        elif not math.isfinite(candidate_bound) or candidate_bound < 0.0:
            invalid.append(f"distance-error-invalid:{entity_id}")
        elif candidate_bound < reference_bound:
            invalid.append(f"distance-error-underbound:{entity_id}")
    return _result(invalid=invalid, unknown=unknown)


def qualify_observer_claims(
    candidate_claims: Mapping[str, bool | None],
    evidence_claims: Mapping[str, bool],
) -> ContractAdmission:
    """Require every evidence-supported observer claim and exact value agreement."""

    invalid: list[str] = []
    unknown: list[str] = []
    for claim_id, expected in sorted(evidence_claims.items()):
        if claim_id not in candidate_claims or candidate_claims[claim_id] is None:
            unknown.append(f"observer-evidence-missing:{claim_id}")
        elif candidate_claims[claim_id] is not expected:
            invalid.append(f"observer-evidence-mismatch:{claim_id}")
    for claim_id in sorted(set(candidate_claims) - set(evidence_claims)):
        invalid.append(f"observer-claim-unbound:{claim_id}")
    return _result(invalid=invalid, unknown=unknown)


def qualify_trajectory_bindings(
    candidate_bindings: Mapping[str, str | None],
    evidence_bindings: Mapping[str, str],
) -> ContractAdmission:
    """Require exact action-locator-to-content-hash trajectory bindings."""

    invalid: list[str] = []
    unknown: list[str] = []
    for locator, expected in sorted(evidence_bindings.items()):
        if locator not in candidate_bindings or candidate_bindings[locator] is None:
            unknown.append(f"trajectory-evidence-missing:{locator}")
        elif candidate_bindings[locator] != expected:
            invalid.append(f"trajectory-hash-mismatch:{locator}")
    for locator in sorted(set(candidate_bindings) - set(evidence_bindings)):
        invalid.append(f"trajectory-binding-unbound:{locator}")
    return _result(invalid=invalid, unknown=unknown)
