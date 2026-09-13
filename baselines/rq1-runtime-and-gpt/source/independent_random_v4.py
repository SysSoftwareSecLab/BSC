"""Corrected independent random-schedule dynamic monitor (v4).

This keeps the v3 baseline's independent stdlib-AST interpreter, public-only
assets, 100 sampled executions, and runtime monitors.  It changes one
correctness detail: operations completing at the same model time take effect
as one atomic completion batch before the post-event collision check.  Thus a
temporary state created only by Python iteration order is never observed.

The result ``safe`` means only that no violation was observed in the sampled
executions.  It is not a bounded-verification claim.
"""

from __future__ import annotations

import hashlib
import math
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Mapping, Sequence

from . import independent_random as v3


ROLLOUTS = 100
MAX_EVENTS_PER_ROLLOUT = v3.MAX_EVENTS_PER_ROLLOUT
WALL_TIMEOUT_SECONDS = v3.WALL_TIMEOUT_SECONDS
GEOMETRY_SAMPLE_NS = v3.GEOMETRY_SAMPLE_NS
SEED_DOMAIN = "EXP-S4-008:independent-random-schedule-dynamic-monitor:v4"
ARMS = v3.ARMS
FINITE_VALUATIONS = v3.FINITE_VALUATIONS


@dataclass
class ProtocolObjectRuntime:
    authority: str | None = None
    grasps: set[str] = field(default_factory=set)
    phase: str = "free"
    sender: str | None = None
    receiver: str | None = None


def _new_world() -> v3.RuntimeWorld:
    return v3.RuntimeWorld(
        objects={
            "payload_alpha": ProtocolObjectRuntime(),
            "payload_beta": ProtocolObjectRuntime(),
        }
    )


def _choice(source_sha256: str, rollout: int, decision: int, population: int) -> int:
    if population < 1:
        raise v3.IndependentRuntimeError("random choice requires nonempty population")
    payload = f"{SEED_DOMAIN}:{source_sha256}:{rollout}:{decision}".encode("utf-8")
    return int.from_bytes(hashlib.sha256(payload).digest(), "big") % population


def _monitor_open_interval(
    world: v3.RuntimeWorld,
    start_ns: int,
    end_ns: int,
    assets: v3.PublicAssets,
) -> Mapping[str, Any] | None:
    """Monitor [start,end), leaving the event boundary to the atomic batch."""

    for at_ns in range(start_ns, end_ns, GEOMETRY_SAMPLE_NS):
        pair = v3._collision_at(world, at_ns, assets)
        if pair is not None:
            return {
                "outcome": "violation",
                "reason": "RANDOM_RUNTIME_COLLISION_SAMPLE",
                "time_ns": at_ns,
                "entity_pair": list(pair),
            }
    return None


def _post_batch_collision(
    world: v3.RuntimeWorld, assets: v3.PublicAssets
) -> Mapping[str, Any] | None:
    pair = v3._collision_at(world, world.time_ns, assets)
    if pair is None:
        return None
    return {
        "outcome": "violation",
        "reason": "RANDOM_RUNTIME_COLLISION_AT_ATOMIC_EVENT_BATCH",
        "time_ns": world.time_ns,
        "entity_pair": list(pair),
    }


def _inside_handover_zone(
    center: Sequence[float], assets: v3.PublicAssets
) -> bool:
    return all(
        low <= value <= high
        for value, low, high in zip(
            center, assets.handover_min, assets.handover_max
        )
    )


