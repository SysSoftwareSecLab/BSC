"""Unreduced explicit-state search for the Timed Bimanual IR.

The implementation deliberately performs no partial-order reduction.  It
enumerates finite input valuations, enabled microstep orders, timed action
starts, joint time elapse, and simultaneous completion orders.  Continuous
geometry is supplied through a three-valued interval checker; an unresolved
interval can never become a verified state.

The lower-level engine remains usable for focused tests.  Qualified paper
runs use :class:`bisafecode.model_checking.BoundedExplicitStateModelChecker`,
which binds property-checker identities and emits the finite-transition-system
contract required for the bounded explicit-state model-checking claim.
"""

from __future__ import annotations

import hashlib
import itertools
import json
import math
import struct
import time
from collections import deque
from dataclasses import dataclass, replace
from enum import Enum
from types import MappingProxyType
from typing import Callable, Iterable, Optional, Union

from .timed_ir import (
    ActionKind,
    ActionNode,
    Arm,
    BarrierNode,
    BranchNode,
    EndNode,
    ForkNode,
    JoinNode,
    LoopHeadNode,
    LoopLatchNode,
    NopNode,
    PredicateOp,
    PredicateSpec,
    SourceSpan,
    TimedProgram,
    program_sha256,
    validate_program,
)
from .trajectory import DOF_PER_ARM, FrozenGripperTrajectory, FrozenJointTrajectory
from .verdicts import Verdict


class Lane(str, Enum):
    MAIN = "main"
    LEFT = "left"
    RIGHT = "right"


class ObjectPhase(str, Enum):
    FREE = "free"
    SENDER_ONLY = "sender-only"
    DUAL_PRE_TRANSFER = "dual-pre-transfer"
    DUAL_POST_TRANSFER = "dual-post-transfer"
    RECEIVER_ONLY = "receiver-only"


class IntervalStatus(str, Enum):
    SAFE = "safe-certificate"
    COLLISION = "collision-witness"
    VIOLATION = "property-violation-witness"
    UNKNOWN = "unresolved"


class UnknownKind(str, Enum):
    NON_LIMIT = "non-limit"
    ANALYSIS_LIMIT = "analysis-limit"


class EdgeKind(str, Enum):
    MICROSTEP = "microstep"
    ACTION_START = "action-start"
    TIME_ELAPSE = "time-elapse"
    TERMINAL = "terminal"


@dataclass(frozen=True)
class InputValue:
    kind: str
    canonical: str

    @classmethod
    def create(cls, value: Union[bool, int, str]) -> "InputValue":
        if type(value) is bool:
            return cls("bool", "true" if value else "false")
        if type(value) is int:
            return cls("int", str(value))
        if type(value) is str:
            return cls("str", value)
        raise TypeError("finite inputs support only bool, int, and str")

    def to_python(self) -> Union[bool, int, str]:
        if self.kind == "bool":
            return self.canonical == "true"
        if self.kind == "int":
            return int(self.canonical)
        if self.kind == "str":
            return self.canonical
        raise ValueError(f"unknown finite input kind: {self.kind}")


@dataclass(frozen=True)
class JointConfiguration:
    bits: tuple[int, ...]

    @classmethod
    def from_positions(cls, positions: Iterable[float]) -> "JointConfiguration":
        values = tuple(float(value) for value in positions)
        if len(values) != DOF_PER_ARM:
            raise ValueError(f"joint configuration requires {DOF_PER_ARM} values")
        if not all(math.isfinite(value) for value in values):
            raise ValueError("joint configuration contains a non-finite value")
        return cls(tuple(struct.unpack(">Q", struct.pack(">d", value))[0] for value in values))

    def positions(self) -> tuple[float, ...]:
        return tuple(struct.unpack(">d", struct.pack(">Q", value))[0] for value in self.bits)


@dataclass(frozen=True)
class GripperConfiguration:
    bits: int

    @classmethod
    def from_position(cls, position: float) -> "GripperConfiguration":
        value = float(position)
        if not math.isfinite(value):
            raise ValueError("gripper configuration contains a non-finite value")
        return cls(struct.unpack(">Q", struct.pack(">d", value))[0])

    def position(self) -> float:
        return struct.unpack(">d", struct.pack(">Q", self.bits))[0]


@dataclass(frozen=True)
class ObjectState:
    object_id: str
    grasps: frozenset[Arm] = frozenset()
    authority: Optional[Arm] = None
    phase: ObjectPhase = ObjectPhase.FREE
    sender: Optional[Arm] = None
    receiver: Optional[Arm] = None
    attachments: tuple[tuple[Arm, str], ...] = ()
    free_pose_ref: Optional[str] = None

    def invariant_issue(self) -> Optional[str]:
        if self.phase is ObjectPhase.FREE:
            if self.grasps or self.authority is not None:
                return "free-phase-has-grasp-or-authority"
            if self.sender is not None or self.receiver is not None:
                return "free-phase-has-protocol-participant"
        elif self.phase is ObjectPhase.SENDER_ONLY:
            if (
                self.sender is None
                or self.receiver is not None
                or self.grasps != frozenset({self.sender})
                or self.authority is not self.sender
            ):
                return "malformed-sender-only-phase"
        elif self.phase in {
            ObjectPhase.DUAL_PRE_TRANSFER,
            ObjectPhase.DUAL_POST_TRANSFER,
        }:
            if (
                self.sender is None
                or self.receiver is None
                or self.sender is self.receiver
                or self.grasps != frozenset({self.sender, self.receiver})
            ):
                return "malformed-dual-grasp-phase"
            expected_authority = (
                self.sender
                if self.phase is ObjectPhase.DUAL_PRE_TRANSFER
                else self.receiver
            )
            if self.authority is not expected_authority:
                return "dual-grasp-authority-phase-mismatch"
        elif self.phase is ObjectPhase.RECEIVER_ONLY:
            if (
                self.sender is not None
                or self.receiver is None
                or self.grasps != frozenset({self.receiver})
                or self.authority is not self.receiver
            ):
                return "malformed-receiver-only-phase"
        attached_arms = {arm for arm, _reference in self.attachments}
        if attached_arms != set(self.grasps):
            return "attachment-grasp-mismatch"
        if len(attached_arms) != len(self.attachments):
            return "duplicate-attachment-arm"
        if tuple(sorted(self.attachments, key=lambda pair: pair[0].value)) != self.attachments:
            return "attachments-not-sorted"
        if self.phase is not ObjectPhase.FREE and self.free_pose_ref is not None:
            return "held-object-has-free-pose"
        return None


@dataclass(frozen=True)
class WorldState:
    left_q: JointConfiguration
    right_q: JointConfiguration
    left_gripper: GripperConfiguration
    right_gripper: GripperConfiguration
    objects: tuple[ObjectState, ...]
    resources: tuple[tuple[str, Optional[Arm]], ...]
    scene_facts: tuple[tuple[str, str], ...] = ()

    def invariant_issue(self) -> Optional[str]:
        object_ids = [item.object_id for item in self.objects]
        if object_ids != sorted(object_ids) or len(object_ids) != len(set(object_ids)):
            return "objects-not-unique-and-sorted"
        resource_ids = [name for name, _owner in self.resources]
        if resource_ids != sorted(resource_ids) or len(resource_ids) != len(set(resource_ids)):
            return "resources-not-unique-and-sorted"
        if any(owner is not None and not isinstance(owner, Arm) for _name, owner in self.resources):
            return "invalid-resource-owner"
        fact_ids = [name for name, _value in self.scene_facts]
        if fact_ids != sorted(fact_ids) or len(fact_ids) != len(set(fact_ids)):
            return "scene-facts-not-unique-and-sorted"
        for item in self.objects:
            issue = item.invariant_issue()
            if issue:
                return f"{item.object_id}:{issue}"
        return None


@dataclass(frozen=True)
class ActiveAction:
    node_id: str
    lane: Lane
    arm: Arm
    kind: ActionKind
    start_ns: int
    end_ns: int
    source: SourceSpan
    trajectory_hash: Optional[str] = None
    object_id: Optional[str] = None
    carrying_objects: tuple[str, ...] = ()
    gripper_trajectory_ref: Optional[str] = None
    gripper_end: Optional[GripperConfiguration] = None


@dataclass(frozen=True)
class SearchState:
    environment_hash: str
    time_ns: int
    pc_main: Optional[str]
    pc_left: Optional[str]
    pc_right: Optional[str]
    loop_counts: tuple[tuple[str, int], ...]
    active: tuple[ActiveAction, ...]
    valuation: tuple[tuple[str, InputValue], ...]
    world: WorldState

    def pc(self, lane: Lane) -> Optional[str]:
        if lane is Lane.MAIN:
            return self.pc_main
        if lane is Lane.LEFT:
            return self.pc_left
        return self.pc_right

    def with_pc(self, lane: Lane, node_id: Optional[str]) -> "SearchState":
        if lane is Lane.MAIN:
            return replace(self, pc_main=node_id)
        if lane is Lane.LEFT:
            return replace(self, pc_left=node_id)
        return replace(self, pc_right=node_id)

    def invariant_issue(self) -> Optional[str]:
        if self.time_ns < 0:
            return "negative-time"
        active_arms = [action.arm for action in self.active]
        active_lanes = [action.lane for action in self.active]
        if len(active_arms) != len(set(active_arms)):
            return "multiple-active-actions-on-arm"
        if len(active_lanes) != len(set(active_lanes)):
            return "multiple-active-actions-on-lane"
        if tuple(sorted(self.active, key=lambda item: item.arm.value)) != self.active:
            return "active-actions-not-sorted"
        if any(not (action.start_ns <= self.time_ns < action.end_ns) for action in self.active):
            return "active-action-time-inconsistent"
        for action in self.active:
            is_gripper_action = action.kind in {ActionKind.CLOSE, ActionKind.OPEN}
            if is_gripper_action and (
                not action.gripper_trajectory_ref or action.gripper_end is None
            ):
                return "active-gripper-trajectory-incomplete"
            if not is_gripper_action and (
                action.gripper_trajectory_ref is not None
                or action.gripper_end is not None
            ):
                return "non-gripper-action-has-gripper-trajectory"
        if tuple(sorted(self.loop_counts)) != self.loop_counts:
            return "loop-counts-not-sorted"
        if tuple(sorted(self.valuation, key=lambda item: item[0])) != self.valuation:
            return "valuation-not-sorted"
        return self.world.invariant_issue()


