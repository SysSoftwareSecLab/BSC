"""Typed finite Timed Bimanual IR and static well-formedness checks.

This module is the first Stage 2 implementation layer.  It represents the
finite CFG consumed by the future successor generator.  It does not parse
Python, execute programs, explore states, or establish the BMC claim.
"""

from __future__ import annotations

import hashlib
import json
import re
from dataclasses import dataclass, fields, is_dataclass
from enum import Enum
from typing import Any, Iterable, Optional, Union


IDENTIFIER = re.compile(r"^[A-Za-z_][A-Za-z0-9_.:-]*$")
SHA256 = re.compile(r"^[0-9a-f]{64}$")


class Arm(str, Enum):
    LEFT = "left"
    RIGHT = "right"


class ActionKind(str, Enum):
    MOVE = "move"
    WAIT = "wait"
    CLOSE = "close"
    OPEN = "open"
    ACQUIRE = "acquire"
    RELEASE = "release"
    TRANSFER_AUTHORITY = "transfer_authority"


class PredicateOp(str, Enum):
    IS_TRUE = "is_true"
    IS_FALSE = "is_false"
    EQUALS = "equals"
    NOT_EQUALS = "not_equals"


@dataclass(frozen=True)
class SourceSpan:
    file: str
    line: int
    column: int
    end_line: int
    end_column: int


@dataclass(frozen=True)
class FiniteInput:
    name: str
    values: tuple[Union[bool, int, str], ...]


@dataclass(frozen=True)
class PredicateSpec:
    input_name: str
    op: PredicateOp
    compare_value: Optional[Union[bool, int, str]] = None


@dataclass(frozen=True)
class ActionSpec:
    kind: ActionKind
    duration_ns: int
    arm: Optional[Arm] = None
    trajectory_hash: Optional[str] = None
    object_id: Optional[str] = None
    resource_id: Optional[str] = None
    sender: Optional[Arm] = None
    receiver: Optional[Arm] = None


@dataclass(frozen=True)
class IRNode:
    node_id: str
    source: SourceSpan


@dataclass(frozen=True)
class NopNode(IRNode):
    next_id: str


@dataclass(frozen=True)
class ActionNode(IRNode):
    action: ActionSpec
    next_id: str


@dataclass(frozen=True)
class BranchNode(IRNode):
    predicate: PredicateSpec
    true_id: str
    false_id: str


@dataclass(frozen=True)
class LoopHeadNode(IRNode):
    loop_id: str
    bound: int
    body_id: str
    exit_id: str


@dataclass(frozen=True)
class LoopLatchNode(IRNode):
    loop_id: str
    head_id: str


@dataclass(frozen=True)
class ForkNode(IRNode):
    par_id: str
    left_entry: str
    right_entry: str
    join_id: str


@dataclass(frozen=True)
class BarrierNode(IRNode):
    barrier_id: str
    participants: tuple[Arm, ...]
    next_id: str


@dataclass(frozen=True)
class JoinNode(IRNode):
    par_id: str
    next_id: str


@dataclass(frozen=True)
class EndNode(IRNode):
    pass


Node = Union[
    NopNode,
    ActionNode,
    BranchNode,
    LoopHeadNode,
    LoopLatchNode,
    ForkNode,
    BarrierNode,
    JoinNode,
    EndNode,
]


@dataclass(frozen=True)
class TimedProgram:
    name: str
    entry_id: str
    nodes: tuple[Node, ...]
    finite_inputs: tuple[FiniteInput, ...]
    object_ids: tuple[str, ...]
    resource_ids: tuple[str, ...]
    trajectory_hashes: tuple[str, ...]
    environment_hash: str
    max_loop_bound: int
    # Present for parser-produced IR.  Directly constructed typed IR may omit
    # it because its canonical IR bytes are already the complete source
    # identity.  Keeping this field in the canonical serialization ensures
    # even semantically inert source edits remain visible to provenance.
    source_sha256: Optional[str] = None


