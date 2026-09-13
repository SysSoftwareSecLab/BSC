"""Conservative distance--speed certificates for continuous collision safety.

The checker proves an interval safe only when every configured collision pair
has both (1) a signed-distance sample at the interval midpoint and (2) a sound
upper bound on its distance closing speed over the entire interval.  If

    d_hat(mid) - v_close * max(|mid-start|, |end-mid|)
        > required_clearance + distance_error_bound,

then that pair remains above the required clearance throughout the interval.
Unresolved intervals are bisected up to explicit limits; exhaustion is
``unknown``.  This module does not compute robot geometry or kinematic speed
bounds itself: those independent backend obligations are injected and their
evidence references are included in the certificate hash.
"""

from __future__ import annotations

import hashlib
import json
import math
from dataclasses import dataclass
from fractions import Fraction
from typing import Callable, Dict, Iterable, Optional, Tuple

from .explicit_state import (
    ActiveAction,
    IntervalAssessment,
    IntervalStatus,
    SearchState,
    UnknownKind,
)


EntityPair = Tuple[str, str]


def canonical_entity_pair(first: str, second: str) -> EntityPair:
    if not first or not second:
        raise ValueError("collision entity identifiers must be non-empty")
    if first == second:
        raise ValueError("collision pair must contain two distinct entities")
    return tuple(sorted((first, second)))


@dataclass(frozen=True)
class PairDistanceSample:
    entity_pair: EntityPair
    time_ns: int
    signed_distance_m: float
    evidence_ref: str

    def __post_init__(self) -> None:
        if self.entity_pair != canonical_entity_pair(*self.entity_pair):
            raise ValueError("distance sample entity pair must be canonical")
        if isinstance(self.time_ns, bool) or not isinstance(self.time_ns, int):
            raise ValueError("distance sample time_ns must be an integer")
        if self.time_ns < 0:
            raise ValueError("distance sample time_ns must be non-negative")
        if not math.isfinite(self.signed_distance_m):
            raise ValueError("signed distance must be finite")
        if not self.evidence_ref:
            raise ValueError("distance sample requires evidence_ref")


@dataclass(frozen=True)
class PairClosureSpeedBound:
    entity_pair: EntityPair
    interval_ns: Tuple[int, int]
    max_closure_speed_m_s: float
    evidence_ref: str

    def __post_init__(self) -> None:
        if self.entity_pair != canonical_entity_pair(*self.entity_pair):
            raise ValueError("speed-bound entity pair must be canonical")
        start_ns, end_ns = self.interval_ns
        if (
            isinstance(start_ns, bool)
            or isinstance(end_ns, bool)
            or not isinstance(start_ns, int)
            or not isinstance(end_ns, int)
            or start_ns < 0
            or end_ns <= start_ns
        ):
            raise ValueError("speed bound requires a positive integer-ns interval")
        if (
            not math.isfinite(self.max_closure_speed_m_s)
            or self.max_closure_speed_m_s < 0.0
        ):
            raise ValueError("max closure speed must be finite and non-negative")
        if not self.evidence_ref:
            raise ValueError("speed bound requires evidence_ref")


@dataclass(frozen=True)
class CollisionCertificatePolicy:
    # The semantic margin m_e in the property d > m_e.  The positive
    # certificate additionally absorbs collision_error_bound_m; callers must
    # not pre-inflate this field.
    required_clearance_m: float
    max_subdivisions: int
    min_interval_ns: int
    collision_error_bound_m: float = 0.0
    collision_error_evidence_ref: str = "assumption:exact-signed-distance"

    def __post_init__(self) -> None:
        if (
            not math.isfinite(self.required_clearance_m)
            or self.required_clearance_m < 0.0
        ):
            raise ValueError("required clearance must be finite and non-negative")
        if (
            isinstance(self.max_subdivisions, bool)
            or not isinstance(self.max_subdivisions, int)
            or self.max_subdivisions < 0
        ):
            raise ValueError("max_subdivisions must be a non-negative integer")
        if (
            isinstance(self.min_interval_ns, bool)
            or not isinstance(self.min_interval_ns, int)
            or self.min_interval_ns <= 0
        ):
            raise ValueError("min_interval_ns must be a positive integer")
        if (
            not math.isfinite(self.collision_error_bound_m)
            or self.collision_error_bound_m < 0.0
        ):
            raise ValueError("collision_error_bound_m must be finite and non-negative")
        if not self.collision_error_evidence_ref:
            raise ValueError("collision error bound requires evidence")

    @property
    def effective_required_clearance_m(self) -> float:
        """Outward-rounded threshold M_e used by the positive certificate."""

        return math.nextafter(
            self.required_clearance_m + self.collision_error_bound_m,
            math.inf,
        )


