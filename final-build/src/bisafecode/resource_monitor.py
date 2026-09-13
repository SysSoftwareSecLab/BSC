"""Verifier-side Shared-Fixture U/O/F property adapters.

The physical observation functions are injected by the geometry backend.  This
module consumes their evidence and the verifier's logical owner U; it does not
infer occupancy from action names or trust program-declared task stage.
"""

from __future__ import annotations

import hashlib
from dataclasses import dataclass
from typing import Callable, Optional

from .explicit_state import (
    ActiveAction,
    IntervalAssessment,
    IntervalChecker,
    IntervalStatus,
    PointAssessment,
    PointChecker,
    SearchState,
)
from .timed_ir import ActionKind, ActionNode, Arm, TimedProgram


@dataclass(frozen=True)
class ResourcePointObservation:
    occupancy: Optional[frozenset[Arm]]
    base_seated: Optional[bool]
    evidence_ref: str

    def __post_init__(self) -> None:
        if not self.evidence_ref:
            raise ValueError("resource point observation requires evidence_ref")
        if self.occupancy is not None and any(
            not isinstance(arm, Arm) for arm in self.occupancy
        ):
            raise ValueError("resource point occupancy contains invalid arm")


@dataclass(frozen=True)
class ResourceIntervalObservation:
    interval_ns: tuple[int, int]
    occupancy_may: Optional[frozenset[Arm]]
    simultaneous_occupancy_may: Optional[bool]
    base_seated_entire_interval: Optional[bool]
    evidence_ref: str
    witness_interval_ns: Optional[tuple[int, int]] = None

    def __post_init__(self) -> None:
        start, end = self.interval_ns
        if start < 0 or end <= start:
            raise ValueError("resource interval observation requires positive interval")
        if not self.evidence_ref:
            raise ValueError("resource interval observation requires evidence_ref")
        if self.occupancy_may is not None and any(
            not isinstance(arm, Arm) for arm in self.occupancy_may
        ):
            raise ValueError("resource interval occupancy contains invalid arm")
        if self.witness_interval_ns is not None:
            witness_start, witness_end = self.witness_interval_ns
            if not (start <= witness_start < witness_end <= end):
                raise ValueError("resource witness interval lies outside observation")


ResourcePointObserver = Callable[[SearchState], ResourcePointObservation]
ResourceIntervalObserver = Callable[
    [SearchState, int, int, tuple[ActiveAction, ...]],
    ResourceIntervalObservation,
]


def _owner(state: SearchState, resource_id: str) -> Optional[Arm]:
    resources = dict(state.world.resources)
    if resource_id not in resources:
        raise KeyError(f"unknown resource in monitor: {resource_id}")
    return resources[resource_id]


def _safe_ref(prefix: str, *parts: str) -> str:
    payload = "|".join(parts).encode("utf-8")
    return prefix + hashlib.sha256(payload).hexdigest()


def build_resource_point_checker(
    program: TimedProgram,
    *,
    resource_id: str,
    observer: ResourcePointObserver,
) -> PointChecker:
    if resource_id not in program.resource_ids:
        raise ValueError(f"program does not declare resource {resource_id}")
    nodes = {node.node_id: node for node in program.nodes}

    def checker(state: SearchState) -> PointAssessment:
        observation = observer(state)
        owner = _owner(state, resource_id)
        if observation.occupancy is None:
            return PointAssessment(
                IntervalStatus.UNKNOWN,
                "fixture-occupancy-undetermined",
                state.time_ns,
                property_id="resource",
                witness_ref=observation.evidence_ref,
            )
        occupancy = observation.occupancy
        witness_ref = observation.evidence_ref

        for node_id in (state.pc_main, state.pc_left, state.pc_right):
            if node_id is None:
                continue
            node = nodes[node_id]
            if (
                isinstance(node, ActionNode)
                and node.action.kind is ActionKind.RELEASE
                and node.action.resource_id == resource_id
                and node.action.arm in occupancy
            ):
                return PointAssessment(
                    IntervalStatus.VIOLATION,
                    "resource-release-before-physical-exit",
                    state.time_ns,
                    property_id="resource",
                    witness_ref=witness_ref,
                )

        if occupancy == frozenset({Arm.LEFT, Arm.RIGHT}):
            return PointAssessment(
                IntervalStatus.VIOLATION,
                "simultaneous-fixture-occupancy",
                state.time_ns,
                property_id="resource",
                witness_ref=witness_ref,
            )
        for occupying_arm in sorted(occupancy, key=lambda arm: arm.value):
            if owner is not occupying_arm:
                return PointAssessment(
                    IntervalStatus.VIOLATION,
                    f"unauthorized-occupancy:{occupying_arm.value}",
                    state.time_ns,
                    property_id="resource",
                    witness_ref=witness_ref,
                )
            if occupying_arm is Arm.RIGHT:
                if observation.base_seated is None:
                    return PointAssessment(
                        IntervalStatus.UNKNOWN,
                        "base-seated-undetermined",
                        state.time_ns,
                        property_id="resource-stage",
                        witness_ref=witness_ref,
                    )
                if observation.base_seated is False:
                    return PointAssessment(
                        IntervalStatus.VIOLATION,
                        "premature-stage-entry:right",
                        state.time_ns,
                        property_id="resource-stage",
                        witness_ref=witness_ref,
                    )
        return PointAssessment(
            IntervalStatus.SAFE,
            "resource-point-certified",
            state.time_ns,
            certificate_ref=_safe_ref(
                "resource-point:",
                witness_ref,
                owner.value if owner is not None else "none",
                ",".join(sorted(arm.value for arm in occupancy)),
                str(observation.base_seated),
            ),
            property_id="resource",
        )

    return checker


