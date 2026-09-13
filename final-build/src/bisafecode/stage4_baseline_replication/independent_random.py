"""Independent random dynamic tester for the frozen controlled language.

This module deliberately uses only the Python standard library. It does not
import BiSafeCode's restricted parser, Timed IR, explicit-state engine,
property adapters, logical oracle, or geometry oracle. The public source
language and frozen JSON scene/model/trajectory assets are its only inputs.
"""

from __future__ import annotations

import ast
import hashlib
import json
import math
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Mapping, Sequence, Union


ROLLOUTS = 100
MAX_EVENTS_PER_ROLLOUT = 10_000
WALL_TIMEOUT_SECONDS = 120.0
GEOMETRY_SAMPLE_NS = 1_000_000
SEED_DOMAIN = "EXP-S4-007:independent-random-dynamic:v1"
ARMS = ("left", "right")
FINITE_VALUATIONS = (
    {"mode": "fast", "ready": False},
    {"mode": "fast", "ready": True},
    {"mode": "safe", "ready": False},
    {"mode": "safe", "ready": True},
)


class IndependentRuntimeError(ValueError):
    """The source or public runtime asset is invalid for this baseline."""


@dataclass(frozen=True)
class Operation:
    kind: str
    args: tuple[Any, ...]
    line: int
    duration_ns: int = 0


@dataclass(frozen=True)
class ParallelOperation:
    left: tuple[Operation, ...]
    right: tuple[Operation, ...]
    line: int


Executable = Union[Operation, ParallelOperation]


@dataclass
class ObjectRuntime:
    authority: str | None = None
    grasps: set[str] = field(default_factory=set)


@dataclass
class ActiveOperation:
    operation: Operation
    start_ns: int
    end_ns: int


@dataclass
class RuntimeWorld:
    time_ns: int = 0
    q: dict[str, tuple[float, ...]] = field(
        default_factory=lambda: {arm: (0.0,) * 7 for arm in ARMS}
    )
    resources: dict[str, str | None] = field(
        default_factory=lambda: {"fixture_alpha": None, "tool_beta": None}
    )
    objects: dict[str, ObjectRuntime] = field(
        default_factory=lambda: {
            "payload_alpha": ObjectRuntime(),
            "payload_beta": ObjectRuntime(),
        }
    )
    active: dict[str, ActiveOperation] = field(default_factory=dict)


@dataclass(frozen=True)
class PublicAssets:
    trajectories: Mapping[str, Mapping[str, Any]]
    radii: Mapping[str, float]
    initial_object_centers: Mapping[str, tuple[float, float, float]]
    checked_pairs: tuple[tuple[str, str], ...]
    handover_min: tuple[float, float, float]
    handover_max: tuple[float, float, float]


def _load_json(path: Path) -> Any:
    return json.loads(path.read_text(encoding="utf-8"))


def load_public_assets(asset_root: Path) -> PublicAssets:
    """Load only frozen public model/scene/trajectory documents."""

    asset_root = asset_root.resolve()
    model = _load_json(asset_root / "model.json")
    scene = _load_json(asset_root / "scene.json")
    radii = {
        str(item["entity_id"]): float(item["radius_m"])
        for item in model["semantics"]["entities"]
    }
    trajectories: dict[str, Mapping[str, Any]] = {}
    for path in sorted((asset_root / "trajectories").glob("*.json")):
        if path.name.endswith(".sidecar.json"):
            continue
        document = _load_json(path)
        sidecar = _load_json(path.with_name(path.stem + ".sidecar.json"))
        file_sha = hashlib.sha256(path.read_bytes()).hexdigest()
        if file_sha != sidecar.get("trajectory_artifact_sha256"):
            raise IndependentRuntimeError("trajectory artifact/sidecar mismatch")
        content_sha = str(sidecar["trajectory_content_sha256"])
        if content_sha in trajectories:
            raise IndependentRuntimeError("duplicate public trajectory content identity")
        trajectories[content_sha] = document
    if len(trajectories) != 4:
        raise IndependentRuntimeError("exactly four public trajectories are required")
    return PublicAssets(
        trajectories=trajectories,
        radii=radii,
        initial_object_centers={
            key: tuple(float(value) for value in values)
            for key, values in scene["initial_object_centers_m"].items()
        },
        checked_pairs=tuple(tuple(pair) for pair in scene["pair_policy"]["checked_pairs"]),
        handover_min=tuple(float(value) for value in scene["handover_zone"]["min_xyz_m"]),
        handover_max=tuple(float(value) for value in scene["handover_zone"]["max_xyz_m"]),
    )


