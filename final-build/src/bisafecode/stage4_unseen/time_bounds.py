"""Independent structural time-bound audit for Stage 4 prep programs.

PREP_ONLY_NOT_FORMAL_STAGE4_EVIDENCE

Parallel helpers are split at barriers.  Their upper bound is the sum of the
per-phase maxima, not the maximum of whole-lane totals.
"""

from __future__ import annotations

import ast
from typing import Dict, List, Mapping, Sequence, Tuple


class TimeBoundError(ValueError):
    """Raised when source cannot be bounded by the frozen prep grammar."""


def _call_name(call: ast.Call) -> str:
    if not isinstance(call.func, ast.Name):
        raise TimeBoundError("only direct fixed-API calls are supported")
    return call.func.id


def _literal(node: ast.AST):
    if isinstance(node, ast.Constant) and isinstance(node.value, (bool, int, str)):
        return node.value
    raise TimeBoundError("time-bound analysis requires literal arguments")


def _bind(call: ast.Call, names: Sequence[str]) -> Mapping[str, ast.AST]:
    if len(call.args) > len(names):
        raise TimeBoundError("too many positional arguments")
    values = {name: value for name, value in zip(names, call.args)}
    for keyword in call.keywords:
        if keyword.arg is None or keyword.arg not in names or keyword.arg in values:
            raise TimeBoundError("invalid keyword argument")
        values[keyword.arg] = keyword.value
    if set(values) != set(names):
        raise TimeBoundError("missing required argument")
    return values


def parallel_phase_upper_bound_ns(
    left_phases: Sequence[int], right_phases: Sequence[int]
) -> int:
    """Return ``sum(max(left_phase, right_phase))`` fail-closed."""

    if len(left_phases) != len(right_phases) or not left_phases:
        raise TimeBoundError("parallel lanes must expose the same nonempty phases")
    if any(value < 0 for value in tuple(left_phases) + tuple(right_phases)):
        raise TimeBoundError("phase durations must be nonnegative")
    return sum(max(left, right) for left, right in zip(left_phases, right_phases))


class _Analyzer:
    _DURATION_SIGNATURES = {
        "wait": ("arm", "duration_ns"),
        "close": ("arm", "object_id", "duration_ns"),
        "open": ("arm", "object_id", "duration_ns"),
    }
    _ZERO_DURATION_SIGNATURES = {
        "acquire": ("arm", "resource_id"),
        "release": ("arm", "resource_id"),
        "transfer_authority": ("object_id", "sender", "receiver"),
        "barrier": ("barrier_id",),
    }

    def __init__(self, source: str, trajectory_durations_ns: Mapping[str, int]):
        try:
            module = ast.parse(source, filename="program.py", mode="exec")
        except SyntaxError as error:
            raise TimeBoundError(str(error)) from error
        self.trajectory_durations_ns = dict(trajectory_durations_ns)
        self.functions: Dict[str, ast.FunctionDef] = {}
        for statement in module.body:
            if not isinstance(statement, ast.FunctionDef):
                raise TimeBoundError("top level must contain only functions")
            if statement.name in self.functions:
                raise TimeBoundError("duplicate function")
            self.functions[statement.name] = statement
        if "task" not in self.functions:
            raise TimeBoundError("task() is required")

    def action_duration_ns(self, call: ast.Call) -> int:
        name = _call_name(call)
        if name in self._DURATION_SIGNATURES:
            bound = _bind(call, self._DURATION_SIGNATURES[name])
            duration = _literal(bound["duration_ns"])
            if isinstance(duration, bool) or not isinstance(duration, int) or duration < 0:
                raise TimeBoundError("duration must be a nonnegative integer")
            return duration
        if name == "move":
            bound = _bind(call, ("arm", "trajectory_hash"))
            trajectory_hash = _literal(bound["trajectory_hash"])
            if trajectory_hash not in self.trajectory_durations_ns:
                raise TimeBoundError("trajectory duration is not frozen")
            duration = self.trajectory_durations_ns[trajectory_hash]
            if isinstance(duration, bool) or not isinstance(duration, int) or duration < 0:
                raise TimeBoundError("trajectory duration must be nonnegative")
            return duration
        if name in self._ZERO_DURATION_SIGNATURES:
            _bind(call, self._ZERO_DURATION_SIGNATURES[name])
            return 0
        raise TimeBoundError("unsupported action: {}".format(name))

    def _lane_phases(self, function_name: str) -> Tuple[List[int], List[str]]:
        if function_name not in self.functions or function_name == "task":
            raise TimeBoundError("parallel lane must name a helper function")
        phases = [0]
        barriers: List[str] = []
        for statement in self.functions[function_name].body:
            if isinstance(statement, ast.Pass):
                continue
            if not (isinstance(statement, ast.Expr) and isinstance(statement.value, ast.Call)):
                raise TimeBoundError("parallel helpers may contain only actions and barriers")
            call = statement.value
            name = _call_name(call)
            if name == "barrier":
                bound = _bind(call, ("barrier_id",))
                barrier_id = _literal(bound["barrier_id"])
                if not isinstance(barrier_id, str):
                    raise TimeBoundError("barrier ID must be a string")
                barriers.append(barrier_id)
                phases.append(0)
            else:
                phases[-1] += self.action_duration_ns(call)
        return phases, barriers

    def _parallel_duration_ns(self, call: ast.Call) -> int:
        bound = _bind(call, ("left", "right"))
        lane_names = []
        for role in ("left", "right"):
            node = bound[role]
            if not isinstance(node, ast.Name):
                raise TimeBoundError("parallel lane must be a helper name")
            lane_names.append(node.id)
        left_phases, left_barriers = self._lane_phases(lane_names[0])
        right_phases, right_barriers = self._lane_phases(lane_names[1])
        if left_barriers != right_barriers:
            raise TimeBoundError("parallel lanes must use identical barrier sequences")
        return parallel_phase_upper_bound_ns(left_phases, right_phases)

    def _statement_duration_ns(self, statement: ast.stmt) -> int:
        if isinstance(statement, ast.Pass):
            return 0
        if isinstance(statement, ast.Expr) and isinstance(statement.value, ast.Call):
            if _call_name(statement.value) == "parallel":
                return self._parallel_duration_ns(statement.value)
            if _call_name(statement.value) == "barrier":
                raise TimeBoundError("barrier is only valid inside a parallel helper")
            return self.action_duration_ns(statement.value)
        if isinstance(statement, ast.If):
            if not statement.orelse:
                raise TimeBoundError("if requires else")
            return max(self._block_duration_ns(statement.body), self._block_duration_ns(statement.orelse))
        if isinstance(statement, ast.For):
            if not (
                isinstance(statement.iter, ast.Call)
                and _call_name(statement.iter) == "range"
                and len(statement.iter.args) == 1
                and not statement.iter.keywords
            ):
                raise TimeBoundError("loop must use range(N)")
            count = _literal(statement.iter.args[0])
            if isinstance(count, bool) or not isinstance(count, int) or count < 0:
                raise TimeBoundError("loop bound must be nonnegative")
            return count * self._block_duration_ns(statement.body)
        raise TimeBoundError("unsupported timed statement")

    def _block_duration_ns(self, statements: Sequence[ast.stmt]) -> int:
        return sum(self._statement_duration_ns(statement) for statement in statements)

    def task_duration_ns(self) -> int:
        return self._block_duration_ns(self.functions["task"].body)


def source_time_upper_bound_ns(
    source: str, trajectory_durations_ns: Mapping[str, int]
) -> int:
    """Compute the structural upper bound without executing the source."""

    return _Analyzer(source, trajectory_durations_ns).task_duration_ns()
