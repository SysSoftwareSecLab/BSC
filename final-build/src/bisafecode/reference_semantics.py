"""Executable reference semantics used to freeze Stage 1 event ordering.

This module is intentionally smaller than the Stage 2 CFG/BMC engine.  It
checks the disputed state invariants and event rules on canonical hand traces;
it must not be described as a complete parser, successor generator, or BMC.
"""

from __future__ import annotations

from dataclasses import dataclass, replace
from enum import Enum
from typing import Callable, FrozenSet, Optional, Tuple


class StepStatus(str, Enum):
    OK = "ok"
    VIOLATED = "violated"
    UNKNOWN = "unknown"
    INVALID = "invalid"


class IntervalStatus(str, Enum):
    SAFE = "safe-certificate"
    COLLISION = "collision-witness"
    UNKNOWN = "unresolved"


class ProtocolPhase(str, Enum):
    FREE = "free"
    SENDER_ONLY = "sender-only"
    DUAL_PRE_TRANSFER = "dual-pre-transfer"
    DUAL_POST_TRANSFER = "dual-post-transfer"
    RECEIVER_ONLY = "receiver-only"


VALID_ARMS = frozenset({"left", "right"})


@dataclass(frozen=True)
class SourceRef:
    file: str
    line: int
    column: int = 0


@dataclass(frozen=True)
class ActiveAction:
    node_id: str
    arm: str
    kind: str
    start_ns: int
    end_ns: int
    source_ref: SourceRef
    carrying_object: Optional[str] = None

    def __post_init__(self) -> None:
        if self.arm not in VALID_ARMS:
            raise ValueError(f"unknown arm: {self.arm}")
        if self.end_ns <= self.start_ns:
            raise ValueError("timed actions require positive duration")


@dataclass(frozen=True)
class ObjectProtocol:
    name: str
    grasps: FrozenSet[str] = frozenset()
    authority: Optional[str] = None
    phase: ProtocolPhase = ProtocolPhase.FREE
    sender: Optional[str] = None
    receiver: Optional[str] = None
    attachments_consistent: Optional[bool] = None

    def invariant_error(self) -> Optional[str]:
        if not self.grasps and self.authority is not None:
            return "authority-without-grasp"
        if self.authority is not None and self.authority not in self.grasps:
            return "authority-holder-not-grasping"
        if not self.grasps and self.phase is not ProtocolPhase.FREE:
            return "empty-grasp-outside-free-phase"
        if len(self.grasps) == 2 and self.phase not in {
            ProtocolPhase.DUAL_PRE_TRANSFER,
            ProtocolPhase.DUAL_POST_TRANSFER,
        }:
            return "dual-grasp-outside-handover"
        return None


@dataclass(frozen=True)
class BarrierWait:
    barrier_id: str
    expected: FrozenSet[str]
    arrived: FrozenSet[str] = frozenset()


@dataclass(frozen=True)
class ReferenceState:
    time_ns: int = 0
    active: Tuple[ActiveAction, ...] = ()
    resources: Tuple[Tuple[str, Optional[str]], ...] = ()
    objects: Tuple[ObjectProtocol, ...] = ()
    barriers: Tuple[BarrierWait, ...] = ()
    released_barriers: FrozenSet[str] = frozenset()
    completion_batches: Tuple[Tuple[int, Tuple[str, ...]], ...] = ()

    def invariant_error(self) -> Optional[str]:
        if self.time_ns < 0:
            return "negative-time"
        active_arms = [action.arm for action in self.active]
        if len(active_arms) != len(set(active_arms)):
            return "multiple-active-actions-on-one-arm"
        if any(action.start_ns > self.time_ns for action in self.active):
            return "active-action-starts-in-future"
        for name, owner in self.resources:
            if not name:
                return "empty-resource-name"
            if owner is not None and owner not in VALID_ARMS:
                return "invalid-resource-owner"
        for obj in self.objects:
            error = obj.invariant_error()
            if error:
                return f"{obj.name}:{error}"
        return None


@dataclass(frozen=True)
class IntervalAssessment:
    status: IntervalStatus
    reason: str
    pair: Optional[Tuple[str, str]] = None
    interval_ns: Optional[Tuple[int, int]] = None


@dataclass(frozen=True)
class StepResult:
    status: StepStatus
    state: ReferenceState
    reason: Optional[str] = None
    source_ref: Optional[SourceRef] = None
    interval: Optional[IntervalAssessment] = None


IntervalChecker = Callable[[ReferenceState, int, int], IntervalAssessment]


def _state_result(state: ReferenceState) -> StepResult:
    error = state.invariant_error()
    if error:
        return StepResult(StepStatus.INVALID, state, error)
    return StepResult(StepStatus.OK, state)


def _resource_map(state: ReferenceState) -> dict[str, Optional[str]]:
    return dict(state.resources)


def _object_map(state: ReferenceState) -> dict[str, ObjectProtocol]:
    return {obj.name: obj for obj in state.objects}


