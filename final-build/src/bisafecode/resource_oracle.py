"""Independent ordered-event oracle for Shared-Fixture U/O/F properties.

The oracle derives logical owner U only from explicit acquire/release events.
Physical occupancy O and BASE_SEATED F are supplied as independently computed
observations.  It does not import or call the verifier.
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import Enum
from typing import Iterable, Optional


class ResourceArm(str, Enum):
    LEFT = "left"
    RIGHT = "right"


class ResourceEventKind(str, Enum):
    ACQUIRE = "acquire_resource"
    RELEASE = "release_resource"
    OBSERVE = "physical_observation"


class ResourceOracleLabel(str, Enum):
    SAFE = "safe"
    UNSAFE = "unsafe"
    INDETERMINATE = "indeterminate"


@dataclass(frozen=True)
class ResourceEvent:
    time_ns: int
    event_order: int
    kind: ResourceEventKind
    arm: Optional[ResourceArm] = None
    occupancy: Optional[frozenset[ResourceArm]] = None
    base_seated: Optional[bool] = None
    evidence_ref: Optional[str] = None

    def __post_init__(self) -> None:
        for name, value in (("time_ns", self.time_ns), ("event_order", self.event_order)):
            if isinstance(value, bool) or not isinstance(value, int) or value < 0:
                raise ValueError(f"{name} must be a non-negative integer")
        if self.kind is ResourceEventKind.ACQUIRE:
            if self.arm is None or self.occupancy is not None:
                raise ValueError("acquire requires arm and no occupancy snapshot")
        elif self.kind is ResourceEventKind.RELEASE:
            if self.arm is None or self.occupancy is None:
                raise ValueError("release requires arm and exact occupancy snapshot")
        elif self.kind is ResourceEventKind.OBSERVE:
            if self.arm is not None or self.occupancy is None:
                raise ValueError("physical observation requires occupancy and no arm")
        if self.occupancy is not None and any(
            not isinstance(arm, ResourceArm) for arm in self.occupancy
        ):
            raise ValueError("occupancy contains an invalid arm")
        if self.kind in {ResourceEventKind.RELEASE, ResourceEventKind.OBSERVE}:
            if not self.evidence_ref:
                raise ValueError("physical occupancy requires evidence_ref")


@dataclass(frozen=True)
class ResourceOracleWitness:
    event_index: int
    time_ns: int
    event_order: int
    reason: str
    owner_before: Optional[ResourceArm]
    event: ResourceEvent


@dataclass(frozen=True)
class ResourceOracleResult:
    label: ResourceOracleLabel
    final_owner: Optional[ResourceArm]
    processed_events: int
    witness: Optional[ResourceOracleWitness] = None


class InvalidResourceOracleTrace(ValueError):
    pass


def _result(
    label: ResourceOracleLabel,
    events: tuple[ResourceEvent, ...],
    index: int,
    reason: str,
    owner: Optional[ResourceArm],
) -> ResourceOracleResult:
    event = events[index]
    return ResourceOracleResult(
        label,
        owner,
        index,
        ResourceOracleWitness(
            index,
            event.time_ns,
            event.event_order,
            reason,
            owner,
            event,
        ),
    )


def monitor_resource_events(
    raw_events: Iterable[ResourceEvent],
    *,
    initial_owner: Optional[ResourceArm] = None,
    require_final_release: bool = True,
) -> ResourceOracleResult:
    events = tuple(raw_events)
    previous_key = None
    orders = set()
    for event in events:
        key = (event.time_ns, event.event_order)
        if previous_key is not None and key <= previous_key:
            raise InvalidResourceOracleTrace("events must be strictly ordered")
        if event.event_order in orders:
            raise InvalidResourceOracleTrace("event_order must be unique")
        previous_key = key
        orders.add(event.event_order)

    owner = initial_owner
    for index, event in enumerate(events):
        if event.kind is ResourceEventKind.ACQUIRE:
            if owner not in {None, event.arm}:
                return _result(
                    ResourceOracleLabel.UNSAFE,
                    events,
                    index,
                    "resource-owned-by-other-arm",
                    owner,
                )
            owner = event.arm
            continue

        if event.kind is ResourceEventKind.RELEASE:
            if owner is not event.arm:
                return _result(
                    ResourceOracleLabel.UNSAFE,
                    events,
                    index,
                    "resource-release-by-non-owner",
                    owner,
                )
            if event.arm in event.occupancy:
                return _result(
                    ResourceOracleLabel.UNSAFE,
                    events,
                    index,
                    "resource-release-before-physical-exit",
                    owner,
                )
            owner = None
            continue

        occupancy = event.occupancy
        if occupancy == frozenset({ResourceArm.LEFT, ResourceArm.RIGHT}):
            return _result(
                ResourceOracleLabel.UNSAFE,
                events,
                index,
                "simultaneous-fixture-occupancy",
                owner,
            )
        for occupying_arm in sorted(occupancy, key=lambda arm: arm.value):
            if owner is not occupying_arm:
                return _result(
                    ResourceOracleLabel.UNSAFE,
                    events,
                    index,
                    f"unauthorized-occupancy:{occupying_arm.value}",
                    owner,
                )
            if occupying_arm is ResourceArm.RIGHT:
                if event.base_seated is None:
                    return _result(
                        ResourceOracleLabel.INDETERMINATE,
                        events,
                        index,
                        "base-seated-undetermined",
                        owner,
                    )
                if event.base_seated is False:
                    return _result(
                        ResourceOracleLabel.UNSAFE,
                        events,
                        index,
                        "premature-stage-entry:right",
                        owner,
                    )

    if require_final_release and owner is not None:
        if not events:
            raise InvalidResourceOracleTrace(
                "cannot witness a held initial owner without any event"
            )
        return _result(
            ResourceOracleLabel.UNSAFE,
            events,
            len(events) - 1,
            f"resource-held-at-termination:{owner.value}",
            owner,
        )
    return ResourceOracleResult(
        ResourceOracleLabel.SAFE,
        owner,
        len(events),
    )