@dataclass(frozen=True, order=True)
class ValidationIssue:
    code: str
    node_id: Optional[str]
    message: str


class InvalidTimedIR(ValueError):
    def __init__(self, issues: Iterable[ValidationIssue]):
        self.issues = tuple(issues)
        detail = "; ".join(
            f"{issue.code}@{issue.node_id or '<program>'}: {issue.message}"
            for issue in self.issues
        )
        super().__init__(detail)


def successor_ids(node: Node) -> tuple[str, ...]:
    if isinstance(node, (NopNode, ActionNode, BarrierNode, JoinNode)):
        return (node.next_id,)
    if isinstance(node, BranchNode):
        return (node.true_id, node.false_id)
    if isinstance(node, LoopHeadNode):
        return (node.body_id, node.exit_id)
    if isinstance(node, LoopLatchNode):
        return (node.head_id,)
    if isinstance(node, ForkNode):
        return (node.left_entry, node.right_entry)
    return ()


def _typed_value_key(value: Union[bool, int, str]) -> tuple[type, Any]:
    return (type(value), value)


def _valid_span(span: SourceSpan) -> bool:
    if not span.file or span.line < 1 or span.column < 0:
        return False
    if span.end_line < span.line or span.end_column < 0:
        return False
    return span.end_line != span.line or span.end_column >= span.column


def _strongly_connected_components(
    node_ids: set[str], adjacency: dict[str, tuple[str, ...]]
) -> list[set[str]]:
    index = 0
    indices: dict[str, int] = {}
    lowlinks: dict[str, int] = {}
    stack: list[str] = []
    on_stack: set[str] = set()
    components: list[set[str]] = []

    def visit(node_id: str) -> None:
        nonlocal index
        indices[node_id] = index
        lowlinks[node_id] = index
        index += 1
        stack.append(node_id)
        on_stack.add(node_id)
        for target in adjacency.get(node_id, ()):
            if target not in node_ids:
                continue
            if target not in indices:
                visit(target)
                lowlinks[node_id] = min(lowlinks[node_id], lowlinks[target])
            elif target in on_stack:
                lowlinks[node_id] = min(lowlinks[node_id], indices[target])
        if lowlinks[node_id] == indices[node_id]:
            component: set[str] = set()
            while True:
                member = stack.pop()
                on_stack.remove(member)
                component.add(member)
                if member == node_id:
                    break
            components.append(component)

    for node_id in sorted(node_ids):
        if node_id not in indices:
            visit(node_id)
    return components


def _nodes_before_join(
    entry_id: str,
    join_id: str,
    nodes: dict[str, Node],
) -> tuple[set[str], bool]:
    region: set[str] = set()
    reached_join = False
    pending = [entry_id]
    while pending:
        node_id = pending.pop()
        if node_id == join_id:
            reached_join = True
            continue
        if node_id in region or node_id not in nodes:
            continue
        region.add(node_id)
        node = nodes[node_id]
        if isinstance(node, (EndNode, JoinNode)):
            continue
        pending.extend(successor_ids(node))
    return region, reached_join


def _dominators(
    entry_id: str,
    node_ids: set[str],
    adjacency: dict[str, tuple[str, ...]],
) -> dict[str, set[str]]:
    """Return exact graph dominators for one finite parallel branch."""

    predecessors = {node_id: set() for node_id in node_ids}
    for node_id in node_ids:
        for target in adjacency.get(node_id, ()):
            if target in node_ids:
                predecessors[target].add(node_id)
    dominators = {
        node_id: ({entry_id} if node_id == entry_id else set(node_ids))
        for node_id in node_ids
    }
    changed = True
    while changed:
        changed = False
        for node_id in sorted(node_ids - {entry_id}):
            incoming = predecessors[node_id]
            if not incoming:
                updated = {node_id}
            else:
                common = set(node_ids)
                for predecessor in incoming:
                    common &= dominators[predecessor]
                updated = {node_id} | common
            if updated != dominators[node_id]:
                dominators[node_id] = updated
                changed = True
    return dominators