def _replace_resource(state: ReferenceState, resource: str, owner: Optional[str]) -> ReferenceState:
    resources = _resource_map(state)
    resources[resource] = owner
    return replace(state, resources=tuple(sorted(resources.items())))


def _replace_object(state: ReferenceState, obj: ObjectProtocol) -> ReferenceState:
    objects = _object_map(state)
    objects[obj.name] = obj
    return replace(state, objects=tuple(sorted(objects.values(), key=lambda item: item.name)))


def acquire_resource(
    state: ReferenceState,
    arm: str,
    resource: str,
    source_ref: SourceRef,
) -> StepResult:
    if arm not in VALID_ARMS or resource not in _resource_map(state):
        return StepResult(StepStatus.INVALID, state, "invalid-resource-reference", source_ref)
    owner = _resource_map(state)[resource]
    if owner not in {None, arm}:
        return StepResult(StepStatus.VIOLATED, state, "resource-owned-by-other-arm", source_ref)
    return _state_result(_replace_resource(state, resource, arm))


def release_resource(
    state: ReferenceState,
    arm: str,
    resource: str,
    source_ref: SourceRef,
) -> StepResult:
    if arm not in VALID_ARMS or resource not in _resource_map(state):
        return StepResult(StepStatus.INVALID, state, "invalid-resource-reference", source_ref)
    if _resource_map(state)[resource] != arm:
        return StepResult(StepStatus.VIOLATED, state, "resource-release-by-non-owner", source_ref)
    return _state_result(_replace_resource(state, resource, None))


def grasp_free_object(
    state: ReferenceState,
    obj_name: str,
    arm: str,
    source_ref: SourceRef,
) -> StepResult:
    objects = _object_map(state)
    if arm not in VALID_ARMS or obj_name not in objects:
        return StepResult(StepStatus.INVALID, state, "invalid-grasp-reference", source_ref)
    obj = objects[obj_name]
    if obj.phase is not ProtocolPhase.FREE or obj.grasps:
        return StepResult(StepStatus.VIOLATED, state, "object-not-free", source_ref)
    updated = replace(
        obj,
        grasps=frozenset({arm}),
        authority=arm,
        phase=ProtocolPhase.SENDER_ONLY,
        sender=arm,
        receiver=None,
        attachments_consistent=True,
    )
    return _state_result(_replace_object(state, updated))


def receiver_grasp(
    state: ReferenceState,
    obj_name: str,
    receiver: str,
    source_ref: SourceRef,
    *,
    in_handover_zone: Optional[bool],
    grasp_success: Optional[bool],
    attachments_consistent: Optional[bool],
) -> StepResult:
    objects = _object_map(state)
    if receiver not in VALID_ARMS or obj_name not in objects:
        return StepResult(StepStatus.INVALID, state, "invalid-grasp-reference", source_ref)
    obj = objects[obj_name]
    if obj.phase is not ProtocolPhase.SENDER_ONLY or obj.sender is None:
        return StepResult(StepStatus.VIOLATED, state, "receiver-grasp-outside-handover", source_ref)
    if receiver == obj.sender:
        return StepResult(StepStatus.INVALID, state, "sender-equals-receiver", source_ref)
    facts = {
        "handover-zone-undetermined": in_handover_zone,
        "grasp-outcome-undetermined": grasp_success,
        "attachment-consistency-undetermined": attachments_consistent,
    }
    for reason, value in facts.items():
        if value is None:
            return StepResult(StepStatus.UNKNOWN, state, reason, source_ref)
    if not in_handover_zone:
        return StepResult(StepStatus.VIOLATED, state, "receiver-grasp-outside-zone", source_ref)
    if not grasp_success:
        return StepResult(StepStatus.VIOLATED, state, "receiver-grasp-failed", source_ref)
    if not attachments_consistent:
        return StepResult(StepStatus.VIOLATED, state, "inconsistent-dual-attachment", source_ref)
    updated = replace(
        obj,
        grasps=frozenset({obj.sender, receiver}),
        receiver=receiver,
        phase=ProtocolPhase.DUAL_PRE_TRANSFER,
        attachments_consistent=True,
    )
    return _state_result(_replace_object(state, updated))


