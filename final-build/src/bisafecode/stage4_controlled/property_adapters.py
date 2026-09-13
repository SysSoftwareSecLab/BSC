"""Hash-bound Stage-4 property adapters for the S3-002 full method.

FREEZE_ONLY_NOT_FORMAL_STAGE4_EVIDENCE

This is method code, not independent oracle code.  It imports the frozen
parser/Timed IR/search types and implements a separate analytic point/interval
property over the controlled model.  Missing handover facts remain unknown.
"""

from __future__ import annotations

import ast
import json
import math
import re
from functools import lru_cache
from pathlib import Path
from typing import Any, Mapping, Tuple

from bisafecode.explicit_state import (
    ActionLocator,
    GripperConfiguration,
    IntervalAssessment,
    IntervalStatus,
    JointConfiguration,
    LocatedPrimitiveContract,
    ObjectState,
    PointAssessment,
    PrimitiveContract,
    SearchEnvironment,
    bind_primitive_contracts,
)
from bisafecode.restricted_python import ParserContext, TrajectoryBinding, parse_restricted_python
from bisafecode.timed_ir import ActionKind, ActionNode, FiniteInput
from bisafecode.trajectory import FrozenGripperTrajectory, FrozenJointTrajectory, TrajectoryPoint

from .identity import CONTRACT_DIR, compute_asset_identity, repository_root, sha256_file


MODEL_HASH = "681883708614c393f3372628bb399d1feab3af645135095b82ecbbebfe135a51"
RADII = {"left_tool_sphere": 0.08, "right_tool_sphere": 0.08, "payload_alpha": 0.11, "payload_beta": 0.05}
INITIAL_OBJECT_CENTERS = {"payload_alpha": (0.0, -0.35, 0.30), "payload_beta": (0.0, -0.50, 0.30)}
CHECKED_PAIRS = (
    ("left_tool_sphere", "right_tool_sphere"),
    ("left_tool_sphere", "payload_alpha"),
    ("left_tool_sphere", "payload_beta"),
    ("payload_alpha", "right_tool_sphere"),
    ("payload_beta", "right_tool_sphere"),
)
HANDOVER_ZONE_MIN = (-0.12, 0.18, 0.28)
HANDOVER_ZONE_MAX = (0.12, 0.42, 0.62)
INITIAL_TOOL_CENTERS = {
    "left": (-0.45, 0.30, 0.45),
    "right": (0.45, 0.30, 0.45),
}
INITIAL_DUAL_GRASP_INSIDE_ZONE = all(
    HANDOVER_ZONE_MIN[index] <= value <= HANDOVER_ZONE_MAX[index]
    for center in INITIAL_TOOL_CENTERS.values()
    for index, value in enumerate(center)
)
INITIAL_DUAL_ATTACHMENT_CONSISTENT = math.dist(
    INITIAL_TOOL_CENTERS["left"], INITIAL_TOOL_CENTERS["right"]
) <= 0.18


def _load_trajectories(root: Path) -> Tuple[FrozenJointTrajectory, ...]:
    base = root / CONTRACT_DIR / "assets" / "trajectories"
    values = []
    for path in sorted(base.glob("*.json")):
        if path.name.endswith(".sidecar.json"):
            continue
        document = json.loads(path.read_text(encoding="utf-8"))
        sidecar = json.loads(path.with_name(path.stem + ".sidecar.json").read_text(encoding="utf-8"))
        if sidecar["trajectory_artifact_sha256"] != sha256_file(path):
            raise ValueError("trajectory artifact/sidecar mismatch")
        trajectory = FrozenJointTrajectory(
            arm=document["arm"],
            joint_names=tuple(document["joint_names"]),
            points=tuple(
                TrajectoryPoint.create(
                    time_ns=item["time_ns"], positions=item["positions"], velocities=item["velocities"]
                )
                for item in document["points"]
            ),
            model_hash=MODEL_HASH,
        )
        if trajectory.content_hash != sidecar["trajectory_content_sha256"]:
            raise ValueError("trajectory content identity mismatch")
        values.append(trajectory)
    if len(values) != 4:
        raise ValueError("exactly four controlled trajectories are required")
    return tuple(values)


