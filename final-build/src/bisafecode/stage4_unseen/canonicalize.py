"""Structural canonicalization for Stage 4 program isolation.

PREP_ONLY_NOT_FORMAL_STAGE4_EVIDENCE

The canonicalizer never executes analyzed source.  It produces a strict
alpha-normalized AST identity and more conservative leakage signatures.  The
leakage forms intentionally over-reject near-canonical programs; they are not
program-equivalence proofs.
"""

from __future__ import annotations

import ast
import hashlib
import json
from dataclasses import dataclass
from typing import Any, Dict, Iterable, List, Mapping, Optional, Sequence, Tuple

from . import PREP_STATUS


ACTION_SIGNATURES: Mapping[str, Tuple[str, ...]] = {
    "move": ("arm", "trajectory_hash"),
    "wait": ("arm", "duration_ns"),
    "close": ("arm", "object_id", "duration_ns"),
    "open": ("arm", "object_id", "duration_ns"),
    "acquire": ("arm", "resource_id"),
    "release": ("arm", "resource_id"),
    "transfer_authority": ("object_id", "sender", "receiver"),
    "barrier": ("barrier_id",),
}

ENTITY_ROLES = {
    "object_id": "object",
    "resource_id": "resource",
    "trajectory_hash": "trajectory",
    "barrier_id": "barrier",
}


class CanonicalizationError(ValueError):
    """Raised when source is not representable by the frozen prep grammar."""


@dataclass(frozen=True)
class CanonicalizationResult:
    evidence_status: str
    source_sha256: str
    canonical_ast_hash: str
    structural_family_signature_hash: str
    time_shift_structural_signature_hash: Optional[str]
    canonical_document: Mapping[str, Any]
    structural_family_document: Mapping[str, Any]
    action_tokens: Tuple[str, ...]
    structure: Mapping[str, Any]


def _sha_document(value: Mapping[str, Any]) -> str:
    payload = json.dumps(
        value, sort_keys=True, separators=(",", ":"), ensure_ascii=True
    ).encode("ascii")
    return hashlib.sha256(payload).hexdigest()


def _serialized(value: Any) -> str:
    return json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=True)


def _strip_docstring_and_pass(statements: Sequence[ast.stmt]) -> List[ast.stmt]:
    result = list(statements)
    if (
        result
        and isinstance(result[0], ast.Expr)
        and isinstance(result[0].value, ast.Constant)
        and isinstance(result[0].value.value, str)
    ):
        result = result[1:]
    return [statement for statement in result if not isinstance(statement, ast.Pass)]


def _call_name(call: ast.Call) -> str:
    if not isinstance(call.func, ast.Name):
        raise CanonicalizationError("only direct fixed-API calls are supported")
    return call.func.id


def _literal(node: ast.AST) -> Any:
    if isinstance(node, ast.Constant) and isinstance(
        node.value, (bool, int, str)
    ):
        return node.value
    raise CanonicalizationError("only bool, int, and str literals are supported")


def _bind_call(call: ast.Call, signature: Sequence[str]) -> Dict[str, ast.AST]:
    if len(call.args) > len(signature):
        raise CanonicalizationError("too many positional arguments")
    values: Dict[str, ast.AST] = {
        name: node for name, node in zip(signature, call.args)
    }
    for keyword in call.keywords:
        if keyword.arg is None or keyword.arg not in signature:
            raise CanonicalizationError("unsupported keyword argument")
        if keyword.arg in values:
            raise CanonicalizationError("duplicate call argument")
        values[keyword.arg] = keyword.value
    if set(values) != set(signature):
        raise CanonicalizationError("missing fixed-API call argument")
    return values


