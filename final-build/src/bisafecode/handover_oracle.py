"""Independent event-trace oracle for the handover protocol.

This module deliberately does not import the Timed IR or explicit-state
engine.  It consumes an ordered trace of physical/protocol events and returns
the first protocol violation.  It is an oracle for handover event ordering,
not for collision, grasp sensing, or continuous geometry.
"""

from __future__ import annotations

from dataclasses import dataclass, replace
from enum import Enum
from typing import Iterable, Optional


class OracleArm(str, Enum):
    LEFT = "left"
    RIGHT = "right"


class HandoverEventKind(str, Enum):
    GRASP_SUCCESS = "grasp_success"
    RELEASE_COMMAND = "release_command"
    RELEASE_SUCCESS = "release_success"
    TRANSFER_AUTHORITY = "transfer_authority"


class OraclePhase(str, Enum):
    FREE = "free"
    SINGLE = "single"
    DUAL_PRE = "dual-pre-transfer"
    DUAL_POST = "dual-post-transfer"


class HandoverOracleLabel(str, Enum):
    SAFE = "safe"
    UNSAFE = "unsafe"
    INDETERMINATE = "indeterminate"


@dataclass(frozen=True)
class HandoverEvent:
    time_ns: int
    event_order: int
    kind: HandoverEventKind
    object_id: str
    arm: Optional[OracleArm] = None
    sender: Optional[OracleArm] = None
    receiver: Optional[OracleArm] = None
    in_handover_zone: Optional[bool] = None
    attachments_consistent: Optional[bool] = None
    synchronized: Optional[bool] = None
    arms_static: Optional[bool] = None
    supported_after_release: Optional[bool] = None

    def __post_init__(self) -> None:
        for name, value in (("time_ns", self.time_ns), ("event_order", self.event_order)):
            if isinstance(value, bool) or not isinstance(value, int) or value < 0:
                raise ValueError(f"{name} must be a non-negative integer")
        if not self.object_id:
            raise ValueError("handover oracle event requires object_id")
        if self.kind in {
            HandoverEventKind.GRASP_SUCCESS,
            HandoverEventKind.RELEASE_COMMAND,
            HandoverEventKind.RELEASE_SUCCESS,
        }:
            if self.arm is None or self.sender is not None or self.receiver is not None:
                raise ValueError("grasp/release event requires only arm")
        elif self.kind is HandoverEventKind.TRANSFER_AUTHORITY:
            if (
                self.arm is not None
                or self.sender is None
                or self.receiver is None
                or self.sender is self.receiver
            ):
                raise ValueError("transfer event requires distinct sender and receiver")


@dataclass(frozen=True)
class HandoverOracleState:
    phase: OraclePhase = OraclePhase.FREE
    holders: frozenset[OracleArm] = frozenset()
    authority: Optional[OracleArm] = None
    sender: Optional[OracleArm] = None
    receiver: Optional[OracleArm] = None

    def invariant_issue(self) -> Optional[str]:
        if self.phase is OraclePhase.FREE:
            if self.holders or self.authority is not None:
                return "free-state-has-holder-or-authority"
            if self.sender is not None or self.receiver is not None:
                return "free-state-has-protocol-participant"
        elif self.phase is OraclePhase.SINGLE:
            if len(self.holders) != 1 or self.authority not in self.holders:
                return "malformed-single-state"
            if self.receiver is None:
                if self.sender is not self.authority:
                    return "malformed-sender-single-state"
            elif self.sender is not None or self.receiver is not self.authority:
                return "malformed-receiver-single-state"
        elif self.phase in {OraclePhase.DUAL_PRE, OraclePhase.DUAL_POST}:
            if (
                self.sender is None
                or self.receiver is None
                or self.sender is self.receiver
                or self.holders != frozenset({self.sender, self.receiver})
            ):
                return "malformed-dual-state"
            expected = (
                self.sender if self.phase is OraclePhase.DUAL_PRE else self.receiver
            )
            if self.authority is not expected:
                return "dual-authority-phase-mismatch"
        return None


@dataclass(frozen=True)
class HandoverOracleWitness:
    event_index: int
    time_ns: int
    event_order: int
    reason: str
    event: HandoverEvent
    state_before: HandoverOracleState


@dataclass(frozen=True)
class HandoverOracleResult:
    label: HandoverOracleLabel
    final_state: HandoverOracleState
    processed_events: int
    witness: Optional[HandoverOracleWitness] = None


class InvalidHandoverOracleTrace(ValueError):
    pass


def _violation(
    events: tuple[HandoverEvent, ...],
    index: int,
    reason: str,
    state: HandoverOracleState,
) -> HandoverOracleResult:
    event = events[index]
    return HandoverOracleResult(
        HandoverOracleLabel.UNSAFE,
        state,
        index,
        HandoverOracleWitness(
            index,
            event.time_ns,
            event.event_order,
            reason,
            event,
            state,
        ),
    )


