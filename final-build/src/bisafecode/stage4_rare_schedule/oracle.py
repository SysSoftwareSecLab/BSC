"""Clean-room exact oracle for EXP-S4-009 resource interleavings.

This module uses only the Python standard library.  It parses the two lane
traces, explores every arm-preserving interleaving, and computes the exact
violation probability of a scheduler that selects uniformly between enabled
lanes.  It imports neither BiSafeCode nor the random baseline.
"""

from __future__ import annotations

import ast
from dataclasses import dataclass
from fractions import Fraction
from functools import lru_cache
from typing import Any, Mapping, Sequence


ARMS = ("left", "right")
RESOURCES = ("fixture_alpha", "tool_beta")


class RareScheduleOracleError(ValueError):
    pass


@dataclass(frozen=True)
class ResourceAction:
    kind: str
    arm: str
    resource: str
    line: int


def _literal_string(node: ast.AST) -> str:
    if not isinstance(node, ast.Constant) or not isinstance(node.value, str):
        raise RareScheduleOracleError("resource action arguments must be strings")
    return node.value


def _lane_actions(function: ast.FunctionDef, expected_arm: str) -> tuple[ResourceAction, ...]:
    actions = []
    for statement in function.body:
        if (
            not isinstance(statement, ast.Expr)
            or not isinstance(statement.value, ast.Call)
            or not isinstance(statement.value.func, ast.Name)
            or statement.value.func.id not in {"acquire", "release"}
            or len(statement.value.args) != 2
            or statement.value.keywords
        ):
            raise RareScheduleOracleError("lane contains a non-resource action")
        arm, resource = (_literal_string(item) for item in statement.value.args)
        if arm != expected_arm or resource not in RESOURCES:
            raise RareScheduleOracleError("lane arm affinity or resource domain failed")
        actions.append(ResourceAction(statement.value.func.id, arm, resource, statement.lineno))
    if not actions:
        raise RareScheduleOracleError("lane cannot be empty")
    return tuple(actions)


def parse_resource_lanes(source: str) -> tuple[tuple[ResourceAction, ...], tuple[ResourceAction, ...]]:
    module = ast.parse(source, filename="opaque_rare_schedule_case.py", mode="exec")
    if any(not isinstance(statement, ast.FunctionDef) for statement in module.body):
        raise RareScheduleOracleError("only the three required functions are allowed")
    functions = {
        statement.name: statement
        for statement in module.body
        if isinstance(statement, ast.FunctionDef)
    }
    if set(functions) != {"left_lane", "right_lane", "task"}:
        raise RareScheduleOracleError("exactly two lanes and task are required")
    if any(
        function.decorator_list
        or function.args.posonlyargs
        or function.args.args
        or function.args.kwonlyargs
        or function.args.vararg is not None
        or function.args.kwarg is not None
        for function in functions.values()
    ):
        raise RareScheduleOracleError("challenge functions must be undecorated and argument-free")
    task = functions["task"]
    if len(task.body) != 1 or not isinstance(task.body[0], ast.Expr):
        raise RareScheduleOracleError("task must contain exactly one parallel call")
    call = task.body[0].value
    if (
        not isinstance(call, ast.Call)
        or not isinstance(call.func, ast.Name)
        or call.func.id != "parallel"
        or len(call.args) != 2
        or call.keywords
        or not all(isinstance(item, ast.Name) for item in call.args)
        or tuple(item.id for item in call.args) != ("left_lane", "right_lane")
    ):
        raise RareScheduleOracleError("task parallel binding is invalid")
    return (
        _lane_actions(functions["left_lane"], "left"),
        _lane_actions(functions["right_lane"], "right"),
    )


def evaluate_exact_schedule_oracle(source: str) -> Mapping[str, Any]:
    lanes = parse_resource_lanes(source)
    resources = {value: index for index, value in enumerate(RESOURCES)}
    visited = set()

    def apply(
        owners: tuple[int, ...], action: ResourceAction, owner: int
    ) -> tuple[tuple[int, ...] | None, str | None]:
        index = resources[action.resource]
        current = owners[index]
        if action.kind == "acquire":
            if current != -1:
                return None, "RESOURCE_DOUBLE_ACQUIRE"
            updated = list(owners)
            updated[index] = owner
            return tuple(updated), None
        if current != owner:
            return None, "RESOURCE_RELEASE_BY_NONOWNER"
        updated = list(owners)
        updated[index] = -1
        return tuple(updated), None

    @lru_cache(maxsize=None)
    def solve(
        left_pc: int, right_pc: int, owners: tuple[int, ...]
    ) -> tuple[Fraction, int, int, tuple[str, ...] | None, str | None]:
        visited.add((left_pc, right_pc, owners))
        if left_pc == len(lanes[0]) and right_pc == len(lanes[1]):
            if any(value != -1 for value in owners):
                return Fraction(1), 1, 0, (), "RESOURCE_NOT_RELEASED"
            return Fraction(0), 0, 1, None, None
        enabled = []
        if left_pc < len(lanes[0]):
            enabled.append(0)
        if right_pc < len(lanes[1]):
            enabled.append(1)
        probability = Fraction(0)
        violating_paths = 0
        safe_paths = 0
        witness = None
        witness_reason = None
        for lane in enabled:
            action = lanes[lane][left_pc if lane == 0 else right_pc]
            updated, reason = apply(owners, action, lane)
            if reason is not None:
                candidate = (Fraction(1), 1, 0, (ARMS[lane],), reason)
            else:
                candidate = solve(
                    left_pc + (lane == 0),
                    right_pc + (lane == 1),
                    updated,
                )
                if candidate[3] is not None:
                    candidate = (
                        candidate[0], candidate[1], candidate[2],
                        (ARMS[lane],) + candidate[3], candidate[4],
                    )
            probability += candidate[0] / len(enabled)
            violating_paths += candidate[1]
            safe_paths += candidate[2]
            if witness is None and candidate[3] is not None:
                witness, witness_reason = candidate[3], candidate[4]
        return probability, violating_paths, safe_paths, witness, witness_reason

    initial_owners = tuple(-1 for _ in RESOURCES)
    probability, violating_paths, safe_paths, witness, reason = solve(
        0, 0, initial_owners
    )
    return {
        "schema": "bisafecode.stage4.rare-schedule.exact-oracle/v1",
        "verdict": "unsafe" if probability > 0 else "safe",
        "reason_codes": [reason] if reason else [],
        "exact_exposure_probability": f"{probability.numerator}/{probability.denominator}",
        "exposure_probability": float(probability),
        "reachable_abstract_states": len(visited),
        "violating_schedule_suffix_paths": violating_paths,
        "safe_schedule_suffix_paths": safe_paths,
        "first_violation_lane_witness": list(witness) if witness is not None else None,
        "parsed_action_counts": {
            "left": len(lanes[0]),
            "right": len(lanes[1]),
        },
        "method_outputs_visible": False,
        "random_outputs_visible": False,
    }


def expected_detection_probability(exposure: str, rollouts: int) -> Fraction:
    if isinstance(rollouts, bool) or not isinstance(rollouts, int) or rollouts < 1:
        raise ValueError("rollouts must be a positive integer")
    numerator, denominator = exposure.split("/", 1)
    probability = Fraction(int(numerator), int(denominator))
    if not 0 <= probability <= 1:
        raise ValueError("exposure probability is outside [0,1]")
    return 1 - (1 - probability) ** rollouts
