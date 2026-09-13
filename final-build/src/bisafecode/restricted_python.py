"""Static parser from the restricted Python surface language to Timed IR.

The parser uses :mod:`ast` only.  It never imports, evaluates, compiles, or
executes the program being analysed.  Robot/scene facts and trajectory
durations enter through a trusted, already validated sidecar context.
"""

from __future__ import annotations

import ast
import hashlib
from dataclasses import dataclass
from pathlib import Path
from typing import Optional, Union

from .timed_ir import (
    ActionKind,
    ActionNode,
    ActionSpec,
    Arm,
    BarrierNode,
    BranchNode,
    EndNode,
    FiniteInput,
    ForkNode,
    IRNode,
    JoinNode,
    LoopHeadNode,
    LoopLatchNode,
    NopNode,
    PredicateOp,
    PredicateSpec,
    SourceSpan,
    TimedProgram,
    ValidationIssue,
    validate_program,
)


LiteralValue = Union[bool, int, str]


@dataclass(frozen=True)
class TrajectoryBinding:
    sha256: str
    duration_ns: int


@dataclass(frozen=True)
class ParserContext:
    program_name: str
    finite_inputs: tuple[FiniteInput, ...]
    object_ids: tuple[str, ...]
    resource_ids: tuple[str, ...]
    trajectories: tuple[TrajectoryBinding, ...]
    environment_hash: str
    max_loop_bound: int


@dataclass(frozen=True)
class ParseIssue:
    code: str
    source: SourceSpan
    message: str


class InvalidRestrictedPython(ValueError):
    def __init__(self, issues: tuple[ParseIssue, ...]):
        self.issues = issues
        detail = "; ".join(
            f"{issue.code}@{issue.source.file}:{issue.source.line}:{issue.source.column}: "
            f"{issue.message}"
            for issue in issues
        )
        super().__init__(detail)


ACTION_SIGNATURES = {
    "move": ("arm", "trajectory_hash"),
    "wait": ("arm", "duration_ns"),
    "close": ("arm", "object_id", "duration_ns"),
    "open": ("arm", "object_id", "duration_ns"),
    "acquire": ("arm", "resource_id"),
    "release": ("arm", "resource_id"),
    "transfer_authority": ("object_id", "sender", "receiver"),
}


def _span(filename: str, node: ast.AST) -> SourceSpan:
    line = getattr(node, "lineno", 1)
    column = getattr(node, "col_offset", 0)
    end_line = getattr(node, "end_lineno", None) or line
    end_column = getattr(node, "end_col_offset", None)
    if end_column is None:
        end_column = column + 1
    return SourceSpan(filename, line, column, end_line, end_column)


def _strip_docstring(statements: list[ast.stmt]) -> list[ast.stmt]:
    if (
        statements
        and isinstance(statements[0], ast.Expr)
        and isinstance(statements[0].value, ast.Constant)
        and isinstance(statements[0].value.value, str)
    ):
        return statements[1:]
    return statements