def _semantic_effect(
    world: v3.RuntimeWorld,
    operation: v3.Operation,
    assets: v3.PublicAssets,
) -> Mapping[str, Any] | None:
    """Apply one completion without inspecting an intermediate collision state."""

    kind, args = operation.kind, operation.args
    if kind == "move":
        trajectory = assets.trajectories.get(str(args[1]))
        if trajectory is None or trajectory.get("arm") != args[0]:
            return {"outcome": "invalid", "reason": "RANDOM_UNKNOWN_TRAJECTORY"}
        world.q[str(args[0])] = tuple(
            float(value) for value in trajectory["points"][-1]["positions"]
        )
    elif kind == "acquire":
        arm, resource = str(args[0]), str(args[1])
        if world.resources.get(resource) is not None:
            return {
                "outcome": "violation",
                "reason": "RANDOM_RESOURCE_DOUBLE_ACQUIRE",
            }
        world.resources[resource] = arm
    elif kind == "release":
        arm, resource = str(args[0]), str(args[1])
        if world.resources.get(resource) != arm:
            return {
                "outcome": "violation",
                "reason": "RANDOM_RESOURCE_RELEASE_BY_NONOWNER",
            }
        world.resources[resource] = None
    elif kind == "close":
        arm, object_id = str(args[0]), str(args[1])
        state = world.objects.get(object_id)
        if state is None:
            return {"outcome": "invalid", "reason": "RANDOM_UNKNOWN_OBJECT"}
        if arm in state.grasps:
            return {
                "outcome": "violation",
                "reason": "RANDOM_OBJECT_DOUBLE_CLOSE",
            }
        if state.phase == "free":
            state.authority = arm
            state.grasps = {arm}
            state.phase = "sender_only"
            state.sender = arm
            state.receiver = None
        elif state.phase == "sender_only" and len(state.grasps) == 1:
            centers = v3._centers(world, world.time_ns, assets)
            if world.active:
                return {
                    "outcome": "violation",
                    "reason": "RANDOM_RECEIVER_CLOSE_WHILE_MOVING",
                }
            if not all(
                _inside_handover_zone(centers[f"{candidate}_tool_sphere"], assets)
                for candidate in ARMS
            ):
                return {
                    "outcome": "violation",
                    "reason": "RANDOM_RECEIVER_CLOSE_OUTSIDE_HANDOVER_ZONE",
                }
            if math.dist(
                centers["left_tool_sphere"], centers["right_tool_sphere"]
            ) > 0.18:
                return {
                    "outcome": "violation",
                    "reason": "RANDOM_INCONSISTENT_DUAL_ATTACHMENT",
                }
            state.grasps.add(arm)
            state.receiver = arm
            state.phase = "dual_pre_transfer"
        else:
            return {
                "outcome": "violation",
                "reason": "RANDOM_OBJECT_CLOSE_IN_ILLEGAL_PHASE",
            }
    elif kind == "open":
        arm, object_id = str(args[0]), str(args[1])
        state = world.objects.get(object_id)
        if state is None:
            return {"outcome": "invalid", "reason": "RANDOM_UNKNOWN_OBJECT"}
        if arm not in state.grasps:
            return {
                "outcome": "violation",
                "reason": "RANDOM_OBJECT_OPEN_BY_NONGRASPING_ARM",
            }
        if state.phase == "dual_pre_transfer":
            reason = (
                "RANDOM_SENDER_RELEASE_BEFORE_TRANSFER"
                if arm == state.sender
                else "RANDOM_RECEIVER_RELEASE_DURING_HANDOVER"
            )
            return {"outcome": "violation", "reason": reason}
        if state.phase == "dual_post_transfer":
            if arm != state.sender:
                return {
                    "outcome": "violation",
                    "reason": "RANDOM_RECEIVER_RELEASE_DURING_HANDOVER",
                }
            state.grasps.remove(arm)
            state.sender = None
            state.phase = "receiver_only"
        elif state.phase in {"sender_only", "receiver_only"}:
            state.authority = None
            state.grasps.clear()
            state.phase = "free"
            state.sender = None
            state.receiver = None
        else:
            return {
                "outcome": "invalid",
                "reason": "RANDOM_OBJECT_OPEN_IN_INVALID_PHASE",
            }
    elif kind == "transfer_authority":
        object_id, sender, receiver = (str(value) for value in args)
        state = world.objects.get(object_id)
        if state is None:
            return {"outcome": "invalid", "reason": "RANDOM_UNKNOWN_OBJECT"}
        if (
            state.phase != "dual_pre_transfer"
            or state.sender != sender
            or state.receiver != receiver
            or state.authority != sender
            or state.grasps != {sender, receiver}
        ):
            return {
                "outcome": "violation",
                "reason": "RANDOM_TRANSFER_WITHOUT_MATCHED_DUAL_GRASP",
            }
        if world.active:
            return {
                "outcome": "violation",
                "reason": "RANDOM_TRANSFER_WHILE_MOVING",
            }
        centers = v3._centers(world, world.time_ns, assets)
        if not all(
            _inside_handover_zone(centers[f"{arm}_tool_sphere"], assets)
            for arm in (sender, receiver)
        ):
            return {
                "outcome": "violation",
                "reason": "RANDOM_TRANSFER_OUTSIDE_HANDOVER_ZONE",
            }
        state.authority = receiver
        state.phase = "dual_post_transfer"
    return None