def build_resource_interval_checker(
    program: TimedProgram,
    *,
    resource_id: str,
    observer: ResourceIntervalObserver,
) -> IntervalChecker:
    if resource_id not in program.resource_ids:
        raise ValueError(f"program does not declare resource {resource_id}")

    def checker(
        state: SearchState,
        start_ns: int,
        end_ns: int,
        active: tuple[ActiveAction, ...],
    ) -> IntervalAssessment:
        observation = observer(state, start_ns, end_ns, active)
        if observation.interval_ns != (start_ns, end_ns):
            return IntervalAssessment(
                IntervalStatus.UNKNOWN,
                "resource-observer-interval-mismatch",
                (start_ns, end_ns),
                property_id="resource",
                witness_ref=observation.evidence_ref,
            )
        owner = _owner(state, resource_id)
        if observation.occupancy_may is None:
            return IntervalAssessment(
                IntervalStatus.UNKNOWN,
                "fixture-occupancy-interval-undetermined",
                (start_ns, end_ns),
                property_id="resource",
                witness_ref=observation.evidence_ref,
            )
        if observation.simultaneous_occupancy_may is None:
            return IntervalAssessment(
                IntervalStatus.UNKNOWN,
                "simultaneous-occupancy-undetermined",
                (start_ns, end_ns),
                property_id="resource",
                witness_ref=observation.evidence_ref,
            )
        witness_interval = observation.witness_interval_ns or (start_ns, end_ns)
        if observation.simultaneous_occupancy_may:
            return IntervalAssessment(
                IntervalStatus.VIOLATION,
                "simultaneous-fixture-occupancy",
                witness_interval,
                property_id="resource",
                witness_ref=observation.evidence_ref,
            )
        for occupying_arm in sorted(
            observation.occupancy_may, key=lambda arm: arm.value
        ):
            if owner is not occupying_arm:
                return IntervalAssessment(
                    IntervalStatus.VIOLATION,
                    f"unauthorized-occupancy:{occupying_arm.value}",
                    witness_interval,
                    property_id="resource",
                    witness_ref=observation.evidence_ref,
                )
            if occupying_arm is Arm.RIGHT:
                if observation.base_seated_entire_interval is None:
                    return IntervalAssessment(
                        IntervalStatus.UNKNOWN,
                        "base-seated-interval-undetermined",
                        (start_ns, end_ns),
                        property_id="resource-stage",
                        witness_ref=observation.evidence_ref,
                    )
                if observation.base_seated_entire_interval is False:
                    return IntervalAssessment(
                        IntervalStatus.VIOLATION,
                        "premature-stage-entry:right",
                        witness_interval,
                        property_id="resource-stage",
                        witness_ref=observation.evidence_ref,
                    )
        return IntervalAssessment(
            IntervalStatus.SAFE,
            "resource-interval-certified",
            (start_ns, end_ns),
            certificate_ref=_safe_ref(
                "resource-interval:",
                observation.evidence_ref,
                owner.value if owner is not None else "none",
                ",".join(
                    sorted(arm.value for arm in observation.occupancy_may)
                ),
                str(observation.base_seated_entire_interval),
            ),
            property_id="resource",
        )

    return checker