class _Compiler:
    def __init__(self, source: str, filename: str, context: ParserContext):
        self.source = source
        self.filename = filename
        self.context = context
        self.nodes: list[IRNode] = []
        self.counter = 0
        self.parallel_counter = 0
        self.functions: dict[str, ast.FunctionDef] = {}
        self.used_branch_functions: set[str] = set()
        self.trajectory_durations = {
            binding.sha256: binding.duration_ns for binding in context.trajectories
        }
        self.input_domains = {
            finite_input.name: finite_input.values for finite_input in context.finite_inputs
        }

    def reject(self, code: str, node: ast.AST, message: str) -> None:
        raise InvalidRestrictedPython((ParseIssue(code, _span(self.filename, node), message),))

    def new_id(self, kind: str) -> str:
        self.counter += 1
        return f"n{self.counter:04d}_{kind}"

    def add(self, node: IRNode) -> str:
        self.nodes.append(node)
        return node.node_id

    def parse_module(self) -> TimedProgram:
        try:
            module = ast.parse(self.source, filename=self.filename, mode="exec")
        except SyntaxError as error:
            source = SourceSpan(
                self.filename,
                error.lineno or 1,
                max((error.offset or 1) - 1, 0),
                getattr(error, "end_lineno", None) or error.lineno or 1,
                max(
                    (
                        getattr(error, "end_offset", None)
                        or error.offset
                        or 1
                    )
                    - 1,
                    0,
                ),
            )
            raise InvalidRestrictedPython(
                (ParseIssue("SYNTAX", source, error.msg),)
            ) from error

        body = _strip_docstring(module.body)
        for statement in body:
            if not isinstance(statement, ast.FunctionDef):
                self.reject(
                    "TOP_LEVEL_STATEMENT",
                    statement,
                    "only function definitions and a module docstring are supported",
                )
            self._register_function(statement)

        if "task" not in self.functions:
            self.reject("MISSING_TASK", module, "exactly one task() entry function is required")

        task = self.functions["task"]
        end_id = self.add(EndNode(node_id=self.new_id("end"), source=_span(self.filename, task)))
        entry_id = self.compile_block(
            _strip_docstring(task.body),
            end_id,
            branch_arm=None,
            control_depth=0,
        )

        unreferenced = sorted(set(self.functions) - {"task"} - self.used_branch_functions)
        if unreferenced:
            function = self.functions[unreferenced[0]]
            self.reject(
                "UNREFERENCED_FUNCTION",
                function,
                f"helper function {function.name!r} is not a parallel branch",
            )

        program = TimedProgram(
            name=self.context.program_name,
            entry_id=entry_id,
            nodes=tuple(self.nodes),
            finite_inputs=self.context.finite_inputs,
            object_ids=self.context.object_ids,
            resource_ids=self.context.resource_ids,
            trajectory_hashes=tuple(binding.sha256 for binding in self.context.trajectories),
            environment_hash=self.context.environment_hash,
            max_loop_bound=self.context.max_loop_bound,
            source_sha256=hashlib.sha256(self.source.encode("utf-8")).hexdigest(),
        )
        ir_issues = validate_program(program)
        if ir_issues:
            raise InvalidRestrictedPython(tuple(self._from_ir_issue(issue) for issue in ir_issues))
        return program

    def _from_ir_issue(self, issue: ValidationIssue) -> ParseIssue:
        if issue.node_id is not None:
            for node in self.nodes:
                if node.node_id == issue.node_id:
                    return ParseIssue(f"IR_{issue.code}", node.source, issue.message)
        return ParseIssue(
            f"IR_{issue.code}",
            SourceSpan(self.filename, 1, 0, 1, 1),
            issue.message,
        )

    def _register_function(self, function: ast.FunctionDef) -> None:
        if function.name in self.functions:
            self.reject("DUPLICATE_FUNCTION", function, f"duplicate function {function.name!r}")
        arguments = function.args
        if (
            function.decorator_list
            or arguments.posonlyargs
            or arguments.args
            or arguments.vararg is not None
            or arguments.kwonlyargs
            or arguments.kwarg is not None
            or arguments.defaults
            or arguments.kw_defaults
        ):
            self.reject(
                "FUNCTION_SIGNATURE",
                function,
                "task and parallel branch functions must be undecorated and parameterless",
            )
        self.functions[function.name] = function

    def compile_block(
        self,
        statements: list[ast.stmt],
        continuation: str,
        *,
        branch_arm: Optional[Arm],
        control_depth: int,
    ) -> str:
        entry = continuation
        for statement in reversed(statements):
            entry = self.compile_statement(
                statement,
                entry,
                branch_arm=branch_arm,
                control_depth=control_depth,
            )
        return entry

    def compile_statement(
        self,
        statement: ast.stmt,
        continuation: str,
        *,
        branch_arm: Optional[Arm],
        control_depth: int,
    ) -> str:
        if isinstance(statement, ast.Pass):
            return continuation
        if isinstance(statement, ast.Expr) and isinstance(statement.value, ast.Call):
            call_name = self._call_name(statement.value)
            if call_name == "parallel":
                if branch_arm is not None:
                    self.reject("NESTED_PARALLEL", statement, "parallel cannot appear in a branch")
                return self._compile_parallel(statement, statement.value, continuation)
            if call_name == "barrier":
                if branch_arm is None:
                    self.reject("ORPHAN_BARRIER", statement, "barrier must occur in a branch")
                if control_depth != 0:
                    self.reject(
                        "CONDITIONAL_BARRIER",
                        statement,
                        "barrier must be an unconditional top-level branch statement",
                    )
                values = self._bind_call(statement.value, ("barrier_id",))
                barrier_id = self._literal_str(values["barrier_id"], "barrier_id")
                return self.add(
                    BarrierNode(
                        node_id=self.new_id("barrier"),
                        source=_span(self.filename, statement),
                        barrier_id=barrier_id,
                        participants=(Arm.LEFT, Arm.RIGHT),
                        next_id=continuation,
                    )
                )
            if call_name in ACTION_SIGNATURES:
                return self._compile_action(
                    statement, statement.value, call_name, continuation, branch_arm
                )
            self.reject(
                "UNSUPPORTED_CALL",
                statement,
                f"call {call_name!r} is not in the fixed API whitelist",
            )
        if isinstance(statement, ast.If):
            if not statement.orelse:
                self.reject("IF_WITHOUT_ELSE", statement, "if requires an explicit else branch")
            predicate = self._predicate(statement.test)
            true_id = self.compile_block(
                statement.body,
                continuation,
                branch_arm=branch_arm,
                control_depth=control_depth + 1,
            )
            false_id = self.compile_block(
                statement.orelse,
                continuation,
                branch_arm=branch_arm,
                control_depth=control_depth + 1,
            )
            return self.add(
                BranchNode(
                    node_id=self.new_id("branch"),
                    source=_span(self.filename, statement),
                    predicate=predicate,
                    true_id=true_id,
                    false_id=false_id,
                )
            )
        if isinstance(statement, ast.For):
            return self._compile_repeat(
                statement,
                continuation,
                branch_arm=branch_arm,
                control_depth=control_depth,
            )
        self.reject(
            "UNSUPPORTED_STATEMENT",
            statement,
            f"{type(statement).__name__} is outside the restricted language",
        )
        raise AssertionError("unreachable")

    def _compile_action(
        self,
        statement: ast.Expr,
        call: ast.Call,
        name: str,
        continuation: str,
        branch_arm: Optional[Arm],
    ) -> str:
        values = self._bind_call(call, ACTION_SIGNATURES[name])
        span = _span(self.filename, statement)
        if name == "transfer_authority":
            if branch_arm is not None:
                self.reject(
                    "BRANCH_TRANSFER",
                    statement,
                    "transfer_authority must occur on the main thread after synchronization",
                )
            action = ActionSpec(
                kind=ActionKind.TRANSFER_AUTHORITY,
                duration_ns=0,
                object_id=self._literal_str(values["object_id"], "object_id"),
                sender=self._arm(values["sender"], "sender"),
                receiver=self._arm(values["receiver"], "receiver"),
            )
        else:
            arm = self._arm(values["arm"], "arm")
            if branch_arm is not None and arm is not branch_arm:
                self.reject(
                    "BRANCH_ARM_AFFINITY",
                    statement,
                    f"{branch_arm.value} branch cannot issue a {arm.value} action",
                )
            if name == "move":
                trajectory_hash = self._literal_str(
                    values["trajectory_hash"], "trajectory_hash"
                )
                if trajectory_hash not in self.trajectory_durations:
                    self.reject(
                        "UNKNOWN_TRAJECTORY",
                        values["trajectory_hash"],
                        "trajectory is absent from the trusted sidecar",
                    )
                action = ActionSpec(
                    kind=ActionKind.MOVE,
                    arm=arm,
                    duration_ns=self.trajectory_durations[trajectory_hash],
                    trajectory_hash=trajectory_hash,
                )
            elif name == "wait":
                action = ActionSpec(
                    kind=ActionKind.WAIT,
                    arm=arm,
                    duration_ns=self._literal_positive_int(values["duration_ns"], "duration_ns"),
                )
            elif name in {"close", "open"}:
                action = ActionSpec(
                    kind=ActionKind.CLOSE if name == "close" else ActionKind.OPEN,
                    arm=arm,
                    duration_ns=self._literal_positive_int(values["duration_ns"], "duration_ns"),
                    object_id=self._literal_str(values["object_id"], "object_id"),
                )
            else:
                action = ActionSpec(
                    kind=ActionKind.ACQUIRE if name == "acquire" else ActionKind.RELEASE,
                    arm=arm,
                    duration_ns=0,
                    resource_id=self._literal_str(values["resource_id"], "resource_id"),
                )
        return self.add(
            ActionNode(
                node_id=self.new_id(name),
                source=span,
                action=action,
                next_id=continuation,
            )
        )

    def _compile_repeat(
        self,
        statement: ast.For,
        continuation: str,
        *,
        branch_arm: Optional[Arm],
        control_depth: int,
    ) -> str:
        if statement.orelse:
            self.reject("FOR_ELSE", statement, "for-else is unsupported")
        if not isinstance(statement.target, ast.Name):
            self.reject("LOOP_TARGET", statement.target, "loop target must be a simple name")
        if not (
            isinstance(statement.iter, ast.Call)
            and self._call_name(statement.iter) == "range"
            and len(statement.iter.args) == 1
            and not statement.iter.keywords
        ):
            self.reject("LOOP_RANGE", statement.iter, "repeat syntax is for name in range(N)")
        bound = self._literal_nonnegative_int(statement.iter.args[0], "loop bound")
        if bound > self.context.max_loop_bound:
            self.reject(
                "LOOP_BOUND",
                statement.iter.args[0],
                "loop bound exceeds the trusted sidecar maximum",
            )
        if any(
            isinstance(node, ast.Name)
            and isinstance(node.ctx, ast.Load)
            and node.id == statement.target.id
            for body_statement in statement.body
            for node in ast.walk(body_statement)
        ):
            self.reject(
                "LOOP_VARIABLE_USE",
                statement,
                "the repeat index cannot affect actions or predicates",
            )

        loop_id = f"loop_{self.counter + 1:04d}"
        head_id = self.new_id("loop_head")
        latch_id = self.add(
            LoopLatchNode(
                node_id=self.new_id("loop_latch"),
                source=_span(self.filename, statement),
                loop_id=loop_id,
                head_id=head_id,
            )
        )
        body_id = self.compile_block(
            statement.body,
            latch_id,
            branch_arm=branch_arm,
            control_depth=control_depth + 1,
        )
        self.add(
            LoopHeadNode(
                node_id=head_id,
                source=_span(self.filename, statement),
                loop_id=loop_id,
                bound=bound,
                body_id=body_id,
                exit_id=continuation,
            )
        )
        return head_id

    def _compile_parallel(
        self,
        statement: ast.Expr,
        call: ast.Call,
        continuation: str,
    ) -> str:
        values = self._bind_call(call, ("left", "right"))
        left_name = self._function_reference(values["left"])
        right_name = self._function_reference(values["right"])
        if left_name == right_name:
            self.reject("PAR_BRANCHES", statement, "parallel branches must be distinct")
        for name in (left_name, right_name):
            if name == "task" or name not in self.functions:
                self.reject("PAR_BRANCH_REFERENCE", statement, f"unknown branch function {name!r}")
            if name in self.used_branch_functions:
                self.reject(
                    "PAR_BRANCH_REUSE",
                    statement,
                    f"branch function {name!r} may be referenced only once",
                )
        self.used_branch_functions.update({left_name, right_name})
        self.parallel_counter += 1
        par_id = f"par_{self.parallel_counter:04d}"
        join_id = self.add(
            JoinNode(
                node_id=self.new_id("join"),
                source=_span(self.filename, statement),
                par_id=par_id,
                next_id=continuation,
            )
        )
        left_function = self.functions[left_name]
        right_function = self.functions[right_name]
        left_barriers = self._barrier_sequence(left_function)
        right_barriers = self._barrier_sequence(right_function)
        if left_barriers != right_barriers:
            self.reject(
                "BARRIER_SEQUENCE",
                statement,
                "left and right branches require the same ordered barrier sequence",
            )
        left_id = self.compile_block(
            _strip_docstring(left_function.body),
            join_id,
            branch_arm=Arm.LEFT,
            control_depth=0,
        )
        right_id = self.compile_block(
            _strip_docstring(right_function.body),
            join_id,
            branch_arm=Arm.RIGHT,
            control_depth=0,
        )
        if left_id == join_id:
            left_id = self.add(
                NopNode(
                    node_id=self.new_id("left_nop"),
                    source=_span(self.filename, left_function),
                    next_id=join_id,
                )
            )
        if right_id == join_id:
            right_id = self.add(
                NopNode(
                    node_id=self.new_id("right_nop"),
                    source=_span(self.filename, right_function),
                    next_id=join_id,
                )
            )
        return self.add(
            ForkNode(
                node_id=self.new_id("fork"),
                source=_span(self.filename, statement),
                par_id=par_id,
                left_entry=left_id,
                right_entry=right_id,
                join_id=join_id,
            )
        )

    def _barrier_sequence(self, function: ast.FunctionDef) -> tuple[str, ...]:
        sequence: list[str] = []
        for statement in _strip_docstring(function.body):
            is_top_level_barrier = (
                isinstance(statement, ast.Expr)
                and isinstance(statement.value, ast.Call)
                and isinstance(statement.value.func, ast.Name)
                and statement.value.func.id == "barrier"
            )
            if not is_top_level_barrier:
                nested_barrier = next(
                    (
                        node
                        for node in ast.walk(statement)
                        if isinstance(node, ast.Call)
                        and isinstance(node.func, ast.Name)
                        and node.func.id == "barrier"
                    ),
                    None,
                )
                if nested_barrier is not None:
                    self.reject(
                        "CONDITIONAL_BARRIER",
                        nested_barrier,
                        "barrier must be an unconditional top-level branch statement",
                    )
                continue
            values = self._bind_call(statement.value, ("barrier_id",))
            sequence.append(self._literal_str(values["barrier_id"], "barrier_id"))
        return tuple(sequence)

    def _predicate(self, expression: ast.expr) -> PredicateSpec:
        if isinstance(expression, ast.Name):
            self._require_input(expression.id, expression)
            return PredicateSpec(expression.id, PredicateOp.IS_TRUE)
        if (
            isinstance(expression, ast.UnaryOp)
            and isinstance(expression.op, ast.Not)
            and isinstance(expression.operand, ast.Name)
        ):
            self._require_input(expression.operand.id, expression.operand)
            return PredicateSpec(expression.operand.id, PredicateOp.IS_FALSE)
        if (
            isinstance(expression, ast.Compare)
            and isinstance(expression.left, ast.Name)
            and len(expression.ops) == 1
            and isinstance(expression.ops[0], (ast.Eq, ast.NotEq))
            and len(expression.comparators) == 1
        ):
            name = expression.left.id
            self._require_input(name, expression.left)
            value = self._literal(expression.comparators[0], "predicate comparison")
            return PredicateSpec(
                name,
                PredicateOp.EQUALS if isinstance(expression.ops[0], ast.Eq) else PredicateOp.NOT_EQUALS,
                value,
            )
        self.reject(
            "PREDICATE",
            expression,
            "predicate must be input, not input, input == literal, or input != literal",
        )
        raise AssertionError("unreachable")

    def _require_input(self, name: str, node: ast.AST) -> None:
        if name not in self.input_domains:
            self.reject("UNKNOWN_INPUT", node, f"finite input {name!r} is not declared")

    def _bind_call(self, call: ast.Call, parameters: tuple[str, ...]) -> dict[str, ast.expr]:
        if any(isinstance(argument, ast.Starred) for argument in call.args):
            self.reject("CALL_ARGUMENTS", call, "starred arguments are unsupported")
        if len(call.args) > len(parameters):
            self.reject("CALL_ARGUMENTS", call, "too many positional arguments")
        values = {name: value for name, value in zip(parameters, call.args)}
        for keyword in call.keywords:
            if keyword.arg is None or keyword.arg not in parameters:
                self.reject("CALL_ARGUMENTS", keyword, "unknown or expanded keyword argument")
            if keyword.arg in values:
                self.reject("CALL_ARGUMENTS", keyword, f"duplicate argument {keyword.arg!r}")
            values[keyword.arg] = keyword.value
        missing = [name for name in parameters if name not in values]
        if missing:
            self.reject("CALL_ARGUMENTS", call, f"missing arguments: {', '.join(missing)}")
        return values

    def _call_name(self, call: ast.Call) -> str:
        if not isinstance(call.func, ast.Name):
            self.reject("CALL_TARGET", call.func, "attribute and dynamic call targets are unsupported")
        return call.func.id

    def _function_reference(self, node: ast.expr) -> str:
        if not isinstance(node, ast.Name):
            self.reject("PAR_BRANCH_REFERENCE", node, "parallel arguments must be function names")
        return node.id

    def _literal(self, node: ast.expr, label: str) -> LiteralValue:
        if not isinstance(node, ast.Constant) or type(node.value) not in {bool, int, str}:
            self.reject("LITERAL", node, f"{label} must be a bool, int, or string literal")
        return node.value

    def _literal_str(self, node: ast.expr, label: str) -> str:
        value = self._literal(node, label)
        if not isinstance(value, str):
            self.reject("LITERAL_TYPE", node, f"{label} must be a string literal")
        return value

    def _literal_positive_int(self, node: ast.expr, label: str) -> int:
        value = self._literal(node, label)
        if isinstance(value, bool) or not isinstance(value, int) or value <= 0:
            self.reject("LITERAL_TYPE", node, f"{label} must be a positive integer literal")
        return value

    def _literal_nonnegative_int(self, node: ast.expr, label: str) -> int:
        value = self._literal(node, label)
        if isinstance(value, bool) or not isinstance(value, int) or value < 0:
            self.reject("LITERAL_TYPE", node, f"{label} must be a non-negative integer literal")
        return value

    def _arm(self, node: ast.expr, label: str) -> Arm:
        value = self._literal_str(node, label)
        try:
            return Arm(value)
        except ValueError:
            self.reject("ARM", node, f"{label} must be 'left' or 'right'")
        raise AssertionError("unreachable")


def parse_restricted_python(
    source: str,
    *,
    filename: str,
    context: ParserContext,
) -> TimedProgram:
    """Parse source text without executing it and return validated Timed IR."""
    if not isinstance(source, str):
        raise TypeError("source must be text")
    if not filename:
        filename = "<program>"
    return _Compiler(source, str(Path(filename)), context).parse_module()