def trajectory_tool_speed_upper_bounds(
    root: Path | None = None,
) -> Mapping[str, float]:
    """Return content-addressed conservative tool-center speed bounds.

    Frozen trajectory polynomial derivative ranges are outward-rounded by the
    S3-002 trajectory implementation.  The frozen analytic model supplies the
    linear joint-to-tool scaling; no empirical speed constant is used.
    """

    root = (root or repository_root()).resolve()
    model = json.loads(
        (root / CONTRACT_DIR / "assets" / "model.json").read_text(encoding="utf-8")
    )
    arm_formulas = model["semantics"]["arm_center_from_joint_position"]
    observed = []
    for arm in ("left", "right"):
        scales = []
        for axis, formula in enumerate(arm_formulas[arm]):
            match = re.search(r"([0-9]+(?:\.[0-9]+)?)\*q([0-9]+)$", formula)
            if match is None or int(match.group(2)) != axis:
                raise ValueError("frozen tool-center formula is not linear and boundable")
            scales.append(float(match.group(1)))
        observed.append(tuple(scales) + (0.0,) * 4)
    if observed[0] != observed[1] or any(
        value < 0 or not math.isfinite(value) for value in observed[0]
    ):
        raise ValueError("frozen tool-center velocity scales disagree")
    scales = observed[0]
    result = {}
    for trajectory in _load_trajectories(root):
        joint_bounds = trajectory.absolute_derivative_bounds(1)
        squared = sum(
            (scale * joint_bound) ** 2
            for scale, joint_bound in zip(scales, joint_bounds)
        )
        result[trajectory.content_hash] = math.nextafter(math.sqrt(squared), math.inf)
    if len(result) != 4:
        raise ValueError("speed bounds require all four frozen trajectories")
    return dict(sorted(result.items()))


@lru_cache(maxsize=1)
def _cached_live_speed_bounds() -> Mapping[str, float]:
    return trajectory_tool_speed_upper_bounds(repository_root())


def _gripper(arm: str, duration_ns: int, opening: bool) -> FrozenGripperTrajectory:
    start, end = ((0.0, 0.044) if opening else (0.044, 0.0))
    return FrozenGripperTrajectory(
        arm=arm,
        joint_name=f"{arm}_finger_joint1",
        points=(
            TrajectoryPoint.create(time_ns=0, positions=(start,), velocities=(0.0,), dof=1),
            TrajectoryPoint.create(time_ns=duration_ns, positions=(end,), velocities=(0.0,), dof=1),
        ),
        model_hash=MODEL_HASH,
    )


_QState = tuple[tuple[float, ...], tuple[float, ...]]


def _literal_call(statement: ast.stmt) -> tuple[str, list[Any]] | None:
    if not isinstance(statement, ast.Expr) or not isinstance(statement.value, ast.Call):
        return None
    call = statement.value
    if not isinstance(call.func, ast.Name):
        return None
    values: list[Any] = []
    for argument in call.args:
        if isinstance(argument, ast.Constant):
            values.append(argument.value)
        elif isinstance(argument, ast.Name):
            values.append(argument.id)
        else:
            values.append(None)
    return call.func.id, values


def _replace_arm_q(state: _QState, arm: str, q: tuple[float, ...]) -> _QState:
    return (q, state[1]) if arm == "left" else (state[0], q)


def _arm_q(state: _QState, arm: str) -> tuple[float, ...]:
    return state[0] if arm == "left" else state[1]