@dataclass(frozen=True)
class PrimitiveContract:
    grasp_success: Optional[bool] = None
    release_success: Optional[bool] = None
    in_handover_zone: Optional[bool] = None
    attachments_consistent: Optional[bool] = None
    synchronized: Optional[bool] = None
    arms_static: Optional[bool] = None
    supported_after_release: Optional[bool] = None
    attachment_ref: Optional[str] = None
    free_pose_ref: Optional[str] = None
    gripper_trajectory_ref: Optional[str] = None
    gripper_final_position: Optional[float] = None
    scene_fact_updates: tuple[tuple[str, str], ...] = ()


@dataclass(frozen=True)
class ActionLocator:
    """Stable sidecar locator; never bind contracts by container position."""

    source_file: str
    source_line: int
    source_column: int
    kind: ActionKind
    arm: Optional[Arm] = None
    object_id: Optional[str] = None
    resource_id: Optional[str] = None
    sender: Optional[Arm] = None
    receiver: Optional[Arm] = None

    def __post_init__(self) -> None:
        if not self.source_file:
            raise ValueError("action locator requires source_file")
        for name, value in (
            ("source_line", self.source_line),
            ("source_column", self.source_column),
        ):
            if isinstance(value, bool) or not isinstance(value, int) or value < 0:
                raise ValueError(f"{name} must be a non-negative integer")
        if self.source_line == 0:
            raise ValueError("source_line is one-based and must be positive")

    @classmethod
    def from_node(cls, node: ActionNode) -> "ActionLocator":
        return cls(
            source_file=node.source.file,
            source_line=node.source.line,
            source_column=node.source.column,
            kind=node.action.kind,
            arm=node.action.arm,
            object_id=node.action.object_id,
            resource_id=node.action.resource_id,
            sender=node.action.sender,
            receiver=node.action.receiver,
        )

    def matches(self, node: ActionNode) -> bool:
        return self == ActionLocator.from_node(node)


@dataclass(frozen=True)
class LocatedPrimitiveContract:
    locator: ActionLocator
    contract: PrimitiveContract


class PrimitiveContractBindingError(ValueError):
    pass


def bind_primitive_contracts(
    program: TimedProgram,
    located_contracts: Iterable[LocatedPrimitiveContract],
) -> tuple[tuple[str, PrimitiveContract], ...]:
    """Resolve trusted sidecar contracts to exactly one ActionNode each."""

    action_nodes = tuple(
        node for node in program.nodes if isinstance(node, ActionNode)
    )
    bindings: dict[str, PrimitiveContract] = {}
    for located in located_contracts:
        matches = [
            node for node in action_nodes if located.locator.matches(node)
        ]
        if len(matches) != 1:
            raise PrimitiveContractBindingError(
                "contract locator must resolve exactly once: "
                f"{located.locator!r}; matches={len(matches)}"
            )
        node_id = matches[0].node_id
        if node_id in bindings:
            raise PrimitiveContractBindingError(
                f"multiple primitive contracts target node {node_id}"
            )
        bindings[node_id] = located.contract
    return tuple(sorted(bindings.items()))


@dataclass(frozen=True)
class SearchEnvironment:
    environment_hash: str
    initial_left_q: JointConfiguration
    initial_right_q: JointConfiguration
    initial_left_gripper: GripperConfiguration
    initial_right_gripper: GripperConfiguration
    gripper_position_limits: tuple[float, float]
    initial_objects: tuple[ObjectState, ...]
    initial_resources: tuple[tuple[str, Optional[Arm]], ...]
    trajectories: tuple[tuple[str, FrozenJointTrajectory], ...] = ()
    gripper_trajectories: tuple[tuple[str, FrozenGripperTrajectory], ...] = ()
    primitive_contracts: tuple[tuple[str, PrimitiveContract], ...] = ()
    initial_scene_facts: tuple[tuple[str, str], ...] = ()
    required_released_resources: tuple[str, ...] = ()
    required_terminal_scene_facts: tuple[tuple[str, str], ...] = ()


@dataclass(frozen=True)
class IntervalAssessment:
    status: IntervalStatus
    reason: str
    interval_ns: tuple[int, int]
    entity_pair: Optional[tuple[str, str]] = None
    certificate_ref: Optional[str] = None
    property_id: str = "collision"
    witness_ref: Optional[str] = None
    unknown_kind: UnknownKind = UnknownKind.NON_LIMIT

    def __post_init__(self) -> None:
        start, end = self.interval_ns
        if start < 0 or end <= start:
            raise ValueError("interval_ns must be a positive, non-negative interval")
        if not self.reason:
            raise ValueError("interval assessment requires a reason")
        if not self.property_id:
            raise ValueError("interval assessment requires property_id")
        if self.status is IntervalStatus.SAFE and not self.certificate_ref:
            raise ValueError("safe assessment requires certificate_ref")
        if self.status is IntervalStatus.COLLISION and self.entity_pair is None:
            raise ValueError("collision assessment requires entity_pair")
        if self.status is IntervalStatus.VIOLATION and not self.witness_ref:
            raise ValueError("property violation assessment requires witness_ref")
        if (
            self.status is not IntervalStatus.UNKNOWN
            and self.unknown_kind is not UnknownKind.NON_LIMIT
        ):
            raise ValueError("only unknown assessments may carry an unknown kind")


@dataclass(frozen=True)
class PointAssessment:
    """Three-valued geometry assessment at one reachable event state."""

    status: IntervalStatus
    reason: str
    time_ns: int
    entity_pair: Optional[tuple[str, str]] = None
    certificate_ref: Optional[str] = None
    property_id: str = "collision"
    witness_ref: Optional[str] = None
    unknown_kind: UnknownKind = UnknownKind.NON_LIMIT

    def __post_init__(self) -> None:
        if isinstance(self.time_ns, bool) or not isinstance(self.time_ns, int) or self.time_ns < 0:
            raise ValueError("point time_ns must be a non-negative integer")
        if not self.reason:
            raise ValueError("point assessment requires a reason")
        if not self.property_id:
            raise ValueError("point assessment requires property_id")
        if self.status is IntervalStatus.SAFE and not self.certificate_ref:
            raise ValueError("safe point assessment requires certificate_ref")
        if self.status is IntervalStatus.COLLISION and self.entity_pair is None:
            raise ValueError("point collision assessment requires entity_pair")
        if self.status is IntervalStatus.VIOLATION and not self.witness_ref:
            raise ValueError("point property violation assessment requires witness_ref")
        if (
            self.status is not IntervalStatus.UNKNOWN
            and self.unknown_kind is not UnknownKind.NON_LIMIT
        ):
            raise ValueError("only unknown point assessments may carry an unknown kind")


IntervalChecker = Callable[
    [SearchState, int, int, tuple[ActiveAction, ...]], IntervalAssessment
]
PointChecker = Callable[[SearchState], PointAssessment]


@dataclass(frozen=True)
class TerminalOutcome:
    verdict: Verdict
    reason: str
    node_ids: tuple[str, ...]
    sources: tuple[SourceSpan, ...]
    interval: Optional[IntervalAssessment] = None
    point: Optional[PointAssessment] = None
    unknown_kind: UnknownKind = UnknownKind.NON_LIMIT

    def __post_init__(self) -> None:
        if self.verdict is not Verdict.UNKNOWN and self.unknown_kind is not UnknownKind.NON_LIMIT:
            raise ValueError("only unknown terminals may carry an unknown kind")


def _exception_unknown_kind(error: Exception) -> UnknownKind:
    if isinstance(error, (RecursionError, MemoryError, TimeoutError)):
        return UnknownKind.ANALYSIS_LIMIT
    return UnknownKind.NON_LIMIT


def _is_path_local_analysis_limit(outcome: TerminalOutcome) -> bool:
    """Identify UNKNOWN terminals caused by an explicit analysis limit.

    Keep this classification deliberately narrow. Missing or ambiguous
    evidence is a non-limit UNKNOWN and, after a complete search, does not mask
    a separately reachable runtime-invalid terminal. Resource-like checker
    failures count as limits only when the exception type states that fact.
    """

    if outcome.verdict is not Verdict.UNKNOWN:
        return False
    return outcome.unknown_kind is UnknownKind.ANALYSIS_LIMIT


@dataclass(frozen=True)
class Transition:
    kind: EdgeKind
    label: str
    source: SearchState
    target: Optional[SearchState]
    node_ids: tuple[str, ...] = ()
    terminal: Optional[TerminalOutcome] = None
    interval: Optional[IntervalAssessment] = None
    point: Optional[PointAssessment] = None


@dataclass(frozen=True)
class SearchBounds:
    max_states: int
    max_transitions: int
    max_time_ns: int
    wall_timeout_s: Optional[float] = None

    def __post_init__(self) -> None:
        for name, value in (
            ("max_states", self.max_states),
            ("max_transitions", self.max_transitions),
            ("max_time_ns", self.max_time_ns),
        ):
            if isinstance(value, bool) or not isinstance(value, int) or value <= 0:
                raise ValueError(f"{name} must be a positive integer")
        if self.wall_timeout_s is not None and self.wall_timeout_s <= 0:
            raise ValueError("wall_timeout_s must be positive when present")