def _barrier_sequence_before_join(
    entry_id: str,
    join_id: str,
    region: set[str],
    nodes: dict[str, Node],
    adjacency: dict[str, tuple[str, ...]],
) -> tuple[Optional[tuple[str, ...]], tuple[str, ...]]:
    """Derive an unconditional ordered barrier sequence for one branch.

    Parser-produced barriers are top-level branch statements.  For direct
    typed IR, domination of the join proves that no path bypasses a barrier;
    rejecting barriers in cycles prevents repeat-dependent arrivals.
    """

    branch_nodes = set(region) | {join_id}
    dominators = _dominators(entry_id, branch_nodes, adjacency)
    barriers = sorted(
        node_id for node_id in region if isinstance(nodes[node_id], BarrierNode)
    )
    issue_codes: list[str] = []
    for node_id in barriers:
        if node_id not in dominators.get(join_id, set()):
            issue_codes.append("CONDITIONAL_BARRIER")

    for component in _strongly_connected_components(branch_nodes, adjacency):
        is_cycle = len(component) > 1 or any(
            node_id in adjacency.get(node_id, ()) for node_id in component
        )
        if is_cycle and any(node_id in component for node_id in barriers):
            issue_codes.append("CONDITIONAL_BARRIER")

    ordered = sorted(barriers, key=lambda node_id: (len(dominators[node_id]), node_id))
    for earlier, later in zip(ordered, ordered[1:]):
        if earlier not in dominators[later]:
            issue_codes.append("BARRIER_ORDER")
    if issue_codes:
        return None, tuple(sorted(set(issue_codes)))
    return tuple(nodes[node_id].barrier_id for node_id in ordered), ()


def _action_issues(
    node: ActionNode,
    objects: set[str],
    resources: set[str],
    trajectories: set[str],
) -> list[ValidationIssue]:
    action = node.action
    issues: list[ValidationIssue] = []

    def issue(code: str, message: str) -> None:
        issues.append(ValidationIssue(code, node.node_id, message))

    if not isinstance(action.kind, ActionKind):
        issue("ACTION_KIND", "action kind must be an ActionKind")
        return issues
    for label, arm in (
        ("arm", action.arm),
        ("sender", action.sender),
        ("receiver", action.receiver),
    ):
        if arm is not None and not isinstance(arm, Arm):
            issue("ACTION_ARM_TYPE", f"{label} must be an Arm")

    if isinstance(action.duration_ns, bool) or not isinstance(action.duration_ns, int):
        issue("ACTION_DURATION_TYPE", "duration_ns must be an integer")
        return issues

    timed = {
        ActionKind.MOVE,
        ActionKind.WAIT,
        ActionKind.CLOSE,
        ActionKind.OPEN,
    }
    if action.kind in timed and action.duration_ns <= 0:
        issue("ACTION_DURATION", "timed actions require positive duration_ns")
    if action.kind not in timed and action.duration_ns != 0:
        issue("ACTION_DURATION", "zero-time actions require duration_ns == 0")

    if action.kind is ActionKind.TRANSFER_AUTHORITY:
        if action.arm is not None:
            issue("ACTION_FIELDS", "transfer_authority must not set arm")
        if action.sender is None or action.receiver is None or action.sender == action.receiver:
            issue("ACTION_TRANSFER", "transfer requires distinct sender and receiver")
        if action.object_id not in objects:
            issue("ACTION_OBJECT", "transfer object is not declared")
    else:
        if action.arm is None:
            issue("ACTION_ARM", "arm action requires an arm")
        if action.sender is not None or action.receiver is not None:
            issue("ACTION_FIELDS", "non-transfer action must not set sender/receiver")

    if action.kind is ActionKind.MOVE:
        if action.trajectory_hash not in trajectories:
            issue("ACTION_TRAJECTORY", "move trajectory hash is not declared")
    elif action.trajectory_hash is not None:
        issue("ACTION_FIELDS", "only move may set trajectory_hash")

    if action.kind in {ActionKind.CLOSE, ActionKind.OPEN}:
        if action.object_id not in objects:
            issue("ACTION_OBJECT", "gripper object is not declared")
    elif action.kind is not ActionKind.TRANSFER_AUTHORITY and action.object_id is not None:
        issue("ACTION_FIELDS", "this action must not set object_id")

    if action.kind in {ActionKind.ACQUIRE, ActionKind.RELEASE}:
        if action.resource_id not in resources:
            issue("ACTION_RESOURCE", "resource is not declared")
    elif action.resource_id is not None:
        issue("ACTION_FIELDS", "this action must not set resource_id")
    return issues