def _parallel_lane_profile(
    function: ast.FunctionDef,
    state: _QState,
    trajectories: Mapping[str, FrozenJointTrajectory],
) -> Mapping[str, Any]:
    current = state
    stage = "__start__"
    candidates: dict[str, set[_QState]] = {stage: {current}}
    observations: list[tuple[tuple[int, int, str], str, str, _QState]] = []
    active_arms: set[str] = set()
    for statement in function.body:
        parsed = _literal_call(statement)
        if parsed is None:
            continue
        kind, arguments = parsed
        if kind == "barrier":
            stage = f"barrier:{arguments[0]}"
            candidates.setdefault(stage, set()).add(current)
            continue
        arm = str(arguments[0]) if kind in {"move", "wait", "close", "open", "acquire", "release"} else ""
        if arm in {"left", "right"}:
            active_arms.add(arm)
        if kind in {"close", "transfer_authority"}:
            observations.append(
                ((statement.lineno, statement.col_offset, kind), stage, arm, current)
            )
        if kind == "move":
            trajectory = trajectories[str(arguments[1])]
            endpoint = tuple(float(value) for value in trajectory.points[-1].positions)
            current = _replace_arm_q(current, arm, endpoint)
        candidates.setdefault(stage, set()).add(current)
    return {
        "end": current,
        "active_arms": active_arms,
        "candidates": candidates,
        "observations": observations,
    }


def _merge_parallel_profiles(
    function_names: list[str],
    state: _QState,
    *,
    trajectories: Mapping[str, FrozenJointTrajectory],
    functions: Mapping[str, ast.FunctionDef],
    observations: dict[tuple[int, int, str], set[_QState]],
    unresolved: set[tuple[int, int, str]],
) -> set[_QState]:
    if len(function_names) != 2:
        raise ValueError("spatial prepass requires exactly two parallel lanes")
    profiles = [
        _parallel_lane_profile(functions[name], state, trajectories)
        for name in function_names
    ]
    if any(len(profile["active_arms"]) != 1 for profile in profiles):
        for profile in profiles:
            unresolved.update(item[0] for item in profile["observations"])
        return {state}
    arms = [next(iter(profile["active_arms"])) for profile in profiles]
    if arms[0] == arms[1]:
        for profile in profiles:
            unresolved.update(item[0] for item in profile["observations"])
        return {state}
    for index, profile in enumerate(profiles):
        other = profiles[1 - index]
        other_arm = arms[1 - index]
        for key, stage, own_arm, own_state in profile["observations"]:
            other_candidates = other["candidates"].get(stage)
            if own_arm not in {"left", "right"} or not other_candidates:
                unresolved.add(key)
                continue
            for candidate in other_candidates:
                merged = _replace_arm_q(
                    own_state, other_arm, _arm_q(candidate, other_arm)
                )
                observations.setdefault(key, set()).add(merged)
    merged_end = state
    for arm, profile in zip(arms, profiles):
        merged_end = _replace_arm_q(merged_end, arm, _arm_q(profile["end"], arm))
    return {merged_end}


def _execute_spatial_block(
    statements: list[ast.stmt],
    states: set[_QState],
    *,
    trajectories: Mapping[str, FrozenJointTrajectory],
    functions: Mapping[str, ast.FunctionDef],
    observations: dict[tuple[int, int, str], set[_QState]],
    unresolved: set[tuple[int, int, str]],
    inside_parallel: bool = False,
) -> set[_QState]:
    current = set(states)
    for statement in statements:
        if isinstance(statement, ast.If):
            body = _execute_spatial_block(
                statement.body, set(current), trajectories=trajectories,
                functions=functions, observations=observations,
                unresolved=unresolved, inside_parallel=inside_parallel,
            )
            alternate = _execute_spatial_block(
                statement.orelse, set(current), trajectories=trajectories,
                functions=functions, observations=observations,
                unresolved=unresolved, inside_parallel=inside_parallel,
            )
            current = body | alternate
            continue
        if isinstance(statement, ast.For):
            if not isinstance(statement.iter, ast.Call) or not isinstance(statement.iter.func, ast.Name) or statement.iter.func.id != "range":
                raise ValueError("spatial prepass supports only range loops")
            bounds = [
                int(item.value) for item in statement.iter.args
                if isinstance(item, ast.Constant) and isinstance(item.value, int)
            ]
            if len(bounds) != len(statement.iter.args):
                raise ValueError("spatial prepass requires literal range bounds")
            count = len(range(*bounds))
            for _ in range(count):
                current = _execute_spatial_block(
                    statement.body, current, trajectories=trajectories,
                    functions=functions, observations=observations,
                    unresolved=unresolved, inside_parallel=inside_parallel,
                )
            continue
        parsed = _literal_call(statement)
        if parsed is None:
            continue
        kind, arguments = parsed
        key = (statement.lineno, statement.col_offset, kind)
        if kind in {"close", "transfer_authority"}:
            if inside_parallel:
                unresolved.add(key)
            else:
                observations.setdefault(key, set()).update(current)
        if kind == "move":
            arm, trajectory_hash = str(arguments[0]), str(arguments[1])
            trajectory = trajectories[trajectory_hash]
            endpoint = tuple(float(value) for value in trajectory.points[-1].positions)
            current = {_replace_arm_q(state, arm, endpoint) for state in current}
        elif kind == "parallel":
            merged: set[_QState] = set()
            for state in current:
                merged.update(_merge_parallel_profiles(
                    list(map(str, arguments)), state, trajectories=trajectories,
                    functions=functions, observations=observations,
                    unresolved=unresolved,
                ))
            current = merged
    return current