@dataclass(frozen=True)
class TraceStep:
    kind: EdgeKind
    label: str
    source_fingerprint: str
    target_fingerprint: Optional[str]
    source_time_ns: int
    target_time_ns: Optional[int]
    source_pcs: tuple[Optional[str], Optional[str], Optional[str]]
    target_pcs: Optional[tuple[Optional[str], Optional[str], Optional[str]]]
    valuation: tuple[tuple[str, InputValue], ...]
    source_world: WorldState
    target_world: Optional[WorldState]
    node_ids: tuple[str, ...]
    sources: tuple[SourceSpan, ...]
    terminal_reason: Optional[str]
    interval: Optional[IntervalAssessment]
    point: Optional[PointAssessment]


@dataclass(frozen=True)
class SearchReport:
    verdict: Verdict
    reasons: tuple[str, ...]
    # explored_states counts unique states actually popped from the BFS queue.
    # discovered_states also includes unique successors still queued at exit.
    explored_states: int
    discovered_states: int
    generated_transitions: int
    max_queue_size: int
    search_exhausted: bool
    counterexample: tuple[TraceStep, ...]
    elapsed_ms: float


def unresolved_interval_checker(
    _state: SearchState,
    start_ns: int,
    end_ns: int,
    _active: tuple[ActiveAction, ...],
) -> IntervalAssessment:
    return IntervalAssessment(
        IntervalStatus.UNKNOWN,
        "geometry-backend-not-configured",
        (start_ns, end_ns),
    )


def unresolved_point_checker(state: SearchState) -> PointAssessment:
    return PointAssessment(
        IntervalStatus.UNKNOWN,
        "point-geometry-backend-not-configured",
        state.time_ns,
    )


def compose_point_checkers(
    named_checkers: Iterable[tuple[str, PointChecker]],
) -> PointChecker:
    """Conjoin independent point obligations with deterministic precedence."""

    checkers = tuple(named_checkers)
    names = tuple(name for name, _checker in checkers)
    if not checkers or any(not name for name in names) or len(names) != len(set(names)):
        raise ValueError("point checker composition requires unique non-empty names")

    def composed(state: SearchState) -> PointAssessment:
        assessments = []
        for name, checker in checkers:
            try:
                assessment = checker(state)
            except Exception as error:
                assessment = PointAssessment(
                    IntervalStatus.UNKNOWN,
                    f"point-checker-error:{type(error).__name__}",
                    state.time_ns,
                    property_id=name,
                    unknown_kind=_exception_unknown_kind(error),
                )
            if not isinstance(assessment, PointAssessment):
                assessment = PointAssessment(
                    IntervalStatus.UNKNOWN,
                    "point-checker-invalid-return",
                    state.time_ns,
                    property_id=name,
                )
            elif assessment.time_ns != state.time_ns:
                assessment = PointAssessment(
                    IntervalStatus.UNKNOWN,
                    "point-checker-returned-wrong-time",
                    state.time_ns,
                    property_id=name,
                )
            elif assessment.status not in {
                IntervalStatus.SAFE,
                IntervalStatus.COLLISION,
                IntervalStatus.VIOLATION,
                IntervalStatus.UNKNOWN,
            }:
                assessment = PointAssessment(
                    IntervalStatus.UNKNOWN,
                    "point-checker-returned-invalid-status",
                    state.time_ns,
                    property_id=name,
                )
            assessments.append((name, assessment))
        for status in (IntervalStatus.COLLISION, IntervalStatus.VIOLATION):
            for _name, assessment in assessments:
                if assessment.status is status:
                    return assessment
        unknown = [
            assessment
            for _name, assessment in assessments
            if assessment.status is IntervalStatus.UNKNOWN
        ]
        if unknown:
            # Preserve the global verdict contract when several independent
            # obligations are inconclusive on the same state.  A declared
            # analysis/implementation limit must not be hidden by an earlier
            # non-limit UNKNOWN merely because of checker registration order.
            return next(
                (
                    assessment
                    for assessment in unknown
                    if assessment.unknown_kind is UnknownKind.ANALYSIS_LIMIT
                ),
                unknown[0],
            )
        if any(assessment.status is not IntervalStatus.SAFE for _name, assessment in assessments):
            raise ValueError("composed point checker received an invalid status")
        payload = "|".join(
            f"{name}:{assessment.certificate_ref}" for name, assessment in assessments
        )
        return PointAssessment(
            IntervalStatus.SAFE,
            "all-point-properties-certified",
            state.time_ns,
            certificate_ref="composite:" + hashlib.sha256(payload.encode("utf-8")).hexdigest(),
            property_id="all",
        )

    return composed


def compose_interval_checkers(
    named_checkers: Iterable[tuple[str, IntervalChecker]],
) -> IntervalChecker:
    """Conjoin independent interval obligations over the same requested edge."""

    checkers = tuple(named_checkers)
    names = tuple(name for name, _checker in checkers)
    if not checkers or any(not name for name in names) or len(names) != len(set(names)):
        raise ValueError("interval checker composition requires unique non-empty names")

    def composed(
        state: SearchState,
        start_ns: int,
        end_ns: int,
        active: tuple[ActiveAction, ...],
    ) -> IntervalAssessment:
        assessments = []
        for name, checker in checkers:
            try:
                assessment = checker(state, start_ns, end_ns, active)
            except Exception as error:
                assessment = IntervalAssessment(
                    IntervalStatus.UNKNOWN,
                    f"interval-checker-error:{type(error).__name__}",
                    (start_ns, end_ns),
                    property_id=name,
                    unknown_kind=_exception_unknown_kind(error),
                )
            if not isinstance(assessment, IntervalAssessment):
                assessment = IntervalAssessment(
                    IntervalStatus.UNKNOWN,
                    "interval-checker-invalid-return",
                    (start_ns, end_ns),
                    property_id=name,
                )
            elif assessment.status is IntervalStatus.SAFE and assessment.interval_ns != (
                start_ns,
                end_ns,
            ):
                assessment = IntervalAssessment(
                    IntervalStatus.UNKNOWN,
                    "interval-checker-returned-wrong-interval",
                    (start_ns, end_ns),
                    property_id=name,
                )
            elif not (
                start_ns <= assessment.interval_ns[0]
                < assessment.interval_ns[1]
                <= end_ns
            ):
                assessment = IntervalAssessment(
                    IntervalStatus.UNKNOWN,
                    "interval-checker-returned-out-of-range-interval",
                    (start_ns, end_ns),
                    property_id=name,
                )
            elif assessment.status not in {
                IntervalStatus.SAFE,
                IntervalStatus.COLLISION,
                IntervalStatus.VIOLATION,
                IntervalStatus.UNKNOWN,
            }:
                assessment = IntervalAssessment(
                    IntervalStatus.UNKNOWN,
                    "interval-checker-returned-invalid-status",
                    (start_ns, end_ns),
                    property_id=name,
                )
            assessments.append((name, assessment))
        candidates = [
            assessment
            for _name, assessment in assessments
            if assessment.status
            in {IntervalStatus.COLLISION, IntervalStatus.VIOLATION}
        ]
        if candidates:
            return min(
                candidates,
                key=lambda item: (
                    item.interval_ns,
                    item.property_id,
                    item.status.value,
                ),
            )
        unknown = [
            assessment
            for _name, assessment in assessments
            if assessment.status is IntervalStatus.UNKNOWN
        ]
        if unknown:
            # Match point composition and the final verdict priority: retain a
            # path-local limit reason independently of checker ordering.
            return next(
                (
                    assessment
                    for assessment in unknown
                    if assessment.unknown_kind is UnknownKind.ANALYSIS_LIMIT
                ),
                unknown[0],
            )
        if any(assessment.status is not IntervalStatus.SAFE for _name, assessment in assessments):
            raise ValueError("composed interval checker received an invalid status")
        payload = "|".join(
            f"{name}:{assessment.certificate_ref}" for name, assessment in assessments
        )
        return IntervalAssessment(
            IntervalStatus.SAFE,
            "all-interval-properties-certified",
            (start_ns, end_ns),
            certificate_ref="composite:" + hashlib.sha256(payload.encode("utf-8")).hexdigest(),
            property_id="all",
        )

    return composed


def _object_map(world: WorldState) -> dict[str, ObjectState]:
    return {item.object_id: item for item in world.objects}


def _resource_map(world: WorldState) -> dict[str, Optional[Arm]]:
    return dict(world.resources)


def _contract_map(environment: SearchEnvironment) -> dict[str, PrimitiveContract]:
    return dict(environment.primitive_contracts)


def _trajectory_map(environment: SearchEnvironment) -> dict[str, FrozenJointTrajectory]:
    return dict(environment.trajectories)


def _gripper_trajectory_map(
    environment: SearchEnvironment,
) -> dict[str, FrozenGripperTrajectory]:
    return dict(environment.gripper_trajectories)


def _replace_object(world: WorldState, item: ObjectState) -> WorldState:
    objects = _object_map(world)
    objects[item.object_id] = item
    return replace(world, objects=tuple(sorted(objects.values(), key=lambda value: value.object_id)))


def _replace_resource(world: WorldState, resource_id: str, owner: Optional[Arm]) -> WorldState:
    resources = _resource_map(world)
    resources[resource_id] = owner
    return replace(world, resources=tuple(sorted(resources.items())))


def _apply_scene_facts(world: WorldState, updates: tuple[tuple[str, str], ...]) -> WorldState:
    facts = dict(world.scene_facts)
    facts.update(updates)
    return replace(world, scene_facts=tuple(sorted(facts.items())))


def _typed_equal(left: Union[bool, int, str], right: Union[bool, int, str]) -> bool:
    return type(left) is type(right) and left == right


def evaluate_predicate(
    predicate: PredicateSpec, valuation: tuple[tuple[str, InputValue], ...]
) -> bool:
    values = dict(valuation)
    if predicate.input_name not in values:
        raise KeyError(predicate.input_name)
    value = values[predicate.input_name].to_python()
    if predicate.op is PredicateOp.IS_TRUE:
        return value is True
    if predicate.op is PredicateOp.IS_FALSE:
        return value is False
    if predicate.op is PredicateOp.EQUALS:
        return _typed_equal(value, predicate.compare_value)
    if predicate.op is PredicateOp.NOT_EQUALS:
        return not _typed_equal(value, predicate.compare_value)
    raise ValueError(f"unsupported predicate op: {predicate.op}")