def _run_sequential(
    world: v3.RuntimeWorld,
    operation: v3.Operation,
    assets: v3.PublicAssets,
    trace: list[Mapping[str, Any]],
) -> Mapping[str, Any] | None:
    if operation.kind == "barrier":
        return {"outcome": "invalid", "reason": "RANDOM_ORPHAN_BARRIER"}
    duration = v3._duration(operation, assets)
    arm = (
        str(operation.args[0])
        if operation.kind != "transfer_authority"
        else None
    )
    v3._record(trace, "start", operation, world.time_ns)
    if duration:
        if arm in world.active:
            return {"outcome": "invalid", "reason": "RANDOM_ARM_ALREADY_ACTIVE"}
        world.active[arm] = v3.ActiveOperation(
            operation, world.time_ns, world.time_ns + duration
        )
        violation = _monitor_open_interval(
            world, world.time_ns, world.time_ns + duration, assets
        )
        world.time_ns += duration
        active = world.active.pop(arm)
        if violation is not None:
            return violation
        v3._record(trace, "complete", operation, world.time_ns)
        outcome = _semantic_effect(world, active.operation, assets)
    else:
        v3._record(trace, "complete", operation, world.time_ns)
        outcome = _semantic_effect(world, operation, assets)
    return outcome if outcome is not None else _post_batch_collision(world, assets)


def _run_parallel(
    world: v3.RuntimeWorld,
    operation: v3.ParallelOperation,
    assets: v3.PublicAssets,
    trace: list[Mapping[str, Any]],
    *,
    source_sha256: str,
    rollout: int,
    decision_start: int,
) -> tuple[Mapping[str, Any] | None, int]:
    lanes = {"left": operation.left, "right": operation.right}
    cursors = {"left": 0, "right": 0}
    active_by_lane: dict[str, v3.ActiveOperation] = {}
    decision = decision_start
    events = 0
    while True:
        events += 1
        if events > MAX_EVENTS_PER_ROLLOUT:
            return (
                {"outcome": "inconclusive", "reason": "RANDOM_EVENT_BOUND_EXHAUSTED"},
                decision,
            )
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
                return (
                    {"outcome": "invalid", "reason": "RANDOM_BARRIER_ID_MISMATCH"},
                    decision,
                )
            for arm in ARMS:
                v3._record(trace, "barrier-release", barriers[arm], world.time_ns)
                cursors[arm] += 1
            continue

        startable = [
            arm
            for arm in ARMS
            if arm not in active_by_lane
            and cursors[arm] < len(lanes[arm])
            and lanes[arm][cursors[arm]].kind != "barrier"
        ]
        if startable:
            selected = startable[
                _choice(source_sha256, rollout, decision, len(startable))
            ]
            decision += 1
            current = lanes[selected][cursors[selected]]
            duration = v3._duration(current, assets)
            v3._record(trace, "start", current, world.time_ns)
            if duration:
                active = v3.ActiveOperation(
                    current, world.time_ns, world.time_ns + duration
                )
                active_by_lane[selected] = active
                world.active[selected] = active
            else:
                v3._record(trace, "complete", current, world.time_ns)
                outcome = _semantic_effect(world, current, assets)
                cursors[selected] += 1
                if outcome is None:
                    outcome = _post_batch_collision(world, assets)
                if outcome is not None:
                    return (outcome, decision)
            continue

        if not active_by_lane:
            return (
                {"outcome": "invalid", "reason": "RANDOM_PARALLEL_DEADLOCK"},
                decision,
            )
        next_ns = min(active.end_ns for active in active_by_lane.values())
        outcome = _monitor_open_interval(world, world.time_ns, next_ns, assets)
        world.time_ns = next_ns
        if outcome is not None:
            return (outcome, decision)

        completing = [
            arm
            for arm, active in active_by_lane.items()
            if active.end_ns == next_ns
        ]
        ordered: list[str] = []
        while completing:
            index = _choice(source_sha256, rollout, decision, len(completing))
            decision += 1
            ordered.append(completing.pop(index))
        batch = []
        for arm in ordered:
            active = active_by_lane.pop(arm)
            world.active.pop(arm, None)
            v3._record(trace, "complete", active.operation, world.time_ns)
            batch.append((arm, active.operation))
        for arm, completed_operation in batch:
            outcome = _semantic_effect(world, completed_operation, assets)
            cursors[arm] += 1
            if outcome is not None:
                return (outcome, decision)
        outcome = _post_batch_collision(world, assets)
        if outcome is not None:
            return (outcome, decision)