def _tristate(values: list[bool]) -> bool | None:
    if not values or any(value != values[0] for value in values[1:]):
        return None
    return values[0]


def _source_spatial_contracts(
    source: str, trajectories: Tuple[FrozenJointTrajectory, ...]
) -> Mapping[tuple[int, int, str], tuple[bool | None, bool | None]]:
    """Derive close/transfer geometry at the source action, not at t=0.

    The prepass is intentionally conservative.  A source location reached with
    multiple positions receives a boolean only when every reachable position
    agrees.  Spatial facts inside a parallel lane remain unknown because a
    lane-local action needs a schedule-aware binding rather than an endpoint
    summary.
    """

    module = ast.parse(source)
    functions = {
        node.name: node for node in module.body if isinstance(node, ast.FunctionDef)
    }
    by_hash = {item.content_hash: item for item in trajectories}
    observations: dict[tuple[int, int, str], set[_QState]] = {}
    unresolved: set[tuple[int, int, str]] = set()
    zero = (0.0,) * 7
    _execute_spatial_block(
        functions["task"].body, {(zero, zero)}, trajectories=by_hash,
        functions=functions, observations=observations, unresolved=unresolved,
    )
    result: dict[tuple[int, int, str], tuple[bool | None, bool | None]] = {}
    for key, states in observations.items():
        if key in unresolved:
            result[key] = (None, None)
            continue
        zone_values = []
        consistency_values = []
        for left_q, right_q in states:
            centers = (
                (-0.45 + 0.2 * left_q[0], 0.30 + 0.2 * left_q[1], 0.45 + 0.2 * left_q[2]),
                (0.45 + 0.2 * right_q[0], 0.30 + 0.2 * right_q[1], 0.45 + 0.2 * right_q[2]),
            )
            zone_values.append(all(
                HANDOVER_ZONE_MIN[index] <= value <= HANDOVER_ZONE_MAX[index]
                for center in centers for index, value in enumerate(center)
            ))
            consistency_values.append(math.dist(*centers) <= 0.18)
        result[key] = (_tristate(zone_values), _tristate(consistency_values))
    for key in unresolved:
        result.setdefault(key, (None, None))
    return result


