"""Clean-room source-event extraction for the EXP-S4-002 oracle.

FREEZE_ONLY_NOT_FORMAL_STAGE4_EVIDENCE

The extractor uses only :mod:`ast`, immutable source text, finite inputs, and
the frozen trajectory-duration map.  In particular it does not import the
BiSafeCode parser, Timed IR, successor relation, properties, or verdicts.
Every executed source action is represented by a start and end event carrying
an immutable source span, a static canonical action ordinal, and an execution
ordinal.  A trace is complete only after a unique terminal record is emitted.
"""

from __future__ import annotations

import ast
import hashlib
from dataclasses import dataclass
from typing import Any, Dict, Mapping, Sequence, Tuple

from . import FREEZE_STATUS


ACTIONS = {
    "wait",
    "move",
    "close",
    "open",
    "acquire",
    "release",
    "transfer_authority",
}


class IndependentTraceError(ValueError):
    pass


def _call_name(call: ast.Call) -> str:
    if not isinstance(call.func, ast.Name):
        raise IndependentTraceError("only direct calls are allowed")
    return call.func.id


def _literal(node: ast.AST):
    if isinstance(node, ast.Constant) and type(node.value) in {bool, int, str}:
        return node.value
    raise IndependentTraceError("only bool, int, and str literals are allowed")


def _bind(call: ast.Call, names: Sequence[str]) -> Mapping[str, Any]:
    if len(call.args) > len(names):
        raise IndependentTraceError("too many positional arguments")
    values = {name: _literal(node) for name, node in zip(names, call.args)}
    for keyword in call.keywords:
        if keyword.arg is None or keyword.arg not in names or keyword.arg in values:
            raise IndependentTraceError("invalid keyword argument")
        values[keyword.arg] = _literal(keyword.value)
    if set(values) != set(names):
        raise IndependentTraceError("missing argument")
    return values


def _predicate(node: ast.AST, inputs: Mapping[str, Any]) -> bool:
    if isinstance(node, ast.Name) and node.id in inputs:
        return bool(inputs[node.id])
    if (
        isinstance(node, ast.UnaryOp)
        and isinstance(node.op, ast.Not)
        and isinstance(node.operand, ast.Name)
        and node.operand.id in inputs
    ):
        return not bool(inputs[node.operand.id])
    if (
        isinstance(node, ast.Compare)
        and isinstance(node.left, ast.Name)
        and node.left.id in inputs
        and len(node.ops) == 1
        and len(node.comparators) == 1
    ):
        observed = inputs[node.left.id]
        expected = _literal(node.comparators[0])
        equal = type(observed) is type(expected) and observed == expected
        if isinstance(node.ops[0], ast.Eq):
            return equal
        if isinstance(node.ops[0], ast.NotEq):
            return not equal
    raise IndependentTraceError("unsupported or undeclared finite predicate")


def _span(node: ast.AST) -> Mapping[str, int]:
    return {
        "line": int(getattr(node, "lineno", 0)),
        "column": int(getattr(node, "col_offset", -1)),
        "end_line": int(getattr(node, "end_lineno", 0)),
        "end_column": int(getattr(node, "end_col_offset", -1)),
    }


def _static_ordinals(module: ast.Module) -> Mapping[Tuple[int, int, int, int], int]:
    calls = []
    for node in ast.walk(module):
        if isinstance(node, ast.Call) and isinstance(node.func, ast.Name):
            if node.func.id in ACTIONS:
                span = _span(node)
                calls.append((span["line"], span["column"], span["end_line"], span["end_column"]))
    ordered = sorted(set(calls))
    if len(ordered) != len(calls):
        raise IndependentTraceError("source action spans are not unique")
    return {value: ordinal for ordinal, value in enumerate(ordered)}


@dataclass(frozen=True)
class _ProducedAction:
    start_ns: int
    end_ns: int
    events: Tuple[Mapping[str, Any], ...]