def validate_program(program: TimedProgram) -> tuple[ValidationIssue, ...]:
    """Return every deterministic static well-formedness issue found."""
    issues: list[ValidationIssue] = []

    def issue(code: str, message: str, node_id: Optional[str] = None) -> None:
        issues.append(ValidationIssue(code, node_id, message))

    if not program.name:
        issue("PROGRAM_NAME", "program name must be non-empty")
    if not SHA256.fullmatch(program.environment_hash):
        issue("ENVIRONMENT_HASH", "environment_hash must be a lowercase SHA-256")
    if program.source_sha256 is not None and not SHA256.fullmatch(program.source_sha256):
        issue("SOURCE_HASH", "source_sha256 must be a lowercase SHA-256 when present")
    if (
        isinstance(program.max_loop_bound, bool)
        or not isinstance(program.max_loop_bound, int)
        or program.max_loop_bound < 0
    ):
        issue("MAX_LOOP_BOUND", "max_loop_bound must be a non-negative integer")

    for label, values in (
        ("object", program.object_ids),
        ("resource", program.resource_ids),
    ):
        if len(values) != len(set(values)):
            issue(f"DUPLICATE_{label.upper()}", f"{label} identifiers must be unique")
        for value in values:
            if not IDENTIFIER.fullmatch(value):
                issue(f"INVALID_{label.upper()}", f"invalid {label} identifier: {value!r}")

    trajectories = set(program.trajectory_hashes)
    if len(trajectories) != len(program.trajectory_hashes):
        issue("DUPLICATE_TRAJECTORY", "trajectory hashes must be unique")
    for trajectory_hash in trajectories:
        if not SHA256.fullmatch(trajectory_hash):
            issue("TRAJECTORY_HASH", f"invalid trajectory SHA-256: {trajectory_hash!r}")

    input_names: set[str] = set()
    input_domains: dict[str, tuple[Union[bool, int, str], ...]] = {}
    for finite_input in program.finite_inputs:
        if not IDENTIFIER.fullmatch(finite_input.name):
            issue("INPUT_NAME", f"invalid finite input name: {finite_input.name!r}")
        if finite_input.name in input_names:
            issue("DUPLICATE_INPUT", f"duplicate finite input: {finite_input.name}")
        input_names.add(finite_input.name)
        input_domains[finite_input.name] = finite_input.values
        if not finite_input.values:
            issue("EMPTY_INPUT_DOMAIN", f"finite input {finite_input.name} has no values")
        if any(type(value) not in {bool, int, str} for value in finite_input.values):
            issue("INPUT_VALUE_TYPE", f"finite input {finite_input.name} has unsupported values")
        if len({_typed_value_key(value) for value in finite_input.values}) != len(
            finite_input.values
        ):
            issue("DUPLICATE_INPUT_VALUE", f"finite input {finite_input.name} has duplicates")

    nodes: dict[str, Node] = {}
    for node in program.nodes:
        if not IDENTIFIER.fullmatch(node.node_id):
            issue("NODE_ID", f"invalid node identifier: {node.node_id!r}", node.node_id)
        if node.node_id in nodes:
            issue("DUPLICATE_NODE", "node_id must be unique", node.node_id)
        else:
            nodes[node.node_id] = node
        if not _valid_span(node.source):
            issue("SOURCE_SPAN", "invalid or empty source span", node.node_id)

    if program.entry_id not in nodes:
        issue("ENTRY", "entry_id does not reference a node")

    adjacency = {node_id: successor_ids(node) for node_id, node in nodes.items()}
    for node_id, targets in adjacency.items():
        for target in targets:
            if target not in nodes:
                issue("UNRESOLVED_EDGE", f"edge target {target!r} does not exist", node_id)

    reachable: set[str] = set()
    pending = [program.entry_id] if program.entry_id in nodes else []
    while pending:
        node_id = pending.pop()
        if node_id in reachable:
            continue
        reachable.add(node_id)
        pending.extend(target for target in adjacency[node_id] if target in nodes)
    for node_id in sorted(set(nodes) - reachable):
        issue("UNREACHABLE_NODE", "node is unreachable from entry", node_id)
    if not any(isinstance(nodes[node_id], EndNode) for node_id in reachable):
        issue("NO_REACHABLE_END", "no End node is reachable from entry")

    objects = set(program.object_ids)
    resources = set(program.resource_ids)
    loop_heads: dict[str, LoopHeadNode] = {}
    loop_latches: dict[str, list[LoopLatchNode]] = {}
    forks: dict[str, ForkNode] = {}
    joins: dict[str, JoinNode] = {}

    for node in nodes.values():
        if isinstance(node, ActionNode):
            issues.extend(_action_issues(node, objects, resources, trajectories))
        elif isinstance(node, BranchNode):
            predicate = node.predicate
            if not isinstance(predicate.op, PredicateOp):
                issue("PREDICATE_OP", "predicate op must be a PredicateOp", node.node_id)
            if predicate.input_name not in input_names:
                issue(
                    "PREDICATE_INPUT",
                    f"unknown finite input {predicate.input_name!r}",
                    node.node_id,
                )
            else:
                domain = input_domains[predicate.input_name]
                if predicate.op in {PredicateOp.IS_TRUE, PredicateOp.IS_FALSE}:
                    if any(type(value) is not bool for value in domain):
                        issue(
                            "PREDICATE_DOMAIN",
                            "truth predicates require a boolean-only input domain",
                            node.node_id,
                        )
                    if predicate.compare_value is not None:
                        issue(
                            "PREDICATE_VALUE",
                            "truth predicates must not set compare_value",
                            node.node_id,
                        )
                elif predicate.op in {PredicateOp.EQUALS, PredicateOp.NOT_EQUALS}:
                    if predicate.compare_value is None or _typed_value_key(
                        predicate.compare_value
                    ) not in {_typed_value_key(value) for value in domain}:
                        issue(
                            "PREDICATE_VALUE",
                            "comparison value must occur in the finite input domain",
                            node.node_id,
                        )
        elif isinstance(node, LoopHeadNode):
            if not IDENTIFIER.fullmatch(node.loop_id):
                issue("LOOP_ID", "invalid loop identifier", node.node_id)
            if node.loop_id in loop_heads:
                issue("DUPLICATE_LOOP", f"duplicate loop_id {node.loop_id}", node.node_id)
            loop_heads[node.loop_id] = node
            if isinstance(node.bound, bool) or not isinstance(node.bound, int):
                issue("LOOP_BOUND", "loop bound must be an integer", node.node_id)
            elif node.bound < 0 or (
                isinstance(program.max_loop_bound, int)
                and not isinstance(program.max_loop_bound, bool)
                and node.bound > program.max_loop_bound
            ):
                issue("LOOP_BOUND", "loop bound is outside the declared maximum", node.node_id)
            if node.body_id == node.exit_id:
                issue("LOOP_EDGES", "loop body and exit must differ", node.node_id)
        elif isinstance(node, LoopLatchNode):
            if not IDENTIFIER.fullmatch(node.loop_id):
                issue("LOOP_ID", "invalid loop identifier", node.node_id)
            loop_latches.setdefault(node.loop_id, []).append(node)
        elif isinstance(node, ForkNode):
            if not IDENTIFIER.fullmatch(node.par_id):
                issue("PAR_ID", "invalid parallel-region identifier", node.node_id)
            if node.par_id in forks:
                issue("DUPLICATE_PAR", f"duplicate par_id {node.par_id}", node.node_id)
            forks[node.par_id] = node
            if node.left_entry == node.right_entry:
                issue("PAR_BRANCHES", "parallel branches require distinct entries", node.node_id)
        elif isinstance(node, JoinNode):
            if not IDENTIFIER.fullmatch(node.par_id):
                issue("PAR_ID", "invalid parallel-region identifier", node.node_id)
            if node.par_id in joins:
                issue("DUPLICATE_JOIN", f"duplicate join for {node.par_id}", node.node_id)
            joins[node.par_id] = node
        elif isinstance(node, BarrierNode):
            if not IDENTIFIER.fullmatch(node.barrier_id):
                issue("BARRIER_ID", "invalid barrier identifier", node.node_id)
            if set(node.participants) != {Arm.LEFT, Arm.RIGHT} or len(node.participants) != 2:
                issue(
                    "BARRIER_PARTICIPANTS",
                    "Paper 1 barriers require exactly left and right participants",
                    node.node_id,
                )

    for loop_id, head in loop_heads.items():
        latches = loop_latches.get(loop_id, [])
        if not latches:
            issue("LOOP_LATCH", f"loop {loop_id} has no latch", head.node_id)
        for latch in latches:
            if latch.head_id != head.node_id:
                issue("LOOP_LATCH", "latch does not target its loop head", latch.node_id)
    for loop_id, latches in loop_latches.items():
        if loop_id not in loop_heads:
            for latch in latches:
                issue("LOOP_HEAD", f"unknown loop_id {loop_id}", latch.node_id)

    for component in _strongly_connected_components(reachable, adjacency):
        is_cycle = len(component) > 1 or any(
            node_id in adjacency.get(node_id, ()) for node_id in component
        )
        if not is_cycle:
            continue
        heads = [nodes[node_id] for node_id in component if isinstance(nodes[node_id], LoopHeadNode)]
        if len(heads) != 1:
            issue(
                "UNSTRUCTURED_CYCLE",
                "every CFG cycle must contain exactly one LoopHead",
                min(component),
            )
            continue
        head = heads[0]
        latches = [nodes[node_id] for node_id in component if isinstance(nodes[node_id], LoopLatchNode)]
        if not latches or any(
            latch.loop_id != head.loop_id or latch.head_id != head.node_id for latch in latches
        ):
            issue("UNSTRUCTURED_CYCLE", "loop cycle has an invalid latch", head.node_id)
        for node_id in component - {head.node_id}:
            if isinstance(nodes[node_id], LoopLatchNode):
                continue
            if any(target not in component for target in adjacency.get(node_id, ())):
                issue(
                    "UNSTRUCTURED_LOOP_EXIT",
                    "only LoopHead.exit_id may leave a repeat body",
                    node_id,
                )

    nodes_in_parallel_region: set[str] = set()
    for par_id, fork in forks.items():
        join = nodes.get(fork.join_id)
        if not isinstance(join, JoinNode) or join.par_id != par_id:
            issue("PAR_JOIN", "fork join_id must reference the matching Join", fork.node_id)
            continue
        if joins.get(par_id) != join:
            issue("PAR_JOIN", "parallel region has inconsistent join mapping", fork.node_id)
        left_region, left_reached = _nodes_before_join(fork.left_entry, fork.join_id, nodes)
        right_region, right_reached = _nodes_before_join(fork.right_entry, fork.join_id, nodes)
        if not left_reached or not right_reached:
            issue("PAR_JOIN_REACHABILITY", "both branches must reach the matching Join", fork.node_id)
        overlap = left_region & right_region
        if overlap:
            issue(
                "PAR_BRANCH_OVERLAP",
                f"parallel branches share nodes before Join: {sorted(overlap)}",
                fork.node_id,
            )
        nodes_in_parallel_region.update(left_region | right_region)
        for branch_arm, region in ((Arm.LEFT, left_region), (Arm.RIGHT, right_region)):
            barriers: dict[str, int] = {}
            for node_id in region:
                node = nodes[node_id]
                if isinstance(node, ForkNode):
                    issue("NESTED_PARALLEL", "nested parallel regions are unsupported", node_id)
                if isinstance(node, EndNode):
                    issue("PAR_EARLY_END", "parallel branch ends before Join", node_id)
                if isinstance(node, JoinNode):
                    issue("PAR_WRONG_JOIN", "parallel branch reaches a different Join", node_id)
                if isinstance(node, ActionNode):
                    arm = node.action.arm
                    if isinstance(arm, Arm) and arm is not branch_arm:
                        issue(
                            "PAR_ARM_AFFINITY",
                            f"{branch_arm.value} branch contains {arm.value} arm action",
                            node_id,
                        )
                if isinstance(node, BarrierNode):
                    barriers[node.barrier_id] = barriers.get(node.barrier_id, 0) + 1
            if any(count != 1 for count in barriers.values()):
                issue(
                    "BARRIER_MULTIPLICITY",
                    "a barrier may appear once per parallel branch",
                    fork.node_id,
                )
        left_barriers = {
            nodes[node_id].barrier_id
            for node_id in left_region
            if isinstance(nodes[node_id], BarrierNode)
        }
        right_barriers = {
            nodes[node_id].barrier_id
            for node_id in right_region
            if isinstance(nodes[node_id], BarrierNode)
        }
        if left_barriers != right_barriers:
            issue("BARRIER_PAIRING", "barrier IDs must be paired across branches", fork.node_id)
        left_sequence, left_sequence_issues = _barrier_sequence_before_join(
            fork.left_entry, fork.join_id, left_region, nodes, adjacency
        )
        right_sequence, right_sequence_issues = _barrier_sequence_before_join(
            fork.right_entry, fork.join_id, right_region, nodes, adjacency
        )
        for code in sorted(set(left_sequence_issues + right_sequence_issues)):
            message = (
                "barriers must be unconditional and outside repeat cycles"
                if code == "CONDITIONAL_BARRIER"
                else "barriers in one branch do not have a single structural order"
            )
            issue(code, message, fork.node_id)
        if (
            left_sequence is not None
            and right_sequence is not None
            and left_sequence != right_sequence
        ):
            issue(
                "BARRIER_SEQUENCE",
                "left and right branches require the same ordered barrier sequence",
                fork.node_id,
            )

    for par_id, join in joins.items():
        if par_id not in forks:
            issue("ORPHAN_JOIN", f"Join has no matching Fork {par_id}", join.node_id)
    for node in nodes.values():
        if isinstance(node, BarrierNode) and node.node_id not in nodes_in_parallel_region:
            issue("ORPHAN_BARRIER", "barrier must occur inside a parallel region", node.node_id)

    return tuple(sorted(set(issues)))


def require_valid_program(program: TimedProgram) -> TimedProgram:
    issues = validate_program(program)
    if issues:
        raise InvalidTimedIR(issues)
    return program


def _canonical_value(value: Any) -> Any:
    if isinstance(value, Enum):
        return value.value
    if is_dataclass(value):
        result = {field.name: _canonical_value(getattr(value, field.name)) for field in fields(value)}
        result["type"] = type(value).__name__
        return result
    if isinstance(value, tuple):
        return [_canonical_value(item) for item in value]
    return value


def canonical_program_bytes(program: TimedProgram) -> bytes:
    """Serialize the typed IR deterministically for provenance hashing."""
    require_valid_program(program)
    return (
        json.dumps(
            _canonical_value(program),
            sort_keys=True,
            separators=(",", ":"),
            ensure_ascii=False,
        )
        + "\n"
    ).encode("utf-8")


def program_sha256(program: TimedProgram) -> str:
    return hashlib.sha256(canonical_program_bytes(program)).hexdigest()
