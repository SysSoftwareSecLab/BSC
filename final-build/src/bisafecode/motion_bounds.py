"""Frozen-trajectory rate bounds and conservative entity speed envelopes.

This module connects cubic/quintic polynomial derivative bounds to the
distance--speed certificate interface.  Geometric envelope coefficients are
not invented here: a backend must supply a provenance-tagged, conservative
coefficient vector for every entity in the configured collision-pair scope.
"""

from __future__ import annotations

import hashlib
import json
import math
from dataclasses import dataclass
from fractions import Fraction
from typing import Callable, Dict, Iterable, Tuple

from .continuous_collision import (
    EntityPair,
    PairClosureSpeedBound,
    canonical_entity_pair,
)
from .explicit_state import ActiveAction, SearchEnvironment, SearchState
from .timed_ir import ActionKind, Arm
from .trajectory import DOF_PER_ARM


ZERO_ARM_RATE = (0.0,) * DOF_PER_ARM


def _float_up(value: Fraction) -> float:
    converted = float(value)
    if math.isinf(converted):
        return converted
    if Fraction.from_float(converted) < value:
        return math.nextafter(converted, math.inf)
    return converted


def _hash_record(prefix: str, record: dict) -> str:
    payload = (
        json.dumps(
            record,
            sort_keys=True,
            separators=(",", ":"),
            ensure_ascii=True,
            allow_nan=False,
        )
        + "\n"
    ).encode("ascii")
    return prefix + hashlib.sha256(payload).hexdigest()


@dataclass(frozen=True)
class FrozenRateBounds:
    interval_ns: Tuple[int, int]
    left_joint_rad_s: Tuple[float, ...] = ZERO_ARM_RATE
    right_joint_rad_s: Tuple[float, ...] = ZERO_ARM_RATE
    left_gripper_m_s: float = 0.0
    right_gripper_m_s: float = 0.0
    evidence_ref: str = ""

    def __post_init__(self) -> None:
        start_ns, end_ns = self.interval_ns
        if start_ns < 0 or end_ns <= start_ns:
            raise ValueError("rate bounds require a positive interval")
        for name, vector in (
            ("left_joint_rad_s", self.left_joint_rad_s),
            ("right_joint_rad_s", self.right_joint_rad_s),
        ):
            if len(vector) != DOF_PER_ARM:
                raise ValueError(f"{name} must have {DOF_PER_ARM} values")
            if any(not math.isfinite(value) or value < 0.0 for value in vector):
                raise ValueError(f"{name} must be finite and non-negative")
        for name, value in (
            ("left_gripper_m_s", self.left_gripper_m_s),
            ("right_gripper_m_s", self.right_gripper_m_s),
        ):
            if not math.isfinite(value) or value < 0.0:
                raise ValueError(f"{name} must be finite and non-negative")
        if not self.evidence_ref:
            raise ValueError("rate bounds require evidence_ref")


class FrozenTrajectoryRateProvider:
    """Extract exact polynomial rate enclosures for active frozen actions."""

    def __init__(self, environment: SearchEnvironment) -> None:
        self.trajectories = dict(environment.trajectories)
        self.gripper_trajectories = dict(environment.gripper_trajectories)

    def __call__(
        self,
        _state: SearchState,
        start_ns: int,
        end_ns: int,
        active: Tuple[ActiveAction, ...],
    ) -> FrozenRateBounds:
        if start_ns < 0 or end_ns <= start_ns:
            raise ValueError("rate query requires a positive interval")
        left_joint = ZERO_ARM_RATE
        right_joint = ZERO_ARM_RATE
        left_gripper = 0.0
        right_gripper = 0.0
        seen_arms = set()
        sources = []

        for action in active:
            if action.arm in seen_arms:
                raise ValueError("multiple active actions for one arm")
            seen_arms.add(action.arm)
            if not action.start_ns <= start_ns < end_ns <= action.end_ns:
                raise ValueError("rate query interval lies outside active action")
            local_start = start_ns - action.start_ns
            local_end = end_ns - action.start_ns

            if action.kind is ActionKind.MOVE:
                trajectory = self.trajectories.get(action.trajectory_hash)
                if trajectory is None:
                    raise KeyError("active move trajectory is unavailable")
                rates = trajectory.absolute_derivative_bounds(
                    1, local_start, local_end
                )
                if action.arm is Arm.LEFT:
                    left_joint = rates
                else:
                    right_joint = rates
                sources.append(
                    {
                        "arm": action.arm.value,
                        "kind": action.kind.value,
                        "trajectory_ref": action.trajectory_hash,
                        "local_interval_ns": [local_start, local_end],
                    }
                )
                continue

            if action.kind in {ActionKind.CLOSE, ActionKind.OPEN}:
                trajectory = self.gripper_trajectories.get(
                    action.gripper_trajectory_ref
                )
                if trajectory is None:
                    raise KeyError("active gripper trajectory is unavailable")
                rate = trajectory.absolute_derivative_bounds(
                    1, local_start, local_end
                )[0]
                if action.arm is Arm.LEFT:
                    left_gripper = rate
                else:
                    right_gripper = rate
                sources.append(
                    {
                        "arm": action.arm.value,
                        "kind": action.kind.value,
                        "trajectory_ref": action.gripper_trajectory_ref,
                        "local_interval_ns": [local_start, local_end],
                    }
                )
                continue

            if action.kind is ActionKind.WAIT:
                sources.append(
                    {
                        "arm": action.arm.value,
                        "kind": action.kind.value,
                        "trajectory_ref": None,
                        "local_interval_ns": [local_start, local_end],
                    }
                )
                continue
            raise ValueError(f"unsupported timed action in rate provider: {action.kind}")

        record = {
            "schema": "bisafecode.frozen-rate-bounds/v0.1",
            "interval_ns": [start_ns, end_ns],
            "sources": sorted(sources, key=lambda item: item["arm"]),
            "left_joint_rad_s": list(left_joint),
            "right_joint_rad_s": list(right_joint),
            "left_gripper_m_s": left_gripper,
            "right_gripper_m_s": right_gripper,
        }
        return FrozenRateBounds(
            (start_ns, end_ns),
            left_joint,
            right_joint,
            left_gripper,
            right_gripper,
            _hash_record("frozen-rate-bounds:", record),
        )