def _literal(node: ast.AST) -> Any:
    if not isinstance(node, ast.Constant) or type(node.value) not in {str, int, bool}:
        raise IndependentRuntimeError("only string/integer/boolean literals are supported")
    return node.value


def _call(statement: ast.stmt) -> ast.Call:
    if not isinstance(statement, ast.Expr) or not isinstance(statement.value, ast.Call):
        raise IndependentRuntimeError("unsupported statement in controlled source")
    if not isinstance(statement.value.func, ast.Name):
        raise IndependentRuntimeError("controlled calls must use direct names")
    if statement.value.keywords:
        raise IndependentRuntimeError("keyword arguments are outside the frozen language")
    return statement.value


def _predicate(node: ast.AST, valuation: Mapping[str, Any]) -> bool:
    if isinstance(node, ast.Name) and node.id in valuation:
        return bool(valuation[node.id])
    if (
        isinstance(node, ast.Compare)
        and len(node.ops) == 1
        and len(node.comparators) == 1
        and isinstance(node.left, ast.Name)
        and node.left.id in valuation
    ):
        right = _literal(node.comparators[0])
        if isinstance(node.ops[0], ast.Eq):
            return valuation[node.left.id] == right
        if isinstance(node.ops[0], ast.NotEq):
            return valuation[node.left.id] != right
    raise IndependentRuntimeError("unsupported frozen predicate")


def _compile_block(
    statements: Sequence[ast.stmt],
    *,
    functions: Mapping[str, ast.FunctionDef],
    valuation: Mapping[str, Any],
    branch_name: str | None = None,
) -> tuple[Executable, ...]:
    executable: list[Executable] = []
    for statement in statements:
        if isinstance(statement, ast.If):
            selected = statement.body if _predicate(statement.test, valuation) else statement.orelse
            if not statement.orelse:
                raise IndependentRuntimeError("if requires an else branch")
            executable.extend(
                _compile_block(selected, functions=functions, valuation=valuation, branch_name=branch_name)
            )
            continue
        if isinstance(statement, ast.For):
            if (
                not isinstance(statement.target, ast.Name)
                or not isinstance(statement.iter, ast.Call)
                or not isinstance(statement.iter.func, ast.Name)
                or statement.iter.func.id != "range"
                or len(statement.iter.args) != 1
            ):
                raise IndependentRuntimeError("only for name in range(literal) is supported")
            bound = _literal(statement.iter.args[0])
            if type(bound) is not int or bound not in {2, 3, 4}:
                raise IndependentRuntimeError("loop bound is outside the frozen domain")
            body = _compile_block(
                statement.body, functions=functions, valuation=valuation, branch_name=branch_name
            )
            executable.extend(body * bound)
            continue
        call = _call(statement)
        name = call.func.id
        if name == "parallel":
            if branch_name is not None or len(call.args) != 2:
                raise IndependentRuntimeError("parallel shape is outside the frozen language")
            names = []
            for argument in call.args:
                if not isinstance(argument, ast.Name) or argument.id not in functions:
                    raise IndependentRuntimeError("parallel branches must be named functions")
                names.append(argument.id)
            left = _compile_block(
                functions[names[0]].body,
                functions=functions,
                valuation=valuation,
                branch_name="left",
            )
            right = _compile_block(
                functions[names[1]].body,
                functions=functions,
                valuation=valuation,
                branch_name="right",
            )
            if any(not isinstance(item, Operation) for item in (*left, *right)):
                raise IndependentRuntimeError("nested parallel is forbidden")
            executable.append(ParallelOperation(tuple(left), tuple(right), statement.lineno))
            continue
        args = tuple(_literal(argument) for argument in call.args)
        signatures = {
            "move": 2,
            "wait": 2,
            "close": 3,
            "open": 3,
            "acquire": 2,
            "release": 2,
            "transfer_authority": 3,
            "barrier": 1,
        }
        if name not in signatures or len(args) != signatures[name]:
            raise IndependentRuntimeError(f"unsupported API call: {name}")
        if branch_name is not None and name not in {"barrier", "transfer_authority"}:
            if args[0] != branch_name:
                raise IndependentRuntimeError("parallel branch violates arm affinity")
        if branch_name is not None and name == "transfer_authority":
            raise IndependentRuntimeError("transfer_authority is forbidden inside parallel")
        duration = int(args[-1]) if name in {"wait", "close", "open"} else 0
        executable.append(Operation(name, args, statement.lineno, duration))
    return tuple(executable)