def build_full_method_inputs(
    source: str, *, program_id: str, root: Path | None = None
) -> tuple[Any, SearchEnvironment]:
    """Build the actual frozen parser/Timed-IR/explicit-state inputs."""

    root = (root or repository_root()).resolve()
    trajectories = _load_trajectories(root)
    spatial_contracts = _source_spatial_contracts(source, trajectories)
    environment_hash = compute_asset_identity(root)["root_sha256"]
    program = parse_restricted_python(
        source,
        filename=f"locked_programs/{program_id}.py",
        context=ParserContext(
            program_name=program_id,
            finite_inputs=(FiniteInput("mode", ("fast", "safe")), FiniteInput("ready", (False, True))),
            object_ids=("payload_alpha", "payload_beta"),
            resource_ids=("fixture_alpha", "tool_beta"),
            trajectories=tuple(TrajectoryBinding(item.content_hash, item.duration_ns) for item in trajectories),
            environment_hash=environment_hash,
            max_loop_bound=4,
        ),
    )
    grippers = {}
    located = []
    for node in program.nodes:
        if not isinstance(node, ActionNode):
            continue
        contract = PrimitiveContract()
        if node.action.kind in {ActionKind.CLOSE, ActionKind.OPEN}:
            opening = node.action.kind is ActionKind.OPEN
            trajectory = _gripper(node.action.arm.value, node.action.duration_ns, opening)
            grippers[trajectory.content_hash] = trajectory
            spatial = spatial_contracts.get(
                (node.source.line, node.source.column, node.action.kind.value),
                (None, None),
            )
            contract = PrimitiveContract(
                grasp_success=True if not opening else None,
                release_success=True if opening else None,
                in_handover_zone=None if opening else spatial[0],
                attachments_consistent=None if opening else spatial[1],
                supported_after_release=True if opening else None,
                attachment_ref=(f"controlled:{node.action.object_id}:{node.action.arm.value}" if not opening else None),
                free_pose_ref=(f"controlled:released:{node.action.object_id}" if opening else None),
                gripper_trajectory_ref=trajectory.content_hash,
                gripper_final_position=0.044 if opening else 0.0,
            )
        elif node.action.kind is ActionKind.TRANSFER_AUTHORITY:
            spatial = spatial_contracts.get(
                (node.source.line, node.source.column, node.action.kind.value),
                (None, None),
            )
            contract = PrimitiveContract(
                in_handover_zone=spatial[0],
                synchronized=True,
                arms_static=True,
            )
        if contract != PrimitiveContract():
            located.append(LocatedPrimitiveContract(ActionLocator.from_node(node), contract))
    contracts = bind_primitive_contracts(program, located)
    zero = JointConfiguration.from_positions((0.0,) * 7)
    environment = SearchEnvironment(
        environment_hash=environment_hash,
        initial_left_q=zero,
        initial_right_q=zero,
        initial_left_gripper=GripperConfiguration.from_position(0.044),
        initial_right_gripper=GripperConfiguration.from_position(0.044),
        gripper_position_limits=(0.0, 0.044),
        initial_objects=tuple(ObjectState(item, free_pose_ref=f"controlled:initial:{item}") for item in ("payload_alpha", "payload_beta")),
        initial_resources=(("fixture_alpha", None), ("tool_beta", None)),
        trajectories=tuple((item.content_hash, item) for item in trajectories),
        gripper_trajectories=tuple(sorted(grippers.items())),
        primitive_contracts=contracts,
        required_released_resources=("fixture_alpha", "tool_beta"),
    )
    return program, environment


def _centers(world) -> Mapping[str, tuple[float, float, float]]:
    q = {"left": world.left_q.positions(), "right": world.right_q.positions()}
    return _centers_from_q(world, q)


def _centers_from_q(world, q) -> Mapping[str, tuple[float, float, float]]:
    values = {
        "left_tool_sphere": (-0.45 + 0.2 * q["left"][0], 0.30 + 0.2 * q["left"][1], 0.45 + 0.2 * q["left"][2]),
        "right_tool_sphere": (0.45 + 0.2 * q["right"][0], 0.30 + 0.2 * q["right"][1], 0.45 + 0.2 * q["right"][2]),
    }
    for item in world.objects:
        if item.authority is not None and item.authority in item.grasps:
            values[item.object_id] = values[f"{item.authority.value}_tool_sphere"]
        else:
            values[item.object_id] = INITIAL_OBJECT_CENTERS[item.object_id]
    return values


@lru_cache(maxsize=1)
def _cached_live_trajectories() -> Mapping[str, FrozenJointTrajectory]:
    return {
        trajectory.content_hash: trajectory
        for trajectory in _load_trajectories(repository_root())
    }