@dataclass(frozen=True)
class EntitySpeedEnvelope:
    entity_id: str
    left_joint_m_per_rad: Tuple[float, ...] = ZERO_ARM_RATE
    right_joint_m_per_rad: Tuple[float, ...] = ZERO_ARM_RATE
    left_gripper_m_per_m: float = 0.0
    right_gripper_m_per_m: float = 0.0
    evidence_ref: str = ""

    def __post_init__(self) -> None:
        if not self.entity_id:
            raise ValueError("entity speed envelope requires entity_id")
        for name, vector in (
            ("left_joint_m_per_rad", self.left_joint_m_per_rad),
            ("right_joint_m_per_rad", self.right_joint_m_per_rad),
        ):
            if len(vector) != DOF_PER_ARM:
                raise ValueError(f"{name} must have {DOF_PER_ARM} values")
            if any(not math.isfinite(value) or value < 0.0 for value in vector):
                raise ValueError(f"{name} must be finite and non-negative")
        for name, value in (
            ("left_gripper_m_per_m", self.left_gripper_m_per_m),
            ("right_gripper_m_per_m", self.right_gripper_m_per_m),
        ):
            if not math.isfinite(value) or value < 0.0:
                raise ValueError(f"{name} must be finite and non-negative")
        if not self.evidence_ref:
            raise ValueError("entity speed envelope requires evidence_ref")

    def speed_upper_bound(self, rates: FrozenRateBounds) -> float:
        exact = Fraction(0)
        for coefficient, rate in zip(
            self.left_joint_m_per_rad, rates.left_joint_rad_s
        ):
            exact += Fraction.from_float(coefficient) * Fraction.from_float(rate)
        for coefficient, rate in zip(
            self.right_joint_m_per_rad, rates.right_joint_rad_s
        ):
            exact += Fraction.from_float(coefficient) * Fraction.from_float(rate)
        exact += Fraction.from_float(self.left_gripper_m_per_m) * Fraction.from_float(
            rates.left_gripper_m_s
        )
        exact += Fraction.from_float(self.right_gripper_m_per_m) * Fraction.from_float(
            rates.right_gripper_m_s
        )
        return _float_up(exact)


EntityEnvelopeResolver = Callable[[SearchState, str], EntitySpeedEnvelope]


class EnvelopeClosureSpeedBounder:
    """Combine entity speed envelopes into pairwise closure-speed bounds."""

    def __init__(
        self,
        *,
        configured_pairs: Iterable[EntityPair],
        rate_provider: FrozenTrajectoryRateProvider,
        envelope_resolver: EntityEnvelopeResolver,
    ) -> None:
        pairs = tuple(sorted(canonical_entity_pair(*pair) for pair in configured_pairs))
        if not pairs or len(pairs) != len(set(pairs)):
            raise ValueError("configured pairs must be non-empty and unique")
        self.configured_pairs = pairs
        self.rate_provider = rate_provider
        self.envelope_resolver = envelope_resolver

    def __call__(
        self,
        state: SearchState,
        start_ns: int,
        end_ns: int,
        active: Tuple[ActiveAction, ...],
    ) -> Tuple[PairClosureSpeedBound, ...]:
        rates = self.rate_provider(state, start_ns, end_ns, active)
        cache: Dict[str, EntitySpeedEnvelope] = {}

        def envelope(entity_id: str) -> EntitySpeedEnvelope:
            if entity_id not in cache:
                value = self.envelope_resolver(state, entity_id)
                if value.entity_id != entity_id:
                    raise ValueError("envelope resolver returned wrong entity")
                cache[entity_id] = value
            return cache[entity_id]

        results = []
        for pair in self.configured_pairs:
            first = envelope(pair[0])
            second = envelope(pair[1])
            exact = Fraction.from_float(first.speed_upper_bound(rates)) + Fraction.from_float(
                second.speed_upper_bound(rates)
            )
            closure = _float_up(exact)
            record = {
                "schema": "bisafecode.pair-closure-speed-bound/v0.1",
                "entity_pair": list(pair),
                "interval_ns": [start_ns, end_ns],
                "max_closure_speed_m_s": closure,
                "rate_evidence_ref": rates.evidence_ref,
                "entity_evidence_refs": [first.evidence_ref, second.evidence_ref],
            }
            results.append(
                PairClosureSpeedBound(
                    pair,
                    (start_ns, end_ns),
                    closure,
                    _hash_record("pair-closure-speed:", record),
                )
            )
        return tuple(results)