class _Canonicalizer:
    def __init__(self, source: str, *, aggressive: bool, arm_swap: bool):
        try:
            module = ast.parse(source, filename="program.py", mode="exec")
        except SyntaxError as error:
            raise CanonicalizationError(str(error)) from error
        self.aggressive = aggressive
        self.arm_swap = arm_swap
        self.functions: Dict[str, ast.FunctionDef] = {}
        self.entity_maps: Dict[str, Dict[str, str]] = {
            role: {} for role in ("object", "resource", "trajectory", "barrier")
        }
        self.input_names: Dict[str, str] = {}
        self.loop_names: Dict[str, str] = {}
        for statement in _strip_docstring_and_pass(module.body):
            if not isinstance(statement, ast.FunctionDef):
                raise CanonicalizationError("top level must contain only functions")
            if statement.name in self.functions:
                raise CanonicalizationError("duplicate function")
            self.functions[statement.name] = statement
        if "task" not in self.functions:
            raise CanonicalizationError("task() is required")

    def document(self) -> Mapping[str, Any]:
        return {
            "schema": "bisafecode.stage4.unseen.canonical-ast/v2",
            "task": self._block(self.functions["task"].body),
        }

    def _identifier(self, role: str, value: str) -> str:
        mapping = self.entity_maps[role]
        if value not in mapping:
            mapping[value] = "${}{}".format(role, len(mapping))
        return mapping[value]

    def _input(self, value: str) -> str:
        if value not in self.input_names:
            self.input_names[value] = "$input{}".format(len(self.input_names))
        return self.input_names[value]

    def _arm(self, value: Any) -> Any:
        if value not in ("left", "right"):
            return value
        if not self.arm_swap:
            return value
        return "right" if value == "left" else "left"

    def _argument(self, role: str, node: ast.AST) -> Any:
        value = _literal(node)
        if role in ("arm", "sender", "receiver"):
            return self._arm(value)
        if role == "trajectory_hash":
            if not isinstance(value, str):
                raise CanonicalizationError("trajectory hash must be a string")
            if not self.aggressive:
                return value
            return self._identifier("trajectory", value)
        if role in ENTITY_ROLES:
            if not isinstance(value, str):
                raise CanonicalizationError("entity identifiers must be strings")
            return self._identifier(ENTITY_ROLES[role], value)
        if role == "duration_ns":
            if not isinstance(value, int) or isinstance(value, bool):
                raise CanonicalizationError("duration must be an integer")
            return "$number" if self.aggressive else value
        return value

    def _predicate(self, node: ast.expr) -> Mapping[str, Any]:
        if isinstance(node, ast.Name):
            return {"op": "is_true", "input": self._input(node.id)}
        if (
            isinstance(node, ast.UnaryOp)
            and isinstance(node.op, ast.Not)
            and isinstance(node.operand, ast.Name)
        ):
            return {"op": "is_false", "input": self._input(node.operand.id)}
        if (
            isinstance(node, ast.Compare)
            and isinstance(node.left, ast.Name)
            and len(node.ops) == 1
            and len(node.comparators) == 1
            and isinstance(node.ops[0], (ast.Eq, ast.NotEq))
        ):
            value = _literal(node.comparators[0])
            if self.aggressive and isinstance(value, (int, str)) and not isinstance(value, bool):
                value = "$constant"
            return {
                "op": "equals" if isinstance(node.ops[0], ast.Eq) else "not_equals",
                "input": self._input(node.left.id),
                "value": value,
            }
        raise CanonicalizationError("predicate is outside the restricted grammar")

    def _function_reference(self, node: ast.AST) -> str:
        if not isinstance(node, ast.Name) or node.id not in self.functions:
            raise CanonicalizationError("parallel branch must name a helper function")
        return node.id

    def _action(self, call: ast.Call, name: str) -> Mapping[str, Any]:
        signature = ACTION_SIGNATURES[name]
        bound = _bind_call(call, signature)
        arguments = [
            {"name": role, "value": self._argument(role, bound[role])}
            for role in signature
        ]
        touches = []
        for item in arguments:
            role = item["name"]
            value = item["value"]
            if role in ("arm", "sender", "receiver"):
                touches.append("arm:{}".format(value))
            elif role in ENTITY_ROLES:
                touches.append("{}:{}".format(ENTITY_ROLES[role], value))
        return {
            "kind": "action",
            "op": name,
            "args": arguments,
            "_touches": sorted(set(touches)),
        }

    def _statement(self, statement: ast.stmt) -> Optional[Mapping[str, Any]]:
        if isinstance(statement, ast.Pass):
            return None
        if isinstance(statement, ast.Expr) and isinstance(statement.value, ast.Call):
            name = _call_name(statement.value)
            if name == "parallel":
                bound = _bind_call(statement.value, ("left", "right"))
                left_name = self._function_reference(bound["left"])
                right_name = self._function_reference(bound["right"])
                if self.arm_swap:
                    left_name, right_name = right_name, left_name
                return {
                    "kind": "parallel",
                    "left": self._block(self.functions[left_name].body),
                    "right": self._block(self.functions[right_name].body),
                }
            if name not in ACTION_SIGNATURES:
                raise CanonicalizationError("unsupported fixed-API call: {}".format(name))
            return self._action(statement.value, name)
        if isinstance(statement, ast.If):
            if not statement.orelse:
                raise CanonicalizationError("if requires else")
            true_body = self._block(statement.body)
            false_body = self._block(statement.orelse)
            if self.aggressive and true_body == false_body:
                return {"kind": "block", "body": true_body}
            return {
                "kind": "if",
                "predicate": self._predicate(statement.test),
                "true": true_body,
                "false": false_body,
            }
        if isinstance(statement, ast.For):
            if not isinstance(statement.target, ast.Name):
                raise CanonicalizationError("loop target must be a name")
            if not (
                isinstance(statement.iter, ast.Call)
                and _call_name(statement.iter) == "range"
                and len(statement.iter.args) == 1
                and not statement.iter.keywords
            ):
                raise CanonicalizationError("loop must use range(N)")
            bound = _literal(statement.iter.args[0])
            if not isinstance(bound, int) or isinstance(bound, bool) or bound < 0:
                raise CanonicalizationError("loop bound must be a nonnegative integer")
            if bound == 0 and self.aggressive:
                return None
            body = self._block(statement.body)
            if bound == 1 and self.aggressive:
                return {"kind": "block", "body": body}
            target = statement.target.id
            if target not in self.loop_names:
                self.loop_names[target] = "$loop{}".format(len(self.loop_names))
            return {
                "kind": "loop",
                "target": self.loop_names[target],
                "bound": "$number" if self.aggressive else bound,
                "body": body,
            }
        raise CanonicalizationError(
            "unsupported statement: {}".format(type(statement).__name__)
        )

    @staticmethod
    def _independent(left: Mapping[str, Any], right: Mapping[str, Any]) -> bool:
        return set(left.get("_touches", ())).isdisjoint(right.get("_touches", ()))

    def _canonical_action_segment(
        self, segment: Sequence[Mapping[str, Any]]
    ) -> List[Mapping[str, Any]]:
        if not self.aggressive or len(segment) < 2:
            return list(segment)
        predecessors: Dict[int, set] = {index: set() for index in range(len(segment))}
        for later in range(len(segment)):
            for earlier in range(later):
                if not self._independent(segment[earlier], segment[later]):
                    predecessors[later].add(earlier)
        remaining = set(range(len(segment)))
        ordered: List[Mapping[str, Any]] = []
        while remaining:
            ready = [
                index
                for index in remaining
                if not (predecessors[index] & remaining)
            ]
            chosen = min(ready, key=lambda index: _serialized(segment[index]))
            ordered.append(segment[chosen])
            remaining.remove(chosen)
        return ordered

    def _block(self, statements: Sequence[ast.stmt]) -> List[Mapping[str, Any]]:
        expanded: List[Mapping[str, Any]] = []
        for statement in _strip_docstring_and_pass(statements):
            value = self._statement(statement)
            if value is None:
                continue
            if value.get("kind") == "block":
                expanded.extend(value["body"])
            else:
                expanded.append(value)
        result: List[Mapping[str, Any]] = []
        segment: List[Mapping[str, Any]] = []
        for value in expanded:
            if value.get("kind") == "action" and value.get("op") != "barrier":
                segment.append(value)
                continue
            if segment:
                result.extend(self._canonical_action_segment(segment))
                segment = []
            # A barrier is a synchronization cut point.  It must never enter an
            # independently reorderable action segment.
            result.append(value)
        if segment:
            result.extend(self._canonical_action_segment(segment))
        return result