def compile_source(source: str, valuation: Mapping[str, Any]) -> tuple[Executable, ...]:
    """Parse the public surface language independently using stdlib AST."""

    module = ast.parse(source, filename="opaque_controlled_case.py", mode="exec")
    functions: dict[str, ast.FunctionDef] = {}
    for statement in module.body:
        if not isinstance(statement, ast.FunctionDef) or statement.name in functions:
            raise IndependentRuntimeError("top level must contain unique functions only")
        if statement.decorator_list or statement.args.args or statement.args.posonlyargs:
            raise IndependentRuntimeError("controlled functions must be parameterless")
        functions[statement.name] = statement
    if "task" not in functions:
        raise IndependentRuntimeError("task entry function is missing")
    return _compile_block(functions["task"].body, functions=functions, valuation=valuation)


def _choice(source_sha256: str, rollout: int, decision: int, population: int) -> int:
    if population < 1:
        raise IndependentRuntimeError("random choice requires nonempty population")
    payload = f"{SEED_DOMAIN}:{source_sha256}:{rollout}:{decision}".encode("utf-8")
    return int.from_bytes(hashlib.sha256(payload).digest(), "big") % population


def _trajectory_position(document: Mapping[str, Any], local_ns: int) -> tuple[float, ...]:
    points = document["points"]
    duration = int(points[-1]["time_ns"])
    local_ns = min(max(local_ns, 0), duration)
    for point in points:
        if int(point["time_ns"]) == local_ns:
            return tuple(float(value) for value in point["positions"])
    for first, second in zip(points, points[1:]):
        start, end = int(first["time_ns"]), int(second["time_ns"])
        if start < local_ns < end:
            span = end - start
            s = (local_ns - start) / span
            h00 = 2 * s**3 - 3 * s**2 + 1
            h10 = s**3 - 2 * s**2 + s
            h01 = -2 * s**3 + 3 * s**2
            h11 = s**3 - s**2
            seconds = span * 1e-9
            return tuple(
                h00 * float(p0)
                + h10 * seconds * float(v0)
                + h01 * float(p1)
                + h11 * seconds * float(v1)
                for p0, v0, p1, v1 in zip(
                    first["positions"], first["velocities"],
                    second["positions"], second["velocities"],
                )
            )
    raise IndependentRuntimeError("trajectory interpolation interval missing")


def _q_at(world: RuntimeWorld, arm: str, at_ns: int, assets: PublicAssets) -> tuple[float, ...]:
    active = world.active.get(arm)
    if active is None or active.operation.kind != "move":
        return world.q[arm]
    document = assets.trajectories[str(active.operation.args[1])]
    return _trajectory_position(document, at_ns - active.start_ns)


def _centers(world: RuntimeWorld, at_ns: int, assets: PublicAssets) -> dict[str, tuple[float, float, float]]:
    q = {arm: _q_at(world, arm, at_ns, assets) for arm in ARMS}
    centers = {
        "left_tool_sphere": (-0.45 + 0.20 * q["left"][0], 0.30 + 0.20 * q["left"][1], 0.45 + 0.20 * q["left"][2]),
        "right_tool_sphere": (0.45 + 0.20 * q["right"][0], 0.30 + 0.20 * q["right"][1], 0.45 + 0.20 * q["right"][2]),
    }
    for object_id, state in world.objects.items():
        centers[object_id] = (
            centers[f"{state.authority}_tool_sphere"]
            if state.authority is not None and state.authority in state.grasps
            else assets.initial_object_centers[object_id]
        )
    return centers


def _collision_at(world: RuntimeWorld, at_ns: int, assets: PublicAssets) -> tuple[str, str] | None:
    centers = _centers(world, at_ns, assets)
    for first, second in assets.checked_pairs:
        exempt = False
        for object_id, state in world.objects.items():
            for arm in state.grasps:
                if {first, second} == {object_id, f"{arm}_tool_sphere"}:
                    exempt = True
                    break
            if exempt:
                break
        if exempt:
            continue
        signed = math.dist(centers[first], centers[second]) - assets.radii[first] - assets.radii[second]
        if signed <= 0.0:
            return (first, second)
    return None