def _canonical(value):
    if isinstance(value, Enum):
        return value.value
    if isinstance(value, frozenset):
        return sorted(_canonical(item) for item in value)
    if hasattr(value, "__dataclass_fields__"):
        return {
            name: _canonical(getattr(value, name))
            for name in value.__dataclass_fields__
        }
    if isinstance(value, tuple):
        return [_canonical(item) for item in value]
    return value


def state_fingerprint(state: SearchState) -> str:
    payload = json.dumps(
        _canonical(state), sort_keys=True, separators=(",", ":"), ensure_ascii=True
    ).encode("ascii")
    return hashlib.sha256(payload).hexdigest()


def search_environment_sha256(environment: SearchEnvironment) -> str:
    """Hash the resolved search inputs, not only the declared environment ID."""

    payload = (
        json.dumps(
            _canonical(environment),
            sort_keys=True,
            separators=(",", ":"),
            ensure_ascii=False,
            allow_nan=False,
        )
        + "\n"
    ).encode("utf-8")
    return hashlib.sha256(payload).hexdigest()


def search_artifact_dict(
    program: TimedProgram,
    environment: SearchEnvironment,
    bounds: SearchBounds,
    report: SearchReport,
) -> dict:
    """Build the evidence object used for result tables and counterexample audit."""

    return {
        "schema": "bisafecode.explicit-search-result/v0.2",
        "algorithm": {
            "name": "unreduced-bfs",
            "partial_order_reduction": False,
            "edge_cost": "one-per-generated-transition",
            "violation_counterexample_is_shortest": (
                report.verdict is Verdict.VIOLATED and bool(report.counterexample)
            ),
        },
        "program_sha256": program_sha256(program),
        "declared_environment_hash": environment.environment_hash,
        "resolved_search_environment_sha256": search_environment_sha256(environment),
        "trajectory_sha256": sorted(program.trajectory_hashes),
        "gripper_trajectory_sha256": sorted(
            trajectory_hash for trajectory_hash, _trajectory in environment.gripper_trajectories
        ),
        "bounds": _canonical(bounds),
        "report": _canonical(report),
    }


def search_artifact_bytes(
    program: TimedProgram,
    environment: SearchEnvironment,
    bounds: SearchBounds,
    report: SearchReport,
) -> bytes:
    return (
        json.dumps(
            search_artifact_dict(program, environment, bounds, report),
            sort_keys=True,
            separators=(",", ":"),
            ensure_ascii=False,
            allow_nan=False,
        )
        + "\n"
    ).encode("utf-8")


def search_artifact_sha256(
    program: TimedProgram,
    environment: SearchEnvironment,
    bounds: SearchBounds,
    report: SearchReport,
) -> str:
    return hashlib.sha256(
        search_artifact_bytes(program, environment, bounds, report)
    ).hexdigest()