def _clean_private(value: Any) -> Any:
    if isinstance(value, list):
        return [_clean_private(item) for item in value]
    if isinstance(value, dict):
        return {
            key: _clean_private(item)
            for key, item in value.items()
            if not key.startswith("_")
        }
    return value


def _without_waits(value: Any) -> Any:
    if isinstance(value, list):
        return [
            transformed
            for item in value
            for transformed in [_without_waits(item)]
            if transformed is not None
        ]
    if not isinstance(value, dict):
        return value
    if value.get("kind") == "action" and value.get("op") == "wait":
        return None
    result = {key: _without_waits(item) for key, item in value.items()}
    for key in ("task", "left", "right", "true", "false", "body"):
        if key in result and result[key] is None:
            result[key] = []
    return result


def _tokens(value: Any) -> Iterable[str]:
    if isinstance(value, list):
        for item in value:
            yield from _tokens(item)
        return
    if not isinstance(value, dict):
        return
    kind = value.get("kind")
    if kind == "action":
        parts = ["call", str(value.get("op"))]
        for argument in value.get("args", []):
            if argument["name"] == "duration_ns":
                continue
            parts.append(str(argument["value"]))
        yield ":".join(parts)
        return
    if kind:
        yield "enter:{}".format(kind)
    for key in ("task", "left", "right", "true", "false", "body"):
        if key in value:
            yield from _tokens(value[key])
    if kind:
        yield "exit:{}".format(kind)