def _monitor_interval(
    world: RuntimeWorld, start_ns: int, end_ns: int, assets: PublicAssets
) -> Mapping[str, Any] | None:
    probes = list(range(start_ns, end_ns, GEOMETRY_SAMPLE_NS))
    if not probes or probes[-1] != end_ns:
        probes.append(end_ns)
    for at_ns in probes:
        pair = _collision_at(world, at_ns, assets)
        if pair is not None:
            return {
                "outcome": "violation",
                "reason": "RANDOM_RUNTIME_COLLISION_SAMPLE",
                "time_ns": at_ns,
                "entity_pair": list(pair),
            }
    return None


def _effect(
    world: RuntimeWorld, operation: Operation, assets: PublicAssets
) -> Mapping[str, Any] | None:
    kind, args = operation.kind, operation.args
    if kind == "move":
        trajectory = assets.trajectories.get(str(args[1]))
        if trajectory is None or trajectory.get("arm") != args[0]:
            return {"outcome": "invalid", "reason": "RANDOM_UNKNOWN_TRAJECTORY"}
        world.q[str(args[0])] = tuple(float(value) for value in trajectory["points"][-1]["positions"])
    elif kind == "acquire":
        arm, resource = str(args[0]), str(args[1])
        if world.resources.get(resource) is not None:
            return {"outcome": "violation", "reason": "RANDOM_RESOURCE_DOUBLE_ACQUIRE"}
        world.resources[resource] = arm
    elif kind == "release":
        arm, resource = str(args[0]), str(args[1])
        if world.resources.get(resource) != arm:
            return {"outcome": "violation", "reason": "RANDOM_RESOURCE_RELEASE_BY_NONOWNER"}
        world.resources[resource] = None
    elif kind == "close":
        arm, object_id = str(args[0]), str(args[1])
        state = world.objects[object_id]
        if arm in state.grasps:
            return {"outcome": "violation", "reason": "RANDOM_OBJECT_DOUBLE_CLOSE"}
        if state.authority is None:
            state.authority = arm
        state.grasps.add(arm)
    elif kind == "open":
        arm, object_id = str(args[0]), str(args[1])
        state = world.objects[object_id]
        if arm not in state.grasps:
            return {"outcome": "violation", "reason": "RANDOM_OBJECT_OPEN_BY_NONGRASPING_ARM"}
        if state.authority == arm and len(state.grasps) > 1:
            return {"outcome": "violation", "reason": "RANDOM_SENDER_RELEASE_BEFORE_TRANSFER"}
        state.grasps.remove(arm)
        if state.authority == arm:
            state.authority = None
    elif kind == "transfer_authority":
        object_id, sender, receiver = (str(value) for value in args)
        state = world.objects[object_id]
        if state.authority != sender or state.grasps != {sender, receiver}:
            return {"outcome": "violation", "reason": "RANDOM_TRANSFER_WITHOUT_DUAL_GRASP"}
        if world.active:
            return {"outcome": "violation", "reason": "RANDOM_TRANSFER_WHILE_MOVING"}
        centers = _centers(world, world.time_ns, assets)
        for arm in (sender, receiver):
            center = centers[f"{arm}_tool_sphere"]
            if not all(low <= value <= high for value, low, high in zip(center, assets.handover_min, assets.handover_max)):
                return {"outcome": "violation", "reason": "RANDOM_TRANSFER_OUTSIDE_HANDOVER_ZONE"}
        state.authority = receiver
    pair = _collision_at(world, world.time_ns, assets)
    if pair is not None:
        return {
            "outcome": "violation",
            "reason": "RANDOM_RUNTIME_COLLISION_AT_EVENT",
            "time_ns": world.time_ns,
            "entity_pair": list(pair),
        }
    return None


def _duration(operation: Operation, assets: PublicAssets) -> int:
    if operation.kind == "move":
        trajectory = assets.trajectories.get(str(operation.args[1]))
        if trajectory is None:
            raise IndependentRuntimeError("move references an unknown public trajectory")
        return int(trajectory["points"][-1]["time_ns"])
    return operation.duration_ns


def _record(trace: list[Mapping[str, Any]], event: str, operation: Operation, at_ns: int) -> None:
    trace.append({"event": event, "kind": operation.kind, "line": operation.line, "time_ns": at_ns})