def _single_rollout(
    source: str,
    *,
    source_sha256: str,
    rollout: int,
    assets: v3.PublicAssets,
) -> Mapping[str, Any]:
    decision = 0
    valuation_index = _choice(
        source_sha256, rollout, decision, len(FINITE_VALUATIONS)
    )
    decision += 1
    valuation = FINITE_VALUATIONS[valuation_index]
    executable = v3.compile_source(source, valuation)
    world = _new_world()
    trace: list[Mapping[str, Any]] = []
    initial = v3._collision_at(world, 0, assets)
    if initial is not None:
        return {
            "rollout_index": rollout,
            "valuation": valuation,
            "outcome": "violation",
            "reason": "RANDOM_INITIAL_COLLISION",
            "trace": trace,
        }
    for operation in executable:
        if isinstance(operation, v3.ParallelOperation):
            outcome, decision = _run_parallel(
                world,
                operation,
                assets,
                trace,
                source_sha256=source_sha256,
                rollout=rollout,
                decision_start=decision,
            )
        else:
            outcome = _run_sequential(world, operation, assets, trace)
        if outcome is not None:
            return {
                "rollout_index": rollout,
                "valuation": valuation,
                **outcome,
                "trace": trace,
            }
    unreleased = sorted(
        key for key, owner in world.resources.items() if owner is not None
    )
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


def run_independent_random_dynamic_v4(
    source: str,
    *,
    source_sha256: str,
    asset_root: Path,
    rollouts: int = ROLLOUTS,
    wall_timeout_seconds: float = WALL_TIMEOUT_SECONDS,
) -> Mapping[str, Any]:
    """Run the corrected sampled monitor without any BiSafeCode method code."""

    actual_sha = hashlib.sha256(source.encode("utf-8")).hexdigest()
    if actual_sha != source_sha256:
        raise v3.IndependentRuntimeError("source identity mismatch")
    if rollouts < 1 or wall_timeout_seconds <= 0:
        raise v3.IndependentRuntimeError("rollout and wall budgets must be positive")
    assets = v3.load_public_assets(asset_root)
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
                    source,
                    source_sha256=source_sha256,
                    rollout=rollout,
                    assets=assets,
                )
            )
        except (
            SyntaxError,
            v3.IndependentRuntimeError,
            KeyError,
            TypeError,
            ValueError,
        ) as error:
            invalid_error = f"{type(error).__name__}:{error}"
            break
    outcomes = [str(record["outcome"]) for record in records]
    if invalid_error is not None:
        status, prediction, reasons = (
            "invalid",
            "invalid",
            ["INDEPENDENT_RUNTIME_INVALID_INPUT"],
        )
    elif any(value == "violation" for value in outcomes):
        status, prediction, reasons = (
            "ok",
            "unsafe",
            ["INDEPENDENT_RANDOM_VIOLATION_OBSERVED"],
        )
    elif len(records) == rollouts and all(
        value == "safe-completion" for value in outcomes
    ):
        status, prediction, reasons = (
            "ok",
            "safe",
            ["NO_VIOLATION_OBSERVED_IN_100_INDEPENDENT_ROLLOUTS"],
        )
    else:
        status, prediction, reasons = (
            "resource_truncated",
            "unknown",
            ["INDEPENDENT_RANDOM_BUDGET_EXHAUSTED"],
        )
    valuation_counts = {
        f"mode={mode},ready={str(ready).lower()}": sum(
            1
            for record in records
            if record.get("valuation") == {"mode": mode, "ready": ready}
        )
        for mode in ("fast", "safe")
        for ready in (False, True)
    }
    return {
        "status": status,
        "method_id": "independent_random_schedule_dynamic_monitor_v4",
        "predicted_class": prediction,
        "observed_outcome": (
            "violation_observed"
            if prediction == "unsafe"
            else "no_violation_observed"
            if prediction == "safe"
            else prediction
        ),
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
        "claim_boundary": "safe means no violation observed; never verified",
        "atomic_simultaneous_completion": True,
        "implementation_independence": {
            "stdlib_ast_interpreter": True,
            "own_event_scheduler": True,
            "own_runtime_monitors": True,
            "historical_v3_runtime_utilities_reused": True,
            "bisafecode_explicit_state_imported": False,
            "bisafecode_property_adapter_imported": False,
            "bisafecode_oracle_imported": False,
            "oracle_labels_or_outputs_visible": False,
            "exhaustive_claim": False,
        },
        "rollouts": records,
    }