def _structure(value: Any) -> Mapping[str, Any]:
    counts = {
        "source_action_count": 0,
        "if_count": 0,
        "loop_count": 0,
        "parallel_count": 0,
        "barrier_count": 0,
        "max_control_depth": 0,
        "max_concurrency_width": 1,
    }
    actions: Dict[str, int] = {}

    def visit(node: Any, depth: int) -> None:
        if isinstance(node, list):
            for child in node:
                visit(child, depth)
            return
        if not isinstance(node, dict):
            return
        kind = node.get("kind")
        if kind == "action":
            counts["source_action_count"] += 1
            op = str(node.get("op"))
            actions[op] = actions.get(op, 0) + 1
            if op == "barrier":
                counts["barrier_count"] += 1
            counts["max_control_depth"] = max(counts["max_control_depth"], depth)
            return
        if kind == "if":
            counts["if_count"] += 1
            depth += 1
        elif kind == "loop":
            counts["loop_count"] += 1
            depth += 1
        elif kind == "parallel":
            counts["parallel_count"] += 1
            counts["max_concurrency_width"] = 2
            depth += 1
        counts["max_control_depth"] = max(counts["max_control_depth"], depth)
        for key in ("task", "left", "right", "true", "false", "body"):
            if key in node:
                visit(node[key], depth)

    visit(value, 0)
    counts["action_kind_counts"] = dict(sorted(actions.items()))
    return counts


def canonicalize_source(source: str) -> CanonicalizationResult:
    """Return strict content identity and a separate structural family signature."""

    strict = _clean_private(
        _Canonicalizer(source, aggressive=False, arm_swap=False).document()
    )
    aggressive_candidates = [
        _clean_private(
            _Canonicalizer(source, aggressive=True, arm_swap=swap).document()
        )
        for swap in (False, True)
    ]
    structural_family = min(aggressive_candidates, key=_serialized)
    time_candidates = [_without_waits(value) for value in aggressive_candidates]
    without_waits = min(time_candidates, key=_serialized)
    time_hash: Optional[str] = None
    if any(token.startswith("call:") for token in _tokens(without_waits)):
        time_hash = _sha_document(without_waits)
    return CanonicalizationResult(
        evidence_status=PREP_STATUS,
        source_sha256=hashlib.sha256(source.encode("utf-8")).hexdigest(),
        canonical_ast_hash=_sha_document(strict),
        structural_family_signature_hash=_sha_document(structural_family),
        time_shift_structural_signature_hash=time_hash,
        canonical_document=strict,
        structural_family_document=structural_family,
        action_tokens=tuple(_tokens(structural_family)),
        structure=_structure(strict),
    )


def token_edit_distance(left: Sequence[str], right: Sequence[str]) -> int:
    """Deterministic Levenshtein distance used only as a leakage screen."""

    previous = list(range(len(right) + 1))
    for left_index, left_value in enumerate(left, 1):
        current = [left_index]
        for right_index, right_value in enumerate(right, 1):
            current.append(
                min(
                    current[-1] + 1,
                    previous[right_index] + 1,
                    previous[right_index - 1] + (left_value != right_value),
                )
            )
        previous = current
    return previous[-1]