def _trajectory_positions_at(
    trajectory: FrozenJointTrajectory, local_ns: int
) -> tuple[float, ...]:
    if not 0 <= local_ns <= trajectory.duration_ns:
        raise ValueError("trajectory endpoint query lies outside the move")
    boundary = next(
        (point for point in trajectory.points if point.time_ns == local_ns), None
    )
    if boundary is not None:
        return boundary.positions
    segment = next(
        segment
        for segment in trajectory.segments()
        if segment.start_ns < local_ns < segment.end_ns
    )
    positions, _velocities, _accelerations = segment.evaluate(
        local_ns - segment.start_ns
    )
    return positions


def _distance(centers: Mapping[str, tuple[float, float, float]], pair: tuple[str, str]) -> float:
    return math.dist(centers[pair[0]], centers[pair[1]]) - RADII[pair[0]] - RADII[pair[1]]


def _allowed_attachment(world, pair: tuple[str, str]) -> bool:
    for item in world.objects:
        for arm in item.grasps:
            if set(pair) == {item.object_id, f"{arm.value}_tool_sphere"}:
                return True
    return False


def controlled_point_property(state) -> PointAssessment:
    centers = _centers(state.world)
    for pair in CHECKED_PAIRS:
        if _allowed_attachment(state.world, pair):
            continue
        if _distance(centers, pair) <= 0:
            return PointAssessment(IntervalStatus.COLLISION, "controlled-point-collision", state.time_ns, entity_pair=pair)
    return PointAssessment(IntervalStatus.SAFE, "controlled-point-separated", state.time_ns, certificate_ref="controlled-point-analytic-v1")


def controlled_interval_property(state, start_ns: int, end_ns: int, active) -> IntervalAssessment:
    centers = _centers(state.world)
    minimum = min(
        (_distance(centers, pair) for pair in CHECKED_PAIRS if not _allowed_attachment(state.world, pair)),
        default=float("inf"),
    )
    bounds = _cached_live_speed_bounds()
    speed_upper = 0.0
    end_q = {
        "left": state.world.left_q.positions(),
        "right": state.world.right_q.positions(),
    }
    trajectories = _cached_live_trajectories()
    for action in active:
        if action.kind is not ActionKind.MOVE:
            continue
        if action.trajectory_hash not in bounds:
            raise ValueError("active move lacks a frozen trajectory speed bound")
        # The sum is a conservative relative-speed bound for any checked pair:
        # a tool or its attached object inherits that arm's tool-center speed.
        speed_upper += bounds[action.trajectory_hash]
        trajectory = trajectories[action.trajectory_hash]
        end_q[action.arm.value] = _trajectory_positions_at(
            trajectory, end_ns - action.start_ns
        )
    end_centers = _centers_from_q(state.world, end_q)
    lower = minimum - speed_upper * ((end_ns - start_ns) * 1e-9)
    if minimum <= 0:
        pair = next(pair for pair in CHECKED_PAIRS if not _allowed_attachment(state.world, pair) and _distance(centers, pair) <= 0)
        return IntervalAssessment(IntervalStatus.COLLISION, "controlled-interval-start-collision", (start_ns, min(end_ns, start_ns + 1)), entity_pair=pair)
    end_collision = next(
        (
            pair
            for pair in CHECKED_PAIRS
            if not _allowed_attachment(state.world, pair)
            and _distance(end_centers, pair) <= 0
        ),
        None,
    )
    if end_collision is not None:
        return IntervalAssessment(
            IntervalStatus.COLLISION,
            "controlled-interval-endpoint-collision",
            (max(start_ns, end_ns - 1), end_ns),
            entity_pair=end_collision,
        )
    if lower <= 0:
        return IntervalAssessment(IntervalStatus.UNKNOWN, "controlled-interval-conservative-unresolved", (start_ns, end_ns))
    return IntervalAssessment(IntervalStatus.SAFE, "controlled-interval-separated", (start_ns, end_ns), certificate_ref="controlled-interval-analytic-v1")