def _indeterminate(
    events: tuple[HandoverEvent, ...],
    index: int,
    reason: str,
    state: HandoverOracleState,
) -> HandoverOracleResult:
    event = events[index]
    return HandoverOracleResult(
        HandoverOracleLabel.INDETERMINATE,
        state,
        index,
        HandoverOracleWitness(
            index,
            event.time_ns,
            event.event_order,
            reason,
            event,
            state,
        ),
    )


def monitor_handover_events(
    raw_events: Iterable[HandoverEvent],
    *,
    object_id: str,
) -> HandoverOracleResult:
    """Evaluate one object's ordered event trace with no verifier dependencies."""

    events = tuple(raw_events)
    if not object_id:
        raise InvalidHandoverOracleTrace("object_id is required")
    previous_key = None
    orders = set()
    for event in events:
        if event.object_id != object_id:
            raise InvalidHandoverOracleTrace("trace contains a different object")
        key = (event.time_ns, event.event_order)
        if previous_key is not None and key <= previous_key:
            raise InvalidHandoverOracleTrace("events must be strictly ordered")
        if event.event_order in orders:
            raise InvalidHandoverOracleTrace("event_order must be unique")
        previous_key = key
        orders.add(event.event_order)

    state = HandoverOracleState()
    for index, event in enumerate(events):
        issue = state.invariant_issue()
        if issue:
            raise InvalidHandoverOracleTrace(f"oracle state invariant failed: {issue}")

        if event.kind is HandoverEventKind.GRASP_SUCCESS:
            if state.phase is OraclePhase.FREE:
                state = HandoverOracleState(
                    phase=OraclePhase.SINGLE,
                    holders=frozenset({event.arm}),
                    authority=event.arm,
                    sender=event.arm,
                )
                continue
            if state.phase is OraclePhase.SINGLE and state.receiver is None:
                if event.arm in state.holders:
                    return _violation(events, index, "duplicate-grasp", state)
                if event.in_handover_zone is None:
                    return _indeterminate(
                        events, index, "handover-zone-undetermined", state
                    )
                if event.in_handover_zone is False:
                    return _violation(
                        events, index, "receiver-grasp-outside-zone", state
                    )
                if event.attachments_consistent is None:
                    return _indeterminate(
                        events, index, "attachment-consistency-undetermined", state
                    )
                if event.attachments_consistent is False:
                    return _violation(
                        events, index, "inconsistent-dual-attachment", state
                    )
                state = replace(
                    state,
                    phase=OraclePhase.DUAL_PRE,
                    holders=frozenset({state.sender, event.arm}),
                    receiver=event.arm,
                )
                continue
            return _violation(events, index, "grasp-outside-handover-protocol", state)

        if event.kind is HandoverEventKind.TRANSFER_AUTHORITY:
            if (
                state.phase is not OraclePhase.DUAL_PRE
                or state.sender is not event.sender
                or state.receiver is not event.receiver
                or state.authority is not event.sender
            ):
                return _violation(events, index, "illegal-authority-transfer", state)
            for value, unknown_reason in (
                (event.in_handover_zone, "handover-zone-undetermined"),
                (event.synchronized, "synchronization-undetermined"),
                (event.arms_static, "static-dual-grasp-undetermined"),
            ):
                if value is None:
                    return _indeterminate(events, index, unknown_reason, state)
                if value is False:
                    return _violation(
                        events, index, "illegal-authority-transfer", state
                    )
            state = replace(
                state,
                phase=OraclePhase.DUAL_POST,
                authority=event.receiver,
            )
            continue

        if event.arm not in state.holders:
            return _violation(events, index, "release-by-non-holder", state)
        if state.phase is OraclePhase.DUAL_PRE:
            if event.arm is state.sender:
                return _violation(events, index, "sender-release-before-transfer", state)
            return _violation(events, index, "receiver-release-during-handover", state)
        if state.phase is OraclePhase.DUAL_POST:
            if event.arm is not state.sender:
                return _violation(events, index, "receiver-release-during-handover", state)
            if event.kind is HandoverEventKind.RELEASE_COMMAND:
                continue
            state = HandoverOracleState(
                phase=OraclePhase.SINGLE,
                holders=frozenset({state.receiver}),
                authority=state.receiver,
                receiver=state.receiver,
            )
            continue
        if event.kind is HandoverEventKind.RELEASE_COMMAND:
            continue
        if event.supported_after_release is None:
            return _indeterminate(
                events, index, "release-support-undetermined", state
            )
        if event.supported_after_release is False:
            return _violation(events, index, "unsupported-object-release", state)
        state = HandoverOracleState()

    issue = state.invariant_issue()
    if issue:
        raise InvalidHandoverOracleTrace(f"final oracle state invariant failed: {issue}")
    return HandoverOracleResult(
        HandoverOracleLabel.SAFE,
        state,
        len(events),
    )
