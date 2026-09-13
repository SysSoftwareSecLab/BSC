"""True timed random schedule/path execution baseline for EXP-S4-006."""

from __future__ import annotations

import hashlib
import time
from typing import Any, Mapping

from bisafecode.explicit_state import ExplicitStateEngine, IntervalStatus, state_fingerprint
from bisafecode.stage4_controlled.property_adapters import (
    build_full_method_inputs,
    controlled_interval_property,
    controlled_point_property,
)
from bisafecode.verdicts import Verdict


ROLLOUTS = 100
MAX_STEPS_PER_ROLLOUT = 10_000
MAX_TOTAL_TRANSITIONS = 500_000
MAX_MODEL_TIME_NS = 5_000_000_000
WALL_TIMEOUT_SECONDS = 120.0
SEED_PREFIX = "EXP-S4-006:random-timed-schedule"


def _choice(program_id: str, rollout: int, decision: int, population: int) -> int:
    if population < 1:
        raise ValueError("random choice requires a nonempty population")
    payload = f"{SEED_PREFIX}:{program_id}:{rollout}:{decision}".encode("utf-8")
    return int.from_bytes(hashlib.sha256(payload).digest(), "big") % population


def run_random_timed_schedule(
    source: str,
    *,
    program_id: str,
    rollouts: int = ROLLOUTS,
    wall_timeout_seconds: float = WALL_TIMEOUT_SECONDS,
) -> Mapping[str, Any]:
    """Execute complete sampled Timed-IR paths without an exhaustive claim."""

    if rollouts < 1 or wall_timeout_seconds <= 0:
        raise ValueError("rollouts and wall timeout must be positive")
    started = time.monotonic()
    deadline = started + wall_timeout_seconds
    program, environment = build_full_method_inputs(source, program_id=program_id)
    engine = ExplicitStateEngine(
        program,
        environment,
        interval_checker=controlled_interval_property,
        point_checker=controlled_point_property,
    )
    issues = engine.configuration_issues()
    if issues:
        return {
            "status": "invalid",
            "predicted_class": "invalid",
            "reason_codes": list(issues),
            "rollouts": [],
            "scheduled_rollouts": rollouts,
            "completed_rollouts": 0,
            "elapsed_ms": (time.monotonic() - started) * 1000.0,
        }
    initials = engine.initial_states()
    records: list[Mapping[str, Any]] = []
    total_selected_transitions = 0
    total_enabled_candidates = 0
    global_bound_reason: str | None = None

    for rollout in range(rollouts):
        if time.monotonic() >= deadline:
            global_bound_reason = "RANDOM_WALL_BUDGET_EXHAUSTED"
            break
        decision = 0
        initial_index = _choice(program_id, rollout, decision, len(initials))
        decision += 1
        state = initials[initial_index]
        path: list[Mapping[str, Any]] = []
        outcome = "inconclusive"
        reason = "RANDOM_STEP_BOUND_EXHAUSTED"
        for step in range(MAX_STEPS_PER_ROLLOUT):
            if time.monotonic() >= deadline:
                global_bound_reason = "RANDOM_WALL_BUDGET_EXHAUSTED"
                reason = global_bound_reason
                break
            point = controlled_point_property(state)
            if point.status in {IntervalStatus.COLLISION, IntervalStatus.VIOLATION}:
                outcome, reason = "violation", point.reason
                path.append({
                    "step": step,
                    "kind": "point-check",
                    "label": point.reason,
                    "source_fingerprint": state_fingerprint(state),
                    "time_ns": state.time_ns,
                })
                break
            if point.status is IntervalStatus.UNKNOWN:
                outcome, reason = "inconclusive", point.reason
                break
            enabled = engine.successors(state, max_time_ns=MAX_MODEL_TIME_NS)
            total_enabled_candidates += len(enabled)
            if total_enabled_candidates > MAX_TOTAL_TRANSITIONS:
                global_bound_reason = "RANDOM_TRANSITION_BUDGET_EXHAUSTED"
                reason = global_bound_reason
                break
            if not enabled:
                outcome, reason = "safe-completion", "NO_VIOLATION_OBSERVED_ON_SAMPLED_EXECUTION"
                break
            selected_index = _choice(program_id, rollout, decision, len(enabled))
            decision += 1
            transition = enabled[selected_index]
            total_selected_transitions += 1
            path.append({
                "step": step,
                "kind": transition.kind.value,
                "label": transition.label,
                "selected_index": selected_index,
                "enabled_count": len(enabled),
                "source_fingerprint": state_fingerprint(state),
                "target_fingerprint": (
                    state_fingerprint(transition.target)
                    if transition.target is not None
                    else None
                ),
            })
            if transition.terminal is not None:
                terminal = transition.terminal
                if terminal.verdict is Verdict.VIOLATED:
                    outcome, reason = "violation", terminal.reason
                elif terminal.verdict is Verdict.INVALID:
                    outcome, reason = "invalid", terminal.reason
                else:
                    outcome, reason = "inconclusive", terminal.reason
                break
            if transition.target is None:
                outcome, reason = "invalid", "MISSING_NONTERMINAL_TARGET"
                break
            state = transition.target
        records.append({
            "rollout_index": rollout,
            "initial_state_index": initial_index,
            "valuation": {
                name: value.to_python() for name, value in initials[initial_index].valuation
            },
            "outcome": outcome,
            "reason": reason,
            "selected_transitions": len(path),
            "path": path,
        })
        if global_bound_reason is not None:
            break

    outcomes = [str(item["outcome"]) for item in records]
    if any(item == "violation" for item in outcomes):
        predicted_class = "unsafe"
        reasons = ["RANDOM_VIOLATION_OBSERVED"]
    elif len(records) == rollouts and all(item == "safe-completion" for item in outcomes):
        predicted_class = "safe"
        reasons = ["NO_VIOLATION_OBSERVED_IN_100_SAMPLED_EXECUTIONS"]
    else:
        predicted_class = "unknown"
        reasons = [global_bound_reason or "RANDOM_EXECUTION_INCONCLUSIVE"]
    return {
        "status": "resource_truncated" if global_bound_reason else "ok",
        "predicted_class": predicted_class,
        "reason_codes": reasons,
        "scheduled_rollouts": rollouts,
        "completed_rollouts": len(records),
        "violation_rollouts": outcomes.count("violation"),
        "safe_completion_rollouts": outcomes.count("safe-completion"),
        "inconclusive_rollouts": outcomes.count("inconclusive"),
        "invalid_rollouts": outcomes.count("invalid"),
        "total_selected_transitions": total_selected_transitions,
        "total_enabled_candidates": total_enabled_candidates,
        "seed_rule": "SHA-256 counter stream over experiment, method, program, rollout, and decision",
        "no_exhaustive_claim": True,
        "elapsed_ms": (time.monotonic() - started) * 1000.0,
        "rollouts": records,
    }