def _run_sequential(
    world: RuntimeWorld,
    operation: Operation,
    assets: PublicAssets,
    trace: list[Mapping[str, Any]],
) -> Mapping[str, Any] | None:
    if operation.kind == "barrier":
        return {"outcome": "invalid", "reason": "RANDOM_ORPHAN_BARRIER"}
    duration = _duration(operation, assets)
    arm = str(operation.args[0]) if operation.kind not in {"transfer_authority"} else None
    _record(trace, "start", operation, world.time_ns)
    if duration:
        if arm in world.active:
            return {"outcome": "invalid", "reason": "RANDOM_ARM_ALREADY_ACTIVE"}
        world.active[arm] = ActiveOperation(operation, world.time_ns, world.time_ns + duration)
        violation = _monitor_interval(world, world.time_ns, world.time_ns + duration, assets)
        world.time_ns += duration
        active = world.active.pop(arm)
        if violation is not None:
            return violation
        _record(trace, "complete", operation, world.time_ns)
        return _effect(world, active.operation, assets)
    _record(trace, "complete", operation, world.time_ns)
    return _effect(world, operation, assets)


def _run_parallel(
    world: RuntimeWorld,
    operation: ParallelOperation,
    assets: PublicAssets,
    trace: list[Mapping[str, Any]],
    *,
    source_sha256: str,
    rollout: int,
    decision_start: int,
) -> tuple[Mapping[str, Any] | None, int]:
    lanes = {"left": operation.left, "right": operation.right}
    cursors = {"left": 0, "right": 0}
    active_by_lane: dict[str, ActiveOperation] = {}
    decision = decision_start
    events = 0
    while True:
        events += 1
        if events > MAX_EVENTS_PER_ROLLOUT:
            return ({"outcome": "inconclusive", "reason": "RANDOM_EVENT_BOUND_EXHAUSTED"}, decision)
        if all(cursors[arm] >= len(lanes[arm]) for arm in ARMS) and not active_by_lane:
            return (None, decision)

        barriers = {
            arm: lanes[arm][cursors[arm]]
            for arm in ARMS
            if cursors[arm] < len(lanes[arm])
            and lanes[arm][cursors[arm]].kind == "barrier"
            and arm not in active_by_lane
        }
        if len(barriers) == 2:
            if barriers["left"].args != barriers["right"].args:
                return ({"outcome": "invalid", "reason": "RANDOM_BARRIER_ID_MISMATCH"}, decision)
            for arm in ARMS:
                _record(trace, "barrier-release", barriers[arm], world.time_ns)
                cursors[arm] += 1
            continue

        startable = [
            arm for arm in ARMS
            if arm not in active_by_lane
            and cursors[arm] < len(lanes[arm])
            and lanes[arm][cursors[arm]].kind != "barrier"
        ]
        if startable:
            selected = startable[_choice(source_sha256, rollout, decision, len(startable))]
            decision += 1
            current = lanes[selected][cursors[selected]]
            duration = _duration(current, assets)
            _record(trace, "start", current, world.time_ns)
            if duration:
                active = ActiveOperation(current, world.time_ns, world.time_ns + duration)
                active_by_lane[selected] = active
                world.active[selected] = active
            else:
                _record(trace, "complete", current, world.time_ns)
                outcome = _effect(world, current, assets)
                cursors[selected] += 1
                if outcome is not None:
                    return (outcome, decision)
            continue

        if not active_by_lane:
            return ({"outcome": "invalid", "reason": "RANDOM_PARALLEL_DEADLOCK"}, decision)
        next_ns = min(active.end_ns for active in active_by_lane.values())
        outcome = _monitor_interval(world, world.time_ns, next_ns, assets)
        world.time_ns = next_ns
        if outcome is not None:
            return (outcome, decision)
        completing = [arm for arm, active in active_by_lane.items() if active.end_ns == next_ns]
        while completing:
            index = _choice(source_sha256, rollout, decision, len(completing))
            decision += 1
            arm = completing.pop(index)
            active = active_by_lane.pop(arm)
            world.active.pop(arm, None)
            _record(trace, "complete", active.operation, world.time_ns)
            outcome = _effect(world, active.operation, assets)
            cursors[arm] += 1
            if outcome is not None:
                return (outcome, decision)