def transfer_authority(
    state: ReferenceState,
    obj_name: str,
    sender: str,
    receiver: str,
    source_ref: SourceRef,
    *,
    in_handover_zone: Optional[bool],
    synchronized: Optional[bool],
    arms_static: Optional[bool],
) -> StepResult:
    objects = _object_map(state)
    if sender not in VALID_ARMS or receiver not in VALID_ARMS or obj_name not in objects:
        return StepResult(StepStatus.INVALID, state, "invalid-transfer-reference", source_ref)
    if sender == receiver:
        return StepResult(StepStatus.INVALID, state, "sender-equals-receiver", source_ref)
    obj = objects[obj_name]
    if (
        obj.phase is not ProtocolPhase.DUAL_PRE_TRANSFER
        or obj.sender != sender
        or obj.receiver != receiver
        or obj.grasps != frozenset({sender, receiver})
        or obj.authority != sender
    ):
        return StepResult(StepStatus.VIOLATED, state, "illegal-authority-transfer", source_ref)
    for reason, value in (
        ("handover-zone-undetermined", in_handover_zone),
        ("synchronization-undetermined", synchronized),
        ("static-dual-grasp-undetermined", arms_static),
    ):
        if value is None:
            return StepResult(StepStatus.UNKNOWN, state, reason, source_ref)
        if not value:
            return StepResult(StepStatus.VIOLATED, state, "illegal-authority-transfer", source_ref)
    if obj.attachments_consistent is not True:
        status = StepStatus.UNKNOWN if obj.attachments_consistent is None else StepStatus.VIOLATED
        return StepResult(status, state, "attachment-consistency-undetermined", source_ref)
    updated = replace(obj, authority=receiver, phase=ProtocolPhase.DUAL_POST_TRANSFER)
    return _state_result(_replace_object(state, updated))


def release_sender(
    state: ReferenceState,
    obj_name: str,
    sender: str,
    source_ref: SourceRef,
) -> StepResult:
    objects = _object_map(state)
    if sender not in VALID_ARMS or obj_name not in objects:
        return StepResult(StepStatus.INVALID, state, "invalid-release-reference", source_ref)
    obj = objects[obj_name]
    if obj.phase is not ProtocolPhase.DUAL_POST_TRANSFER or obj.sender != sender:
        return StepResult(StepStatus.VIOLATED, state, "sender-release-before-transfer", source_ref)
    if obj.receiver is None or obj.authority != obj.receiver:
        return StepResult(StepStatus.VIOLATED, state, "receiver-lacks-authority", source_ref)
    updated = replace(
        obj,
        grasps=frozenset({obj.receiver}),
        phase=ProtocolPhase.RECEIVER_ONLY,
        sender=None,
        attachments_consistent=True,
    )
    return _state_result(_replace_object(state, updated))


def start_timed_action(state: ReferenceState, action: ActiveAction) -> StepResult:
    if action.start_ns != state.time_ns:
        return StepResult(StepStatus.INVALID, state, "action-start-time-mismatch", action.source_ref)
    if any(current.arm == action.arm for current in state.active):
        return StepResult(StepStatus.INVALID, state, "arm-already-active", action.source_ref)
    if action.carrying_object is not None:
        obj = _object_map(state).get(action.carrying_object)
        if obj is None:
            return StepResult(StepStatus.INVALID, state, "unknown-carried-object", action.source_ref)
        if len(obj.grasps) == 2 and action.kind == "move":
            return StepResult(StepStatus.VIOLATED, state, "dual-grasp-motion-unsupported", action.source_ref)
        if obj.grasps != frozenset({action.arm}) or obj.authority != action.arm:
            return StepResult(StepStatus.VIOLATED, state, "unauthorized-object-motion", action.source_ref)
    return _state_result(replace(state, active=state.active + (action,)))


def elapse_to_next_event(state: ReferenceState, checker: IntervalChecker) -> StepResult:
    if not state.active:
        return StepResult(StepStatus.INVALID, state, "time-elapse-without-active-action")
    next_time = min(action.end_ns for action in state.active)
    assessment = checker(state, state.time_ns, next_time)
    if assessment.status is IntervalStatus.COLLISION:
        return StepResult(
            StepStatus.VIOLATED,
            state,
            assessment.reason,
            interval=assessment,
        )
    if assessment.status is IntervalStatus.UNKNOWN:
        return StepResult(
            StepStatus.UNKNOWN,
            state,
            assessment.reason,
            interval=assessment,
        )
    completed = tuple(sorted(action.node_id for action in state.active if action.end_ns == next_time))
    remaining = tuple(action for action in state.active if action.end_ns != next_time)
    updated = replace(
        state,
        time_ns=next_time,
        active=remaining,
        completion_batches=state.completion_batches + ((next_time, completed),),
    )
    return _state_result(updated)


def arrive_barrier(
    state: ReferenceState,
    barrier_id: str,
    participant: str,
    expected: FrozenSet[str],
) -> StepResult:
    if participant not in expected or not expected or not expected.issubset(VALID_ARMS):
        return StepResult(StepStatus.INVALID, state, "invalid-barrier-participants")
    barriers = {item.barrier_id: item for item in state.barriers}
    wait = barriers.get(barrier_id, BarrierWait(barrier_id, expected))
    if wait.expected != expected or participant in wait.arrived:
        return StepResult(StepStatus.INVALID, state, "malformed-barrier-arrival")
    arrived = wait.arrived | {participant}
    released = state.released_barriers
    if arrived == expected:
        barriers.pop(barrier_id, None)
        released = released | {barrier_id}
    else:
        barriers[barrier_id] = replace(wait, arrived=frozenset(arrived))
    updated = replace(
        state,
        barriers=tuple(sorted(barriers.values(), key=lambda item: item.barrier_id)),
        released_barriers=released,
    )
    return _state_result(updated)