class _Extractor:
    def __init__(
        self,
        source: str,
        inputs: Mapping[str, Any],
        trajectory_durations_ns: Mapping[str, int],
    ):
        module = ast.parse(source, filename="program.py", mode="exec")
        self.functions: Dict[str, ast.FunctionDef] = {}
        for statement in module.body:
            if not isinstance(statement, ast.FunctionDef):
                raise IndependentTraceError("top level must contain only functions")
            if statement.name in self.functions:
                raise IndependentTraceError("duplicate function")
            self.functions[statement.name] = statement
        if "task" not in self.functions:
            raise IndependentTraceError("task function is required")
        if set(inputs) != {"mode", "ready"}:
            raise IndependentTraceError("finite inputs must be exactly mode and ready")
        if inputs["mode"] not in {"safe", "fast"} or type(inputs["ready"]) is not bool:
            raise IndependentTraceError("finite input valuation is outside the frozen domain")
        self.source = source
        self.inputs = dict(inputs)
        self.trajectory_durations_ns = dict(trajectory_durations_ns)
        self.static_ordinals = _static_ordinals(module)
        self._insertion = 0
        self._execution_ordinal = 0

    def _event(self, time_ns: int, kind: str, **fields: Any) -> Mapping[str, Any]:
        self._insertion += 1
        return {"_insertion": self._insertion, "time_ns": time_ns, "event_kind": kind, **fields}

    def _action(self, call: ast.Call, now_ns: int) -> Tuple[list, int]:
        name = _call_name(call)
        signatures = {
            "wait": ("arm", "duration_ns"),
            "close": ("arm", "object_id", "duration_ns"),
            "open": ("arm", "object_id", "duration_ns"),
            "acquire": ("arm", "resource_id"),
            "release": ("arm", "resource_id"),
            "transfer_authority": ("object_id", "sender", "receiver"),
            "move": ("arm", "trajectory_hash"),
        }
        if name not in signatures:
            raise IndependentTraceError("unsupported action")
        values = dict(_bind(call, signatures[name]))
        span = _span(call)
        span_key = (span["line"], span["column"], span["end_line"], span["end_column"])
        static_ordinal = self.static_ordinals[span_key]
        execution_ordinal = self._execution_ordinal
        self._execution_ordinal += 1
        duration = int(values.get("duration_ns", 0))
        if duration < 0:
            raise IndependentTraceError("negative duration")
        if name == "move":
            trajectory = str(values["trajectory_hash"])
            if trajectory not in self.trajectory_durations_ns:
                raise IndependentTraceError("trajectory identity is not frozen")
            duration = self.trajectory_durations_ns[trajectory]
        end_ns = now_ns + duration
        common = {
            "action_kind": name,
            "action_execution_ordinal": execution_ordinal,
            "canonical_action_ordinal": static_ordinal,
            "source_span": span,
            "arm": values.get("arm"),
            "object_id": values.get("object_id"),
            "resource_id": values.get("resource_id"),
            "trajectory_hash": values.get("trajectory_hash"),
        }
        start = self._event(now_ns, "action_start", **common)
        end = self._event(end_ns, "action_end", **common)
        events = [start, end]
        if name not in {"wait", "move"}:
            semantic = dict(common)
            semantic.update({key: value for key, value in values.items() if key not in semantic})
            events.append(self._event(end_ns, name, **semantic))
        return events, end_ns

    def _parallel(self, call: ast.Call, now_ns: int) -> Tuple[list, int]:
        if len(call.args) != 2 or call.keywords:
            raise IndependentTraceError("parallel requires two helper names")
        names = []
        for node in call.args:
            if not isinstance(node, ast.Name) or node.id not in self.functions:
                raise IndependentTraceError("parallel helper is unknown")
            names.append(node.id)
        lanes = []
        barriers = []
        for name in names:
            phases = [[]]
            lane_barriers = []
            for statement in self.functions[name].body:
                if (
                    isinstance(statement, ast.Expr)
                    and isinstance(statement.value, ast.Call)
                    and _call_name(statement.value) == "barrier"
                ):
                    bound = _bind(statement.value, ("barrier_id",))
                    lane_barriers.append((str(bound["barrier_id"]), _span(statement.value)))
                    phases.append([])
                else:
                    phases[-1].append(statement)
            lanes.append(phases)
            barriers.append(lane_barriers)
        if [item[0] for item in barriers[0]] != [item[0] for item in barriers[1]]:
            raise IndependentTraceError("parallel barrier sequence mismatch")
        events = []
        phase_start = now_ns
        for index, (left_phase, right_phase) in enumerate(zip(lanes[0], lanes[1])):
            left_events, left_end = self._block(left_phase, phase_start, allow_parallel=False)
            right_events, right_end = self._block(right_phase, phase_start, allow_parallel=False)
            events.extend(left_events)
            events.extend(right_events)
            phase_start = max(left_end, right_end)
            if index < len(barriers[0]):
                barrier_id = barriers[0][index][0]
                for lane, (_same_id, span) in zip(("left", "right"), (barriers[0][index], barriers[1][index])):
                    events.append(
                        self._event(
                            phase_start,
                            "barrier",
                            barrier_id=barrier_id,
                            arm=lane,
                            object_id=None,
                            resource_id=None,
                            source_span=span,
                        )
                    )
        return events, phase_start

    def _statement(self, statement: ast.stmt, now_ns: int, allow_parallel: bool) -> Tuple[list, int]:
        if isinstance(statement, ast.Pass):
            return [], now_ns
        if isinstance(statement, ast.Expr) and isinstance(statement.value, ast.Call):
            name = _call_name(statement.value)
            if name == "parallel":
                if not allow_parallel:
                    raise IndependentTraceError("nested parallel is prohibited")
                return self._parallel(statement.value, now_ns)
            if name == "barrier":
                raise IndependentTraceError("orphan barrier")
            return self._action(statement.value, now_ns)
        if isinstance(statement, ast.If):
            branch = statement.body if _predicate(statement.test, self.inputs) else statement.orelse
            if not branch:
                raise IndependentTraceError("if requires else")
            return self._block(branch, now_ns, allow_parallel=allow_parallel)
        if isinstance(statement, ast.For):
            if not (
                isinstance(statement.iter, ast.Call)
                and _call_name(statement.iter) == "range"
                and len(statement.iter.args) == 1
                and not statement.iter.keywords
            ):
                raise IndependentTraceError("loop must use range literal")
            count = _literal(statement.iter.args[0])
            if isinstance(count, bool) or not isinstance(count, int) or not 0 <= count <= 4:
                raise IndependentTraceError("loop bound outside oracle grammar")
            events = []
            for _ in range(count):
                produced, now_ns = self._block(statement.body, now_ns, allow_parallel=allow_parallel)
                events.extend(produced)
            return events, now_ns
        raise IndependentTraceError("unsupported statement")

    def _block(self, statements: Sequence[ast.stmt], now_ns: int, *, allow_parallel: bool) -> Tuple[list, int]:
        events = []
        for statement in statements:
            produced, now_ns = self._statement(statement, now_ns, allow_parallel)
            events.extend(produced)
        return events, now_ns

    def run(self, program_id: str) -> Mapping[str, Any]:
        events, terminal_ns = self._block(self.functions["task"].body, 0, allow_parallel=True)
        events.append(
            self._event(
                terminal_ns,
                "terminal",
                arm=None,
                object_id=None,
                resource_id=None,
                terminal_status="normal",
            )
        )
        ordered = sorted(events, key=lambda event: (event["time_ns"], event["_insertion"]))
        cleaned = []
        for ordinal, event in enumerate(ordered):
            item = dict(event)
            item.pop("_insertion")
            item["event_ordinal"] = ordinal
            item["program_id"] = program_id
            cleaned.append(item)
        return {
            "evidence_status": FREEZE_STATUS,
            "schema": "bisafecode.stage4.controlled.logical-event-trace/v2",
            "program_id": program_id,
            "source_sha256": hashlib.sha256(self.source.encode("utf-8")).hexdigest(),
            "inputs": dict(sorted(self.inputs.items())),
            "complete": True,
            "source_action_count": self._execution_ordinal,
            "terminal_time_ns": terminal_ns,
            "events": cleaned,
        }


def extract_event_trace(
    source: str,
    *,
    program_id: str,
    inputs: Mapping[str, Any],
    trajectory_durations_ns: Mapping[str, int],
) -> Mapping[str, Any]:
    return _Extractor(source, inputs, trajectory_durations_ns).run(program_id)


def extract_all_finite_traces(
    source: str,
    *,
    program_id: str,
    trajectory_durations_ns: Mapping[str, int],
) -> Tuple[Mapping[str, Any], ...]:
    """Enumerate the frozen mode×ready input universe in lexical order."""

    return tuple(
        extract_event_trace(
            source,
            program_id=program_id,
            inputs={"mode": mode, "ready": ready},
            trajectory_durations_ns=trajectory_durations_ns,
        )
        for mode in ("fast", "safe")
        for ready in (False, True)
    )