class ExplicitStateEngine:
    def __init__(
        self,
        program: TimedProgram,
        environment: SearchEnvironment,
        *,
        interval_checker: IntervalChecker = unresolved_interval_checker,
        point_checker: PointChecker = unresolved_point_checker,
    ):
        self.program = program
        self.environment = environment
        self.interval_checker = interval_checker
        self.point_checker = point_checker
        # Successor semantics are immutable for the lifetime of an engine.
        # Read-only mappings prevent a cache/side-table mutation from changing
        # the future of an already visited exact state key.
        self.nodes = MappingProxyType({node.node_id: node for node in program.nodes})
        self.trajectories = MappingProxyType(_trajectory_map(environment))
        self.gripper_trajectories = MappingProxyType(
            _gripper_trajectory_map(environment)
        )
        self.contracts = MappingProxyType(_contract_map(environment))

    def configuration_issues(self) -> tuple[str, ...]:
        issues = [f"IR:{item.code}:{item.node_id or '<program>'}" for item in validate_program(self.program)]
        if self.environment.environment_hash != self.program.environment_hash:
            issues.append("environment-hash-mismatch")
        object_ids = tuple(item.object_id for item in self.environment.initial_objects)
        if object_ids != tuple(sorted(self.program.object_ids)):
            issues.append("initial-object-set-mismatch")
        resource_ids = tuple(name for name, _owner in self.environment.initial_resources)
        if resource_ids != tuple(sorted(self.program.resource_ids)):
            issues.append("initial-resource-set-mismatch")
        world = self._initial_world()
        world_issue = world.invariant_issue()
        if world_issue:
            issues.append(f"initial-world:{world_issue}")
        lower, upper = self.environment.gripper_position_limits
        if (
            not math.isfinite(lower)
            or not math.isfinite(upper)
            or lower >= upper
        ):
            issues.append("invalid-gripper-position-limits")
        else:
            for name, configuration in (
                ("left", self.environment.initial_left_gripper),
                ("right", self.environment.initial_right_gripper),
            ):
                if not lower <= configuration.position() <= upper:
                    issues.append(f"initial-{name}-gripper-out-of-limits")
        for trajectory_hash in self.program.trajectory_hashes:
            trajectory = self.trajectories.get(trajectory_hash)
            if trajectory is None:
                issues.append(f"missing-trajectory:{trajectory_hash}")
            elif trajectory.content_hash != trajectory_hash:
                issues.append(f"trajectory-content-hash-mismatch:{trajectory_hash}")
        if len(self.trajectories) != len(self.environment.trajectories):
            issues.append("duplicate-trajectory-binding")
        if len(self.gripper_trajectories) != len(
            self.environment.gripper_trajectories
        ):
            issues.append("duplicate-gripper-trajectory-binding")
        for trajectory_hash, trajectory in self.gripper_trajectories.items():
            if trajectory.content_hash != trajectory_hash:
                issues.append(
                    f"gripper-trajectory-content-hash-mismatch:{trajectory_hash}"
                )
        if len(self.contracts) != len(self.environment.primitive_contracts):
            issues.append("duplicate-primitive-contract")
        for node_id in self.contracts:
            if node_id not in self.nodes or not isinstance(self.nodes[node_id], ActionNode):
                issues.append(f"primitive-contract-target-invalid:{node_id}")
            else:
                node = self.nodes[node_id]
                contract = self.contracts[node_id]
                if node.action.kind in {ActionKind.CLOSE, ActionKind.OPEN}:
                    target = contract.gripper_final_position
                    if target is not None and (
                        not math.isfinite(target)
                        or not lower <= target <= upper
                    ):
                        issues.append(f"gripper-target-out-of-limits:{node_id}")
                    reference = contract.gripper_trajectory_ref
                    if reference is not None:
                        trajectory = self.gripper_trajectories.get(reference)
                        if trajectory is None:
                            issues.append(
                                f"missing-gripper-trajectory:{node_id}:{reference}"
                            )
                        elif (
                            trajectory.arm != node.action.arm.value
                            or trajectory.duration_ns != node.action.duration_ns
                        ):
                            issues.append(
                                f"gripper-trajectory-metadata-mismatch:{node_id}"
                            )
        for node in self.program.nodes:
            if not isinstance(node, ActionNode) or node.action.kind is not ActionKind.MOVE:
                continue
            trajectory = self.trajectories.get(node.action.trajectory_hash)
            if trajectory is not None and (
                trajectory.arm != node.action.arm.value
                or trajectory.duration_ns != node.action.duration_ns
            ):
                issues.append(f"move-trajectory-metadata-mismatch:{node.node_id}")
        resources = set(self.program.resource_ids)
        for resource_id in self.environment.required_released_resources:
            if resource_id not in resources:
                issues.append(f"unknown-required-release-resource:{resource_id}")
        required_facts = self.environment.required_terminal_scene_facts
        if len(required_facts) != len(dict(required_facts)):
            issues.append("duplicate-required-terminal-scene-fact")
        if len(self.environment.required_released_resources) != len(
            set(self.environment.required_released_resources)
        ):
            issues.append("duplicate-required-release-resource")
        for node_id, contract in self.environment.primitive_contracts:
            updates = contract.scene_fact_updates
            if len(updates) != len(dict(updates)):
                issues.append(f"duplicate-contract-scene-fact:{node_id}")
        return tuple(sorted(set(issues)))

    def _initial_world(self) -> WorldState:
        return WorldState(
            self.environment.initial_left_q,
            self.environment.initial_right_q,
            self.environment.initial_left_gripper,
            self.environment.initial_right_gripper,
            tuple(sorted(self.environment.initial_objects, key=lambda item: item.object_id)),
            tuple(sorted(self.environment.initial_resources, key=lambda item: item[0])),
            tuple(sorted(self.environment.initial_scene_facts)),
        )

    def initial_states(self) -> tuple[SearchState, ...]:
        domains = [item.values for item in self.program.finite_inputs]
        combinations = itertools.product(*domains) if domains else [()]
        states = []
        for combination in combinations:
            valuation = tuple(
                sorted(
                    (
                        (finite_input.name, InputValue.create(value))
                        for finite_input, value in zip(self.program.finite_inputs, combination)
                    ),
                    key=lambda item: item[0],
                )
            )
            states.append(
                SearchState(
                    environment_hash=self.program.environment_hash,
                    time_ns=0,
                    pc_main=self.program.entry_id,
                    pc_left=None,
                    pc_right=None,
                    loop_counts=(),
                    active=(),
                    valuation=valuation,
                    world=self._initial_world(),
                )
            )
        return tuple(states)

    def successors(
        self, state: SearchState, *, max_time_ns: Optional[int] = None
    ) -> tuple[Transition, ...]:
        issue = state.invariant_issue()
        if issue:
            return (
                self._terminal_transition(
                    state, Verdict.INVALID, f"state-invariant:{issue}", (), ()
                ),
            )

        barrier_release = self._barrier_release(state)
        if barrier_release is not None:
            return (barrier_release,)
        join_release = self._join_release(state)
        if join_release is not None:
            return (join_release,)

        enabled: list[Transition] = []
        blocked_lanes = {action.lane for action in state.active}
        for lane in (Lane.MAIN, Lane.LEFT, Lane.RIGHT):
            if lane in blocked_lanes:
                continue
            node_id = state.pc(lane)
            if node_id is None:
                continue
            node = self.nodes[node_id]
            transition = self._lane_microstep(state, lane, node)
            if transition is not None:
                enabled.append(transition)
        if enabled:
            return tuple(enabled)
        if state.active:
            return self._time_elapse(state, max_time_ns=max_time_ns)
        if self._is_success_terminal(state):
            terminal_obligation = self._termination_obligation(state)
            return () if terminal_obligation is None else (terminal_obligation,)
        node_ids = tuple(
            node_id
            for node_id in (state.pc_main, state.pc_left, state.pc_right)
            if node_id is not None
        )
        sources = tuple(self.nodes[node_id].source for node_id in node_ids)
        return (
            self._terminal_transition(
                state, Verdict.INVALID, "control-deadlock", node_ids, sources
            ),
        )

    def _lane_microstep(self, state: SearchState, lane: Lane, node) -> Optional[Transition]:
        if isinstance(node, EndNode):
            return None
        if isinstance(node, (BarrierNode, JoinNode)):
            return None
        if isinstance(node, NopNode):
            target = state.with_pc(lane, node.next_id)
            return self._transition(EdgeKind.MICROSTEP, "nop", state, target, (node.node_id,))
        if isinstance(node, BranchNode):
            branch = evaluate_predicate(node.predicate, state.valuation)
            target = state.with_pc(lane, node.true_id if branch else node.false_id)
            return self._transition(
                EdgeKind.MICROSTEP,
                f"branch:{'true' if branch else 'false'}",
                state,
                target,
                (node.node_id,),
            )
        if isinstance(node, LoopHeadNode):
            counts = dict(state.loop_counts)
            count = counts.get(node.loop_id, 0)
            if count < node.bound:
                counts[node.loop_id] = count + 1
                next_id = node.body_id
                label = f"loop-enter:{count + 1}/{node.bound}"
            else:
                counts.pop(node.loop_id, None)
                next_id = node.exit_id
                label = f"loop-exit:{count}/{node.bound}"
            target = replace(
                state.with_pc(lane, next_id), loop_counts=tuple(sorted(counts.items()))
            )
            return self._transition(
                EdgeKind.MICROSTEP, label, state, target, (node.node_id,)
            )
        if isinstance(node, LoopLatchNode):
            target = state.with_pc(lane, node.head_id)
            return self._transition(
                EdgeKind.MICROSTEP, "loop-latch", state, target, (node.node_id,)
            )
        if isinstance(node, ForkNode):
            if lane is not Lane.MAIN or state.pc_left is not None or state.pc_right is not None:
                return self._terminal_transition(
                    state,
                    Verdict.INVALID,
                    "fork-outside-idle-main",
                    (node.node_id,),
                    (node.source,),
                )
            target = replace(
                state,
                pc_main=node.join_id,
                pc_left=node.left_entry,
                pc_right=node.right_entry,
            )
            return self._transition(
                EdgeKind.MICROSTEP, f"fork:{node.par_id}", state, target, (node.node_id,)
            )
        if isinstance(node, ActionNode):
            if node.action.kind in {ActionKind.ACQUIRE, ActionKind.RELEASE, ActionKind.TRANSFER_AUTHORITY}:
                return self._instant_action(state, lane, node)
            return self._start_timed_action(state, lane, node)
        return self._terminal_transition(
            state,
            Verdict.INVALID,
            "unsupported-ir-node",
            (node.node_id,),
            (node.source,),
        )

    def _barrier_release(self, state: SearchState) -> Optional[Transition]:
        if state.pc_left is None or state.pc_right is None:
            return None
        left = self.nodes[state.pc_left]
        right = self.nodes[state.pc_right]
        if not isinstance(left, BarrierNode) or not isinstance(right, BarrierNode):
            return None
        if any(action.lane in {Lane.LEFT, Lane.RIGHT} for action in state.active):
            return None
        if left.barrier_id != right.barrier_id:
            return self._terminal_transition(
                state,
                Verdict.INVALID,
                "barrier-id-mismatch",
                (left.node_id, right.node_id),
                (left.source, right.source),
            )
        target = replace(state, pc_left=left.next_id, pc_right=right.next_id)
        return self._transition(
            EdgeKind.MICROSTEP,
            f"barrier-release:{left.barrier_id}",
            state,
            target,
            (left.node_id, right.node_id),
        )

    def _join_release(self, state: SearchState) -> Optional[Transition]:
        if state.pc_main is None:
            return None
        main = self.nodes[state.pc_main]
        if not isinstance(main, JoinNode):
            return None
        if state.pc_left != main.node_id or state.pc_right != main.node_id:
            return None
        if any(action.lane in {Lane.LEFT, Lane.RIGHT} for action in state.active):
            return None
        target = replace(state, pc_main=main.next_id, pc_left=None, pc_right=None)
        return self._transition(
            EdgeKind.MICROSTEP,
            f"join:{main.par_id}",
            state,
            target,
            (main.node_id,),
        )

    def _instant_action(self, state: SearchState, lane: Lane, node: ActionNode) -> Transition:
        action = node.action
        target = state.with_pc(lane, node.next_id)
        if action.kind is ActionKind.ACQUIRE:
            resources = _resource_map(state.world)
            owner = resources[action.resource_id]
            if owner not in {None, action.arm}:
                return self._terminal_transition(
                    state,
                    Verdict.VIOLATED,
                    "resource-owned-by-other-arm",
                    (node.node_id,),
                    (node.source,),
                )
            target = replace(
                target,
                world=_replace_resource(state.world, action.resource_id, action.arm),
            )
        elif action.kind is ActionKind.RELEASE:
            if _resource_map(state.world)[action.resource_id] is not action.arm:
                return self._terminal_transition(
                    state,
                    Verdict.VIOLATED,
                    "resource-release-by-non-owner",
                    (node.node_id,),
                    (node.source,),
                )
            target = replace(
                target, world=_replace_resource(state.world, action.resource_id, None)
            )
        else:
            outcome = self._transfer_authority(state.world, node)
            if isinstance(outcome, TerminalOutcome):
                return Transition(
                    EdgeKind.TERMINAL,
                    "transfer-authority",
                    state,
                    None,
                    (node.node_id,),
                    outcome,
                )
            target = replace(target, world=outcome)
        return self._transition(
            EdgeKind.MICROSTEP, action.kind.value, state, target, (node.node_id,)
        )

    def _transfer_authority(
        self, world: WorldState, node: ActionNode
    ) -> Union[WorldState, TerminalOutcome]:
        action = node.action
        item = _object_map(world)[action.object_id]
        if (
            item.phase is not ObjectPhase.DUAL_PRE_TRANSFER
            or item.sender is not action.sender
            or item.receiver is not action.receiver
            or item.grasps != frozenset({action.sender, action.receiver})
            or item.authority is not action.sender
        ):
            return self._outcome(
                Verdict.VIOLATED, "illegal-authority-transfer", (node,)
            )
        contract = self.contracts.get(node.node_id, PrimitiveContract())
        for reason, value in (
            ("handover-zone-undetermined", contract.in_handover_zone),
            ("synchronization-undetermined", contract.synchronized),
            ("static-dual-grasp-undetermined", contract.arms_static),
        ):
            if value is None:
                return self._outcome(Verdict.UNKNOWN, reason, (node,))
            if value is not True:
                return self._outcome(
                    Verdict.VIOLATED, "illegal-authority-transfer", (node,)
                )
        updated = replace(
            item,
            authority=action.receiver,
            phase=ObjectPhase.DUAL_POST_TRANSFER,
        )
        return _apply_scene_facts(
            _replace_object(world, updated), contract.scene_fact_updates
        )

    def _start_timed_action(self, state: SearchState, lane: Lane, node: ActionNode) -> Transition:
        action = node.action
        if any(active.arm is action.arm for active in state.active):
            return self._terminal_transition(
                state,
                Verdict.INVALID,
                "arm-already-active",
                (node.node_id,),
                (node.source,),
            )
        precondition = self._timed_precondition(state.world, node)
        if precondition is not None:
            return Transition(
                EdgeKind.TERMINAL,
                f"precondition:{action.kind.value}",
                state,
                None,
                (node.node_id,),
                precondition,
            )

        carrying: tuple[str, ...] = ()
        gripper_trajectory_ref = None
        gripper_end = None
        if action.kind in {ActionKind.CLOSE, ActionKind.OPEN}:
            contract = self.contracts.get(node.node_id, PrimitiveContract())
            if contract.gripper_final_position is None:
                return self._terminal_transition(
                    state,
                    Verdict.UNKNOWN,
                    "gripper-target-undetermined",
                    (node.node_id,),
                    (node.source,),
                )
            if not contract.gripper_trajectory_ref:
                return self._terminal_transition(
                    state,
                    Verdict.UNKNOWN,
                    "gripper-trajectory-undetermined",
                    (node.node_id,),
                    (node.source,),
                )
            lower, upper = self.environment.gripper_position_limits
            if not lower <= contract.gripper_final_position <= upper:
                return self._terminal_transition(
                    state,
                    Verdict.INVALID,
                    "gripper-target-out-of-limits",
                    (node.node_id,),
                    (node.source,),
                )
            trajectory = self.gripper_trajectories.get(
                contract.gripper_trajectory_ref
            )
            if trajectory is None:
                return self._terminal_transition(
                    state,
                    Verdict.INVALID,
                    "missing-gripper-trajectory-artifact",
                    (node.node_id,),
                    (node.source,),
                )
            if (
                trajectory.arm != action.arm.value
                or trajectory.duration_ns != action.duration_ns
            ):
                return self._terminal_transition(
                    state,
                    Verdict.INVALID,
                    "gripper-trajectory-metadata-mismatch",
                    (node.node_id,),
                    (node.source,),
                )
            current_gripper = (
                state.world.left_gripper
                if action.arm is Arm.LEFT
                else state.world.right_gripper
            )
            start_gripper = GripperConfiguration.from_position(
                trajectory.points[0].positions[0]
            )
            end_gripper = GripperConfiguration.from_position(
                trajectory.points[-1].positions[0]
            )
            declared_end = GripperConfiguration.from_position(
                contract.gripper_final_position
            )
            if current_gripper != start_gripper:
                return self._terminal_transition(
                    state,
                    Verdict.INVALID,
                    "gripper-trajectory-start-binding-mismatch",
                    (node.node_id,),
                    (node.source,),
                )
            if end_gripper != declared_end:
                return self._terminal_transition(
                    state,
                    Verdict.INVALID,
                    "gripper-trajectory-end-binding-mismatch",
                    (node.node_id,),
                    (node.source,),
                )
            gripper_trajectory_ref = contract.gripper_trajectory_ref
            gripper_end = end_gripper
        if action.kind is ActionKind.MOVE:
            trajectory = self.trajectories.get(action.trajectory_hash)
            if trajectory is None:
                return self._terminal_transition(
                    state,
                    Verdict.INVALID,
                    "missing-trajectory-artifact",
                    (node.node_id,),
                    (node.source,),
                )
            if trajectory.arm != action.arm.value or trajectory.duration_ns != action.duration_ns:
                return self._terminal_transition(
                    state,
                    Verdict.INVALID,
                    "trajectory-metadata-mismatch",
                    (node.node_id,),
                    (node.source,),
                )
            current_q = state.world.left_q if action.arm is Arm.LEFT else state.world.right_q
            start_q = JointConfiguration.from_positions(trajectory.points[0].positions)
            if current_q != start_q:
                return self._terminal_transition(
                    state,
                    Verdict.INVALID,
                    "trajectory-start-binding-mismatch",
                    (node.node_id,),
                    (node.source,),
                )
            carried = [
                item
                for item in state.world.objects
                if action.arm in item.grasps
            ]
            if len(carried) > 1:
                return self._terminal_transition(
                    state,
                    Verdict.VIOLATED,
                    "multiple-carried-objects-unsupported",
                    (node.node_id,),
                    (node.source,),
                )
            if carried:
                item = carried[0]
                if len(item.grasps) == 2:
                    return self._terminal_transition(
                        state,
                        Verdict.VIOLATED,
                        "dual-grasp-motion-unsupported",
                        (node.node_id,),
                        (node.source,),
                    )
                if item.authority is not action.arm:
                    return self._terminal_transition(
                        state,
                        Verdict.VIOLATED,
                        "motion-without-object-authority",
                        (node.node_id,),
                        (node.source,),
                    )
                carrying = (item.object_id,)

        active = ActiveAction(
            node_id=node.node_id,
            lane=lane,
            arm=action.arm,
            kind=action.kind,
            start_ns=state.time_ns,
            end_ns=state.time_ns + action.duration_ns,
            source=node.source,
            trajectory_hash=action.trajectory_hash,
            object_id=action.object_id,
            carrying_objects=carrying,
            gripper_trajectory_ref=gripper_trajectory_ref,
            gripper_end=gripper_end,
        )
        target = state.with_pc(lane, node.next_id)
        target = replace(
            target,
            active=tuple(sorted(state.active + (active,), key=lambda item: item.arm.value)),
        )
        return self._transition(
            EdgeKind.ACTION_START,
            f"start:{action.kind.value}:{action.arm.value}",
            state,
            target,
            (node.node_id,),
        )

    def _timed_precondition(
        self, world: WorldState, node: ActionNode
    ) -> Optional[TerminalOutcome]:
        action = node.action
        if action.kind is ActionKind.CLOSE:
            item = _object_map(world)[action.object_id]
            if item.phase is ObjectPhase.FREE:
                return None
            if (
                item.phase is ObjectPhase.SENDER_ONLY
                and action.arm not in item.grasps
                and len(item.grasps) == 1
            ):
                return None
            return self._outcome(
                Verdict.VIOLATED, "close-outside-supported-grasp-phase", (node,)
            )
        if action.kind is ActionKind.OPEN:
            item = _object_map(world)[action.object_id]
            if action.arm not in item.grasps:
                return self._outcome(
                    Verdict.VIOLATED, "release-by-non-holder", (node,)
                )
            if item.phase is ObjectPhase.DUAL_PRE_TRANSFER:
                return self._outcome(
                    Verdict.VIOLATED, "sender-release-before-transfer", (node,)
                )
            if item.phase is ObjectPhase.DUAL_POST_TRANSFER and action.arm is not item.sender:
                return self._outcome(
                    Verdict.VIOLATED, "receiver-release-during-handover", (node,)
                )
        return None

    def _next_event_time(self, state: SearchState) -> int:
        candidates = [action.end_ns for action in state.active]
        for action in state.active:
            if action.kind is not ActionKind.MOVE:
                continue
            trajectory = self.trajectories[action.trajectory_hash]
            elapsed = state.time_ns - action.start_ns
            for point in trajectory.points:
                if point.time_ns > elapsed:
                    candidates.append(action.start_ns + point.time_ns)
                    break
        return min(candidates)

    def _time_elapse(
        self, state: SearchState, *, max_time_ns: Optional[int]
    ) -> tuple[Transition, ...]:
        next_time = self._next_event_time(state)
        if max_time_ns is not None and next_time > max_time_ns:
            return (
                self._terminal_transition(
                    state,
                    Verdict.UNKNOWN,
                    "global-time-bound-exhausted",
                    tuple(action.node_id for action in state.active),
                    tuple(action.source for action in state.active),
                    unknown_kind=UnknownKind.ANALYSIS_LIMIT,
                ),
            )
        try:
            assessment = self.interval_checker(state, state.time_ns, next_time, state.active)
        except Exception as error:  # Geometry backend failure cannot become verified.
            return (
                self._terminal_transition(
                    state,
                    Verdict.UNKNOWN,
                    f"interval-checker-error:{type(error).__name__}",
                    tuple(action.node_id for action in state.active),
                    tuple(action.source for action in state.active),
                    unknown_kind=_exception_unknown_kind(error),
                ),
            )
        if not isinstance(assessment, IntervalAssessment):
            return (
                self._terminal_transition(
                    state,
                    Verdict.UNKNOWN,
                    "interval-checker-invalid-return",
                    tuple(action.node_id for action in state.active),
                    tuple(action.source for action in state.active),
                ),
            )
        assessment_start, assessment_end = assessment.interval_ns
        if not (
            state.time_ns <= assessment_start < assessment_end <= next_time
        ) or (
            assessment.status is IntervalStatus.SAFE
            and assessment.interval_ns != (state.time_ns, next_time)
        ):
            return (
                self._terminal_transition(
                    state,
                    Verdict.UNKNOWN,
                    "interval-checker-returned-wrong-interval",
                    tuple(action.node_id for action in state.active),
                    tuple(action.source for action in state.active),
                ),
            )
        if assessment.status in {
            IntervalStatus.COLLISION,
            IntervalStatus.VIOLATION,
        }:
            return (
                self._terminal_transition(
                    state,
                    Verdict.VIOLATED,
                    assessment.reason,
                    tuple(action.node_id for action in state.active),
                    tuple(action.source for action in state.active),
                    assessment,
                ),
            )
        if assessment.status is IntervalStatus.UNKNOWN:
            return (
                self._terminal_transition(
                    state,
                    Verdict.UNKNOWN,
                    assessment.reason,
                    tuple(action.node_id for action in state.active),
                    tuple(action.source for action in state.active),
                    assessment,
                ),
            )
        if assessment.status is not IntervalStatus.SAFE:
            return (
                self._terminal_transition(
                    state,
                    Verdict.UNKNOWN,
                    "interval-checker-returned-invalid-status",
                    tuple(action.node_id for action in state.active),
                    tuple(action.source for action in state.active),
                ),
            )

        progressed_world = self._world_at_time(state, next_time)
        completed = tuple(action for action in state.active if action.end_ns == next_time)
        remaining = tuple(action for action in state.active if action.end_ns != next_time)
        base = replace(
            state,
            time_ns=next_time,
            active=remaining,
            world=progressed_world,
        )
        if not completed:
            return (
                self._transition(
                    EdgeKind.TIME_ELAPSE,
                    f"time:{state.time_ns}->{next_time}",
                    state,
                    base,
                    tuple(action.node_id for action in state.active),
                    assessment,
                ),
            )

        transitions = []
        for order in itertools.permutations(completed):
            current_world = base.world
            terminal = None
            for action in order:
                outcome = self._complete_action(current_world, action)
                if isinstance(outcome, TerminalOutcome):
                    terminal = outcome
                    break
                current_world = outcome
            label = "complete:" + ",".join(action.node_id for action in order)
            if terminal is not None:
                event_state = replace(base, world=current_world)
                transitions.append(
                    Transition(
                        EdgeKind.TERMINAL,
                        label,
                        state,
                        event_state,
                        tuple(action.node_id for action in order),
                        terminal,
                        assessment,
                    )
                )
            else:
                target = replace(base, world=current_world)
                transitions.append(
                    self._transition(
                        EdgeKind.TIME_ELAPSE,
                        label,
                        state,
                        target,
                        tuple(action.node_id for action in order),
                        assessment,
                    )
                )
        return tuple(transitions)

    def _world_at_time(self, state: SearchState, next_time: int) -> WorldState:
        world = state.world
        for action in state.active:
            if action.kind in {ActionKind.CLOSE, ActionKind.OPEN}:
                if next_time == action.end_ns:
                    if action.gripper_end is None:
                        raise ValueError("active gripper action has no final configuration")
                    if action.arm is Arm.LEFT:
                        world = replace(world, left_gripper=action.gripper_end)
                    else:
                        world = replace(world, right_gripper=action.gripper_end)
                continue
            if action.kind is not ActionKind.MOVE:
                continue
            trajectory = self.trajectories[action.trajectory_hash]
            elapsed = next_time - action.start_ns
            boundary_point = next(
                (point for point in trajectory.points if point.time_ns == elapsed),
                None,
            )
            if boundary_point is not None:
                positions = boundary_point.positions
            else:
                segment = next(
                    segment
                    for segment in trajectory.segments()
                    if segment.start_ns < elapsed < segment.end_ns
                )
                positions, _velocities, _accelerations = segment.evaluate(
                    elapsed - segment.start_ns
                )
            configuration = JointConfiguration.from_positions(positions)
            if action.arm is Arm.LEFT:
                world = replace(world, left_q=configuration)
            else:
                world = replace(world, right_q=configuration)
        return world

    def _complete_action(
        self, world: WorldState, action: ActiveAction
    ) -> Union[WorldState, TerminalOutcome]:
        node = self.nodes[action.node_id]
        contract = self.contracts.get(action.node_id, PrimitiveContract())
        if action.kind in {ActionKind.WAIT, ActionKind.MOVE}:
            return _apply_scene_facts(world, contract.scene_fact_updates)
        if action.kind is ActionKind.CLOSE:
            if contract.grasp_success is None:
                return self._outcome(
                    Verdict.UNKNOWN, "grasp-outcome-undetermined", (node,)
                )
            if contract.grasp_success is not True:
                return self._outcome(Verdict.VIOLATED, "grasp-failed", (node,))
            if not contract.attachment_ref:
                return self._outcome(
                    Verdict.UNKNOWN, "attachment-reference-undetermined", (node,)
                )
            item = _object_map(world)[action.object_id]
            attachments = dict(item.attachments)
            attachments[action.arm] = contract.attachment_ref
            if item.phase is ObjectPhase.FREE:
                updated = replace(
                    item,
                    grasps=frozenset({action.arm}),
                    authority=action.arm,
                    phase=ObjectPhase.SENDER_ONLY,
                    sender=action.arm,
                    receiver=None,
                    attachments=tuple(sorted(attachments.items(), key=lambda pair: pair[0].value)),
                    free_pose_ref=None,
                )
            else:
                if contract.in_handover_zone is None:
                    return self._outcome(
                        Verdict.UNKNOWN, "handover-zone-undetermined", (node,)
                    )
                if contract.attachments_consistent is None:
                    return self._outcome(
                        Verdict.UNKNOWN, "attachment-consistency-undetermined", (node,)
                    )
                if contract.in_handover_zone is not True:
                    return self._outcome(
                        Verdict.VIOLATED, "receiver-grasp-outside-zone", (node,)
                    )
                if contract.attachments_consistent is not True:
                    return self._outcome(
                        Verdict.VIOLATED, "inconsistent-dual-attachment", (node,)
                    )
                updated = replace(
                    item,
                    grasps=frozenset(set(item.grasps) | {action.arm}),
                    phase=ObjectPhase.DUAL_PRE_TRANSFER,
                    receiver=action.arm,
                    attachments=tuple(sorted(attachments.items(), key=lambda pair: pair[0].value)),
                )
            return _apply_scene_facts(
                _replace_object(world, updated), contract.scene_fact_updates
            )
        if action.kind is ActionKind.OPEN:
            if contract.release_success is None:
                return self._outcome(
                    Verdict.UNKNOWN, "release-outcome-undetermined", (node,)
                )
            if contract.release_success is not True:
                return self._outcome(Verdict.VIOLATED, "release-failed", (node,))
            item = _object_map(world)[action.object_id]
            attachments = dict(item.attachments)
            attachments.pop(action.arm, None)
            if item.phase is ObjectPhase.DUAL_POST_TRANSFER:
                updated = replace(
                    item,
                    grasps=frozenset({item.receiver}),
                    phase=ObjectPhase.RECEIVER_ONLY,
                    sender=None,
                    attachments=tuple(sorted(attachments.items(), key=lambda pair: pair[0].value)),
                )
            else:
                if contract.supported_after_release is None:
                    return self._outcome(
                        Verdict.UNKNOWN, "release-support-undetermined", (node,)
                    )
                if contract.supported_after_release is not True:
                    return self._outcome(
                        Verdict.VIOLATED, "unsupported-object-release", (node,)
                    )
                if not contract.free_pose_ref:
                    return self._outcome(
                        Verdict.UNKNOWN, "released-pose-undetermined", (node,)
                    )
                updated = replace(
                    item,
                    grasps=frozenset(),
                    authority=None,
                    phase=ObjectPhase.FREE,
                    sender=None,
                    receiver=None,
                    attachments=(),
                    free_pose_ref=contract.free_pose_ref,
                )
            return _apply_scene_facts(
                _replace_object(world, updated), contract.scene_fact_updates
            )
        return self._outcome(Verdict.INVALID, "unsupported-completion-kind", (node,))

    def _is_success_terminal(self, state: SearchState) -> bool:
        return (
            state.pc_main is not None
            and isinstance(self.nodes[state.pc_main], EndNode)
            and state.pc_left is None
            and state.pc_right is None
            and not state.active
        )

    def _termination_obligation(self, state: SearchState) -> Optional[Transition]:
        resources = _resource_map(state.world)
        for resource_id in self.environment.required_released_resources:
            if resources[resource_id] is not None:
                node = self.nodes[state.pc_main]
                return self._terminal_transition(
                    state,
                    Verdict.VIOLATED,
                    f"resource-held-at-termination:{resource_id}",
                    (node.node_id,),
                    (node.source,),
                )
        facts = dict(state.world.scene_facts)
        for name, expected in self.environment.required_terminal_scene_facts:
            if name not in facts:
                node = self.nodes[state.pc_main]
                return self._terminal_transition(
                    state,
                    Verdict.UNKNOWN,
                    f"terminal-scene-fact-undetermined:{name}",
                    (node.node_id,),
                    (node.source,),
                )
            if facts[name] != expected:
                node = self.nodes[state.pc_main]
                return self._terminal_transition(
                    state,
                    Verdict.VIOLATED,
                    f"terminal-scene-fact-mismatch:{name}",
                    (node.node_id,),
                    (node.source,),
                )
        return None

    def run(self, bounds: SearchBounds) -> SearchReport:
        started = time.monotonic()
        deadline = (
            started + bounds.wall_timeout_s
            if bounds.wall_timeout_s is not None
            else None
        )

        def wall_time_exhausted() -> bool:
            return deadline is not None and time.monotonic() >= deadline

        issues = self.configuration_issues()
        if issues:
            return SearchReport(
                Verdict.INVALID,
                issues,
                0,
                0,
                0,
                0,
                True,
                (),
                (time.monotonic() - started) * 1000.0,
            )

        initial_count = math.prod(
            len(item.values) for item in self.program.finite_inputs
        ) if self.program.finite_inputs else 1
        if initial_count > bounds.max_states:
            return SearchReport(
                Verdict.UNKNOWN,
                ("initial-valuations-exceed-state-bound",),
                0,
                0,
                0,
                initial_count,
                False,
                (),
                (time.monotonic() - started) * 1000.0,
            )
        initials = self.initial_states()
        if wall_time_exhausted():
            return SearchReport(
                Verdict.UNKNOWN,
                ("wall-time-bound-exhausted",),
                0,
                len(initials),
                0,
                len(initials),
                False,
                (),
                (time.monotonic() - started) * 1000.0,
            )
        queue = deque(initials)
        visited = set(initials)
        predecessor: dict[SearchState, tuple[Optional[SearchState], Optional[Transition]]] = {
            state: (None, None) for state in initials
        }
        terminal_records: list[tuple[SearchState, Transition]] = []
        expanded = 0
        transitions = 0
        max_queue = len(queue)
        exhausted = True
        bound_reason = None

        while queue:
            if wall_time_exhausted():
                exhausted = False
                bound_reason = "wall-time-bound-exhausted"
                break
            state = queue.popleft()
            expanded += 1
            point_terminal = self._point_check_transition(state)
            # Check the deadline again after calling an external property
            # checker.  A checker that returns after the declared wall-time
            # budget must never certify a state or report a late witness as a
            # bounded-search result.
            if wall_time_exhausted():
                exhausted = False
                bound_reason = "wall-time-bound-exhausted"
                break
            if point_terminal is not None:
                terminal_records.append((state, point_terminal))
                if point_terminal.terminal.verdict is Verdict.VIOLATED:
                    return self._report_from_terminal(
                        started,
                        point_terminal.terminal.verdict,
                        (point_terminal.terminal.reason,),
                        visited,
                        expanded,
                        transitions,
                        max_queue,
                        False,
                        predecessor,
                        state,
                        point_terminal,
                    )
                # UNKNOWN/INVALID proof obligations do not change the nominal
                # discrete state.  Continue exploring so an independent,
                # reachable protocol violation or malformed action is not
                # hidden by a missing geometry certificate.  The recorded
                # terminal still prevents a final VERIFIED verdict.
            successor_transitions = self.successors(
                state, max_time_ns=bounds.max_time_ns
            )
            # successors() may invoke the interval checker.  Treat an
            # over-budget return as UNKNOWN before consuming any returned
            # transition, including a collision witness.
            if wall_time_exhausted():
                exhausted = False
                bound_reason = "wall-time-bound-exhausted"
                break
            for transition in successor_transitions:
                if wall_time_exhausted():
                    exhausted = False
                    bound_reason = "wall-time-bound-exhausted"
                    break
                if transitions >= bounds.max_transitions:
                    exhausted = False
                    bound_reason = "transition-bound-exhausted"
                    break
                transitions += 1
                if transition.terminal is not None:
                    terminal_records.append((state, transition))
                    if transition.terminal.verdict is Verdict.VIOLATED:
                        return self._report_from_terminal(
                            started,
                            transition.terminal.verdict,
                            (transition.terminal.reason,),
                            visited,
                            expanded,
                            transitions,
                            max_queue,
                            False,
                            predecessor,
                            state,
                            transition,
                        )
                    continue
                target = transition.target
                if target not in visited:
                    if len(visited) >= bounds.max_states:
                        exhausted = False
                        bound_reason = "state-bound-exhausted"
                        break
                    visited.add(target)
                    predecessor[target] = (state, transition)
                    queue.append(target)
                    max_queue = max(max_queue, len(queue))
            if not exhausted:
                break

        # A final deadline check closes the edge case where the final
        # transition emptied the queue just as the wall-time budget expired.
        if exhausted and wall_time_exhausted():
            exhausted = False
            bound_reason = "wall-time-bound-exhausted"

        elapsed = (time.monotonic() - started) * 1000.0
        if not exhausted:
            # Resource exhaustion dominates every non-violation obligation.
            # A partial search cannot classify the whole program INVALID or
            # VERIFIED merely because such a terminal was seen before the
            # budget ran out.  Reachable violations return immediately above.
            incomplete_records = [
                record
                for record in terminal_records
                if record[1].terminal.verdict in {Verdict.INVALID, Verdict.UNKNOWN}
            ]
            if incomplete_records:
                state, transition = incomplete_records[0]
                reasons = tuple(
                    sorted(
                        {bound_reason}
                        | {
                            record[1].terminal.reason
                            for record in incomplete_records
                        }
                    )
                )
                return self._report_from_terminal(
                    started,
                    Verdict.UNKNOWN,
                    reasons,
                    visited,
                    expanded,
                    transitions,
                    max_queue,
                    False,
                    predecessor,
                    state,
                    transition,
                )
            return SearchReport(
                Verdict.UNKNOWN,
                (bound_reason,),
                expanded,
                len(visited),
                transitions,
                max_queue,
                False,
                (),
                elapsed,
            )
        unknown = [
            record for record in terminal_records if record[1].terminal.verdict is Verdict.UNKNOWN
        ]
        analysis_limit = [
            record
            for record in unknown
            if _is_path_local_analysis_limit(record[1].terminal)
        ]
        if analysis_limit:
            # The worklist can be empty even though a path-local model-time or
            # refinement limit prevented certification. In that mixed case,
            # fail closed as UNKNOWN before considering runtime INVALID. A
            # qualified violation has already returned above, and static
            # configuration invalidity was handled before search began.
            state, transition = analysis_limit[0]
            reasons = tuple(
                sorted({record[1].terminal.reason for record in analysis_limit})
            )
            return self._report_from_terminal(
                started,
                Verdict.UNKNOWN,
                reasons,
                visited,
                expanded,
                transitions,
                max_queue,
                True,
                predecessor,
                state,
                transition,
            )
        invalid = [
            record for record in terminal_records if record[1].terminal.verdict is Verdict.INVALID
        ]
        if invalid:
            state, transition = invalid[0]
            reasons = tuple(sorted({record[1].terminal.reason for record in invalid}))
            return self._report_from_terminal(
                started,
                Verdict.INVALID,
                reasons,
                visited,
                expanded,
                transitions,
                max_queue,
                True,
                predecessor,
                state,
                transition,
            )
        if unknown:
            state, transition = unknown[0]
            reasons = tuple(sorted({record[1].terminal.reason for record in unknown}))
            return self._report_from_terminal(
                started,
                Verdict.UNKNOWN,
                reasons,
                visited,
                expanded,
                transitions,
                max_queue,
                True,
                predecessor,
                state,
                transition,
            )
        return SearchReport(
            Verdict.VERIFIED,
            (),
            expanded,
            len(visited),
            transitions,
            max_queue,
            True,
            (),
            elapsed,
        )

    def _point_check_transition(self, state: SearchState) -> Optional[Transition]:
        try:
            assessment = self.point_checker(state)
        except Exception as error:  # A failed geometry backend can never certify safety.
            return self._terminal_transition(
                state,
                Verdict.UNKNOWN,
                f"point-checker-error:{type(error).__name__}",
                self._current_node_ids(state),
                self._current_sources(state),
                unknown_kind=_exception_unknown_kind(error),
            )
        if not isinstance(assessment, PointAssessment):
            return self._terminal_transition(
                state,
                Verdict.UNKNOWN,
                "point-checker-invalid-return",
                self._current_node_ids(state),
                self._current_sources(state),
            )
        if assessment.time_ns != state.time_ns:
            return self._terminal_transition(
                state,
                Verdict.UNKNOWN,
                "point-checker-returned-wrong-time",
                self._current_node_ids(state),
                self._current_sources(state),
            )
        if assessment.status in {
            IntervalStatus.COLLISION,
            IntervalStatus.VIOLATION,
        }:
            return self._terminal_transition(
                state,
                Verdict.VIOLATED,
                assessment.reason,
                self._current_node_ids(state),
                self._current_sources(state),
                point=assessment,
            )
        if assessment.status is IntervalStatus.UNKNOWN:
            return self._terminal_transition(
                state,
                Verdict.UNKNOWN,
                assessment.reason,
                self._current_node_ids(state),
                self._current_sources(state),
                point=assessment,
            )
        if assessment.status is not IntervalStatus.SAFE:
            return self._terminal_transition(
                state,
                Verdict.UNKNOWN,
                "point-checker-returned-invalid-status",
                self._current_node_ids(state),
                self._current_sources(state),
                point=assessment,
            )
        return None

    def _current_node_ids(self, state: SearchState) -> tuple[str, ...]:
        return tuple(
            node_id
            for node_id in (state.pc_main, state.pc_left, state.pc_right)
            if node_id is not None
        )

    def _current_sources(self, state: SearchState) -> tuple[SourceSpan, ...]:
        return tuple(self.nodes[node_id].source for node_id in self._current_node_ids(state))

    def _report_from_terminal(
        self,
        started: float,
        verdict: Verdict,
        reasons: tuple[str, ...],
        visited: set[SearchState],
        expanded: int,
        transitions: int,
        max_queue: int,
        exhausted: bool,
        predecessor: dict[SearchState, tuple[Optional[SearchState], Optional[Transition]]],
        state: SearchState,
        terminal_transition: Transition,
    ) -> SearchReport:
        path = []
        current = state
        while predecessor[current][0] is not None:
            previous, transition = predecessor[current]
            path.append(self._trace_step(transition))
            current = previous
        path.reverse()
        path.append(self._trace_step(terminal_transition))
        return SearchReport(
            verdict,
            reasons,
            expanded,
            len(visited),
            transitions,
            max_queue,
            exhausted,
            tuple(path),
            (time.monotonic() - started) * 1000.0,
        )

    def _trace_step(self, transition: Transition) -> TraceStep:
        sources = (
            transition.terminal.sources
            if transition.terminal is not None
            else tuple(self.nodes[node_id].source for node_id in transition.node_ids)
        )
        return TraceStep(
            transition.kind,
            transition.label,
            state_fingerprint(transition.source),
            state_fingerprint(transition.target) if transition.target is not None else None,
            transition.source.time_ns,
            transition.target.time_ns if transition.target is not None else None,
            (
                transition.source.pc_main,
                transition.source.pc_left,
                transition.source.pc_right,
            ),
            (
                (
                    transition.target.pc_main,
                    transition.target.pc_left,
                    transition.target.pc_right,
                )
                if transition.target is not None
                else None
            ),
            transition.source.valuation,
            transition.source.world,
            transition.target.world if transition.target is not None else None,
            transition.node_ids,
            sources,
            transition.terminal.reason if transition.terminal is not None else None,
            transition.interval,
            transition.point,
        )

    def _outcome(
        self,
        verdict: Verdict,
        reason: str,
        nodes: tuple[ActionNode, ...],
        interval: Optional[IntervalAssessment] = None,
        point: Optional[PointAssessment] = None,
        unknown_kind: UnknownKind = UnknownKind.NON_LIMIT,
    ) -> TerminalOutcome:
        return TerminalOutcome(
            verdict,
            reason,
            tuple(node.node_id for node in nodes),
            tuple(node.source for node in nodes),
            interval,
            point,
            unknown_kind,
        )

    def _terminal_transition(
        self,
        state: SearchState,
        verdict: Verdict,
        reason: str,
        node_ids: tuple[str, ...],
        sources: tuple[SourceSpan, ...],
        interval: Optional[IntervalAssessment] = None,
        point: Optional[PointAssessment] = None,
        unknown_kind: UnknownKind = UnknownKind.NON_LIMIT,
    ) -> Transition:
        if interval is not None and interval.status is IntervalStatus.UNKNOWN:
            unknown_kind = interval.unknown_kind
        if point is not None and point.status is IntervalStatus.UNKNOWN:
            unknown_kind = point.unknown_kind
        return Transition(
            EdgeKind.TERMINAL,
            reason,
            state,
            None,
            node_ids,
            TerminalOutcome(
                verdict,
                reason,
                node_ids,
                sources,
                interval,
                point,
                unknown_kind,
            ),
            interval,
            point,
        )

    def _transition(
        self,
        kind: EdgeKind,
        label: str,
        source: SearchState,
        target: SearchState,
        node_ids: tuple[str, ...],
        interval: Optional[IntervalAssessment] = None,
    ) -> Transition:
        issue = target.invariant_issue()
        if issue:
            return self._terminal_transition(
                source,
                Verdict.INVALID,
                f"successor-invariant:{issue}",
                node_ids,
                tuple(self.nodes[node_id].source for node_id in node_ids),
            )
        return Transition(kind, label, source, target, node_ids, None, interval)