DistanceSampler = Callable[
    [SearchState, int, Tuple[ActiveAction, ...]], Iterable[PairDistanceSample]
]
ClosureSpeedBounder = Callable[
    [SearchState, int, int, Tuple[ActiveAction, ...]],
    Iterable[PairClosureSpeedBound],
]


@dataclass(frozen=True)
class _NodeResult:
    status: IntervalStatus
    reason: str
    interval_ns: Tuple[int, int]
    evidence_ref: str
    entity_pair: Optional[EntityPair] = None


def _canonical_json(value: dict) -> bytes:
    return (
        json.dumps(
            value,
            sort_keys=True,
            separators=(",", ":"),
            ensure_ascii=True,
            allow_nan=False,
        )
        + "\n"
    ).encode("ascii")


def _float_down(value: Fraction) -> float:
    converted = float(value)
    if math.isinf(converted):
        return converted
    if Fraction.from_float(converted) > value:
        return math.nextafter(converted, -math.inf)
    return converted


def _float_up(value: Fraction) -> float:
    converted = float(value)
    if math.isinf(converted):
        return converted
    if Fraction.from_float(converted) < value:
        return math.nextafter(converted, math.inf)
    return converted


class AdaptiveDistanceSpeedChecker:
    """An IntervalChecker with content-addressed proof/witness records."""

    def __init__(
        self,
        *,
        configured_pairs: Iterable[EntityPair],
        distance_sampler: DistanceSampler,
        closure_speed_bounder: ClosureSpeedBounder,
        policy: CollisionCertificatePolicy,
    ) -> None:
        pairs = tuple(sorted(canonical_entity_pair(*pair) for pair in configured_pairs))
        if not pairs:
            raise ValueError("at least one collision pair must be configured")
        if len(pairs) != len(set(pairs)):
            raise ValueError("configured collision pairs must be unique")
        self.configured_pairs = pairs
        self.distance_sampler = distance_sampler
        self.closure_speed_bounder = closure_speed_bounder
        self.policy = policy
        self._evidence_records: Dict[str, dict] = {}

    def evidence_record(self, evidence_ref: str) -> dict:
        """Return a defensive copy of one content-addressed proof record."""

        if evidence_ref not in self._evidence_records:
            raise KeyError(evidence_ref)
        return json.loads(json.dumps(self._evidence_records[evidence_ref]))

    def evidence_records(self) -> Tuple[Tuple[str, dict], ...]:
        return tuple(
            (reference, self.evidence_record(reference))
            for reference in sorted(self._evidence_records)
        )

    def _store(self, prefix: str, record: dict) -> str:
        digest = hashlib.sha256(_canonical_json(record)).hexdigest()
        reference = f"{prefix}:{digest}"
        self._evidence_records[reference] = record
        return reference

    def __call__(
        self,
        state: SearchState,
        start_ns: int,
        end_ns: int,
        active: Tuple[ActiveAction, ...],
    ) -> IntervalAssessment:
        if start_ns < 0 or end_ns <= start_ns:
            return IntervalAssessment(
                IntervalStatus.UNKNOWN,
                "continuous-collision-invalid-interval",
                (max(0, start_ns), max(max(0, start_ns) + 1, end_ns)),
                property_id="collision",
                witness_ref="collision-checker:invalid-interval",
            )
        try:
            result = self._certify(state, start_ns, end_ns, active, depth=0)
        except Exception as error:
            record = {
                "schema": "bisafecode.collision-certificate-node/v0.1",
                "status": "unknown",
                "reason": f"collision-backend-error:{type(error).__name__}",
                "interval_ns": [start_ns, end_ns],
            }
            reference = self._store("collision-unknown", record)
            return IntervalAssessment(
                IntervalStatus.UNKNOWN,
                record["reason"],
                (start_ns, end_ns),
                property_id="collision",
                witness_ref=reference,
                unknown_kind=(
                    UnknownKind.ANALYSIS_LIMIT
                    if isinstance(error, (RecursionError, MemoryError, TimeoutError))
                    else UnknownKind.NON_LIMIT
                ),
            )

        if result.status is IntervalStatus.SAFE:
            return IntervalAssessment(
                IntervalStatus.SAFE,
                result.reason,
                (start_ns, end_ns),
                certificate_ref=result.evidence_ref,
                property_id="collision",
            )
        if result.status is IntervalStatus.COLLISION:
            return IntervalAssessment(
                IntervalStatus.COLLISION,
                result.reason,
                result.interval_ns,
                entity_pair=result.entity_pair,
                property_id="collision",
                witness_ref=result.evidence_ref,
            )
        return IntervalAssessment(
            IntervalStatus.UNKNOWN,
            result.reason,
            (start_ns, end_ns),
            property_id="collision",
            witness_ref=result.evidence_ref,
            unknown_kind=(
                UnknownKind.ANALYSIS_LIMIT
                if result.reason == "continuous-collision-certificate-exhausted"
                else UnknownKind.NON_LIMIT
            ),
        )

    def _certify(
        self,
        state: SearchState,
        start_ns: int,
        end_ns: int,
        active: Tuple[ActiveAction, ...],
        *,
        depth: int,
    ) -> _NodeResult:
        midpoint_ns = start_ns + (end_ns - start_ns) // 2
        samples = tuple(self.distance_sampler(state, midpoint_ns, active))
        bounds = tuple(
            self.closure_speed_bounder(state, start_ns, end_ns, active)
        )
        sample_map = {sample.entity_pair: sample for sample in samples}
        bound_map = {bound.entity_pair: bound for bound in bounds}
        expected = set(self.configured_pairs)

        malformed_reason = None
        if len(sample_map) != len(samples):
            malformed_reason = "duplicate-distance-sample-pair"
        elif len(bound_map) != len(bounds):
            malformed_reason = "duplicate-speed-bound-pair"
        elif set(sample_map) != expected or set(bound_map) != expected:
            malformed_reason = "collision-pair-coverage-mismatch"
        elif any(sample.time_ns != midpoint_ns for sample in samples):
            malformed_reason = "distance-sample-time-mismatch"
        elif any(bound.interval_ns != (start_ns, end_ns) for bound in bounds):
            malformed_reason = "speed-bound-interval-mismatch"

        if malformed_reason is not None:
            record = {
                "schema": "bisafecode.collision-certificate-node/v0.1",
                "status": "unknown",
                "reason": malformed_reason,
                "interval_ns": [start_ns, end_ns],
                "depth": depth,
                "expected_pairs": [list(pair) for pair in self.configured_pairs],
                "sample_pairs": [list(pair) for pair in sorted(sample_map)],
                "bound_pairs": [list(pair) for pair in sorted(bound_map)],
            }
            reference = self._store("collision-unknown", record)
            return _NodeResult(
                IntervalStatus.UNKNOWN,
                malformed_reason,
                (start_ns, end_ns),
                reference,
            )

        semantic_clearance = Fraction.from_float(
            self.policy.required_clearance_m
        )
        point_violations = []
        for sample in samples:
            exact_upper = Fraction.from_float(sample.signed_distance_m) + Fraction.from_float(
                self.policy.collision_error_bound_m
            )
            outward_upper = _float_up(exact_upper)
            if Fraction.from_float(outward_upper) <= semantic_clearance:
                point_violations.append((sample, outward_upper))
        if point_violations:
            collision, signed_distance_upper = min(
                point_violations,
                key=lambda item: (item[1], item[0].entity_pair),
            )
            robust_penetration = signed_distance_upper < 0.0
            witness_reason = (
                "nonpositive-signed-distance-sample"
                if robust_penetration
                else "clearance-margin-breach-sample"
            )
            witness_end = min(end_ns, midpoint_ns + 1)
            if witness_end <= midpoint_ns:
                witness_start = max(start_ns, midpoint_ns - 1)
                witness_interval = (witness_start, midpoint_ns)
            else:
                witness_interval = (midpoint_ns, witness_end)
            record = {
                "schema": "bisafecode.collision-certificate-node/v0.1",
                "status": "collision-witness",
                "reason": witness_reason,
                "witness_subtype": (
                    "robust-penetration"
                    if robust_penetration
                    else "clearance-margin-breach"
                ),
                "interval_ns": [start_ns, end_ns],
                "witness_interval_ns": list(witness_interval),
                "sample_time_ns": midpoint_ns,
                "entity_pair": list(collision.entity_pair),
                "signed_distance_m": collision.signed_distance_m,
                "collision_error_bound_m": self.policy.collision_error_bound_m,
                "collision_error_evidence_ref": self.policy.collision_error_evidence_ref,
                "signed_distance_upper_bound_m": signed_distance_upper,
                "required_clearance_m": self.policy.required_clearance_m,
                "effective_required_clearance_m": (
                    self.policy.effective_required_clearance_m
                ),
                "distance_evidence_ref": collision.evidence_ref,
                "depth": depth,
            }
            reference = self._store("collision-witness", record)
            return _NodeResult(
                IntervalStatus.COLLISION,
                witness_reason,
                witness_interval,
                reference,
                collision.entity_pair,
            )

        temporal_radius_ns = max(midpoint_ns - start_ns, end_ns - midpoint_ns)
        effective_required = Fraction.from_float(
            self.policy.effective_required_clearance_m
        )
        pair_records = []
        all_safe = True
        for pair in self.configured_pairs:
            sample = sample_map[pair]
            bound = bound_map[pair]
            exact_lower = Fraction.from_float(sample.signed_distance_m) - (
                Fraction.from_float(bound.max_closure_speed_m_s)
                * Fraction(temporal_radius_ns, 1_000_000_000)
            )
            certified = exact_lower > effective_required
            all_safe = all_safe and certified
            pair_records.append(
                {
                    "entity_pair": list(pair),
                    "midpoint_signed_distance_m": sample.signed_distance_m,
                    "max_closure_speed_m_s": bound.max_closure_speed_m_s,
                    "temporal_radius_ns": temporal_radius_ns,
                    "distance_lower_bound_m": _float_down(exact_lower),
                    "required_clearance_m": self.policy.required_clearance_m,
                    "collision_error_bound_m": self.policy.collision_error_bound_m,
                    "effective_required_clearance_m": (
                        self.policy.effective_required_clearance_m
                    ),
                    "certified": certified,
                    "distance_evidence_ref": sample.evidence_ref,
                    "speed_evidence_ref": bound.evidence_ref,
                }
            )

        if all_safe:
            record = {
                "schema": "bisafecode.collision-certificate-node/v0.1",
                "status": "safe-certificate",
                "reason": "distance-speed-lower-bound",
                "interval_ns": [start_ns, end_ns],
                "sample_time_ns": midpoint_ns,
                "depth": depth,
                "pairs": pair_records,
            }
            reference = self._store("collision-safe", record)
            return _NodeResult(
                IntervalStatus.SAFE,
                "distance-speed-interval-certified",
                (start_ns, end_ns),
                reference,
            )

        width_ns = end_ns - start_ns
        split_ns = start_ns + width_ns // 2
        can_split = (
            depth < self.policy.max_subdivisions
            and width_ns > self.policy.min_interval_ns
            and start_ns < split_ns < end_ns
        )
        if not can_split:
            record = {
                "schema": "bisafecode.collision-certificate-node/v0.1",
                "status": "unknown",
                "reason": "continuous-collision-certificate-exhausted",
                "interval_ns": [start_ns, end_ns],
                "sample_time_ns": midpoint_ns,
                "depth": depth,
                "max_subdivisions": self.policy.max_subdivisions,
                "min_interval_ns": self.policy.min_interval_ns,
                "pairs": pair_records,
            }
            reference = self._store("collision-unknown", record)
            return _NodeResult(
                IntervalStatus.UNKNOWN,
                "continuous-collision-certificate-exhausted",
                (start_ns, end_ns),
                reference,
            )

        left = self._certify(state, start_ns, split_ns, active, depth=depth + 1)
        if left.status is IntervalStatus.COLLISION:
            return left
        right = self._certify(state, split_ns, end_ns, active, depth=depth + 1)
        if right.status is IntervalStatus.COLLISION:
            return right

        if left.status is IntervalStatus.SAFE and right.status is IntervalStatus.SAFE:
            status = IntervalStatus.SAFE
            reason = "distance-speed-subdivision-certified"
            prefix = "collision-safe"
        elif any(
            child.reason == "continuous-collision-certificate-exhausted"
            for child in (left, right)
        ):
            # Preserve the declared refinement-limit cause through internal
            # subdivision nodes.  A generic unresolved child (for example,
            # malformed or incomplete backend evidence) is not a limit.
            status = IntervalStatus.UNKNOWN
            reason = "continuous-collision-certificate-exhausted"
            prefix = "collision-unknown"
        else:
            status = IntervalStatus.UNKNOWN
            reason = "continuous-collision-subdivision-unresolved"
            prefix = "collision-unknown"
        record = {
            "schema": "bisafecode.collision-certificate-node/v0.1",
            "status": status.value,
            "reason": reason,
            "interval_ns": [start_ns, end_ns],
            "depth": depth,
            "children": [left.evidence_ref, right.evidence_ref],
        }
        reference = self._store(prefix, record)
        return _NodeResult(
            status,
            reason,
            (start_ns, end_ns),
            reference,
        )