def _single_rollout(
    source: str,
    *,
    source_sha256: str,
    rollout: int,
    assets: PublicAssets,
) -> Mapping[str, Any]:
    decision = 0
    valuation_index = _choice(source_sha256, rollout, decision, len(FINITE_VALUATIONS))
    decision += 1
    valuation = FINITE_VALUATIONS[valuation_index]
    executable = compile_source(source, valuation)
    world = RuntimeWorld()
    trace: list[Mapping[str, Any]] = []
    initial = _collision_at(world, 0, assets)
    if initial is not None:
        return {"rollout_index": rollout, "valuation": valuation, "outcome": "violation", "reason": "RANDOM_INITIAL_COLLISION", "trace": trace}
    for operation in executable:
        if isinstance(operation, ParallelOperation):
            outcome, decision = _run_parallel(
                world, operation, assets, trace,
                source_sha256=source_sha256, rollout=rollout, decision_start=decision,
            )
        else:
            outcome = _run_sequential(world, operation, assets, trace)
        if outcome is not None:
            return {"rollout_index": rollout, "valuation": valuation, **outcome, "trace": trace}
    unreleased = sorted(key for key, owner in world.resources.items() if owner is not None)
    if unreleased:
        return {
            "rollout_index": rollout,
            "valuation": valuation,
            "outcome": "violation",
            "reason": "RANDOM_RESOURCE_NOT_RELEASED_AT_TERMINATION",
            "resources": unreleased,
            "trace": trace,
        }
    return {
        "rollout_index": rollout,
        "valuation": valuation,
        "outcome": "safe-completion",
        "reason": "NO_VIOLATION_OBSERVED_ON_INDEPENDENT_SAMPLED_EXECUTION",
        "trace": trace,
    }


def run_independent_random_dynamic(
    source: str,
    *,
    source_sha256: str,
    asset_root: Path,
    rollouts: int = ROLLOUTS,
    wall_timeout_seconds: float = WALL_TIMEOUT_SECONDS,
) -> Mapping[str, Any]:
    """Run sampled executions without invoking any BiSafeCode method component."""

    actual_sha = hashlib.sha256(source.encode("utf-8")).hexdigest()
    if actual_sha != source_sha256:
        raise IndependentRuntimeError("source identity mismatch")
    if rollouts < 1 or wall_timeout_seconds <= 0:
        raise IndependentRuntimeError("rollout and wall budgets must be positive")
    assets = load_public_assets(asset_root)
    started = time.monotonic()
    records: list[Mapping[str, Any]] = []
    truncated = False
    invalid_error: str | None = None
    for rollout in range(rollouts):
        if time.monotonic() - started >= wall_timeout_seconds:
            truncated = True
            break
        try:
            records.append(
                _single_rollout(
                    source, source_sha256=source_sha256, rollout=rollout, assets=assets
                )
            )
        except (SyntaxError, IndependentRuntimeError, KeyError, TypeError, ValueError) as error:
            invalid_error = f"{type(error).__name__}:{error}"
            break
    outcomes = [str(record["outcome"]) for record in records]
    if invalid_error is not None:
        status, prediction, reasons = "invalid", "invalid", ["INDEPENDENT_RUNTIME_INVALID_INPUT"]
    elif any(value == "violation" for value in outcomes):
        status, prediction, reasons = "ok", "unsafe", ["INDEPENDENT_RANDOM_VIOLATION_OBSERVED"]
    elif len(records) == rollouts and all(value == "safe-completion" for value in outcomes):
        status, prediction, reasons = "ok", "safe", ["NO_VIOLATION_OBSERVED_IN_100_INDEPENDENT_ROLLOUTS"]
    else:
        status, prediction, reasons = "resource_truncated", "unknown", ["INDEPENDENT_RANDOM_BUDGET_EXHAUSTED"]
    valuation_counts = {
        f"mode={mode},ready={str(ready).lower()}": sum(
            1 for record in records
            if record.get("valuation") == {"mode": mode, "ready": ready}
        )
        for mode in ("fast", "safe") for ready in (False, True)
    }
    return {
        "status": status,
        "predicted_class": prediction,
        "reason_codes": reasons,
        "scheduled_rollouts": rollouts,
        "completed_rollouts": len(records),
        "violation_rollouts": outcomes.count("violation"),
        "safe_completion_rollouts": outcomes.count("safe-completion"),
        "inconclusive_rollouts": outcomes.count("inconclusive"),
        "invalid_error": invalid_error,
        "valuation_counts": valuation_counts,
        "elapsed_ms": (time.monotonic() - started) * 1000.0,
        "seed_input": "source_sha256_only; no program/family/oracle identifier",
        "implementation_independence": {
            "stdlib_ast_interpreter": True,
            "own_event_scheduler": True,
            "own_runtime_monitors": True,
            "bisafecode_explicit_state_imported": False,
            "bisafecode_property_adapter_imported": False,
            "bisafecode_oracle_imported": False,
            "exhaustive_claim": False,
        },
        "rollouts": records,
    }
