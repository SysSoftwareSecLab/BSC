"""Executable full method and three frozen EXP-S4-002 baselines.

FREEZE_ONLY_NOT_FORMAL_STAGE4_EVIDENCE

The functions are inert until called by the approval-gated pipeline.  Unit
tests use only unscheduled synthetic sources and a local fake LLM invoker.
"""

from __future__ import annotations

import ast
import hashlib
from pathlib import Path
from typing import Any, Mapping, Tuple

from bisafecode.explicit_state import SearchBounds
from bisafecode.model_checking import BoundedExplicitStateModelChecker, PropertyCheckerBinding, callable_implementation_sha256

from . import FORMAL_RAW_STATUS
from .generator import TRAJECTORIES
from .identity import compute_asset_identity
from .isolation import run_isolated_job
from .property_adapters import build_full_method_inputs, controlled_interval_property, controlled_point_property
from bisafecode.stage4_unseen.time_bounds import source_time_upper_bound_ns


COMMON_BUDGET: Mapping[str, int] = {
    "wall_timeout_seconds": 120,
    "max_resident_memory_bytes": 4_294_967_296,
    "max_states": 100_000,
    "max_transitions": 500_000,
    "max_model_time_ns": 5_000_000_000,
}

METHODS: Tuple[Mapping[str, Any], ...] = (
    {"method_id": "bisafecode_full", "role": "full_method", "implementation": "S3-002 frozen parser + Timed IR + explicit-state BMC + Stage-4 property adapters", "budget": COMMON_BUDGET},
    {"method_id": "non_timed_action_boundary", "role": "baseline", "implementation": "AST action-boundary abstraction without durations/shared timeline", "budget": COMMON_BUDGET},
    {"method_id": "random_dynamic_testing", "role": "baseline", "implementation": "100 deterministic SHA-256-selected finite input/action-prefix draws", "draws_per_program": 100, "seed_rule": "first 64 bits of SHA-256('EXP-S4-002:random-dynamic:' + program_id)", "budget": COMMON_BUDGET},
    {"method_id": "llm_as_judge", "role": "baseline", "implementation": "five fresh isolated ChatGPT-authenticated codex exec judgments parsed and aggregated by exact policy", "judgments_per_program": 5, "reasoning_effort": "high", "model_id": "gpt-5.5", "paper_label": "Codex-CLI-mediated GPT-5.5 LLM-as-judge baseline", "reproducibility_wording": "model-family-pinned and protocol-reproducible", "current_codex_conversation_allowed": False, "budget": COMMON_BUDGET},
)


def method_ids() -> Tuple[str, ...]:
    return tuple(str(item["method_id"]) for item in METHODS)


def assert_equal_common_budgets() -> None:
    if method_ids() != ("bisafecode_full", "non_timed_action_boundary", "random_dynamic_testing", "llm_as_judge"):
        raise ValueError("full method plus three authoritative baselines are required")
    if any(item["budget"] != COMMON_BUDGET for item in METHODS):
        raise ValueError("all methods must receive the identical common budget")


def run_full_method(source: str, *, program_id: str) -> Mapping[str, Any]:
    program, environment = build_full_method_inputs(source, program_id=program_id)
    asset_root = compute_asset_identity()["root_sha256"]
    point_binding = PropertyCheckerBinding(
        name="stage4-controlled-point-property/v1",
        implementation_sha256=callable_implementation_sha256(controlled_point_property),
        evidence_sha256=(asset_root,),
        input_contract="exact SearchState plus frozen controlled asset root",
        deterministic=True,
        side_effect_free=True,
    )
    interval_binding = PropertyCheckerBinding(
        name="stage4-controlled-interval-property/v1",
        implementation_sha256=callable_implementation_sha256(controlled_interval_property),
        evidence_sha256=(asset_root,),
        input_contract="SearchState, interval, active actions, frozen controlled asset root",
        deterministic=True,
        side_effect_free=True,
    )
    result = BoundedExplicitStateModelChecker(
        program,
        environment,
        interval_checker=controlled_interval_property,
        point_checker=controlled_point_property,
        interval_checker_binding=interval_binding,
        point_checker_binding=point_binding,
    ).run(
        SearchBounds(
            max_states=COMMON_BUDGET["max_states"],
            max_transitions=COMMON_BUDGET["max_transitions"],
            max_time_ns=COMMON_BUDGET["max_model_time_ns"],
            wall_timeout_s=COMMON_BUDGET["wall_timeout_seconds"],
        )
    )
    report = result.report
    return {
        "verdict": report.verdict.value,
        "reason_codes": list(report.reasons),
        "states": report.explored_states,
        "transitions": report.generated_transitions,
        "model_time_ns": source_time_upper_bound_ns(
            source,
            {
                value["sha256"]: int(value["duration_ns"])
                for value in TRAJECTORIES.values()
            },
        ),
        "artifact": result.search_artifact,
    }


def _calls(source: str) -> Tuple[ast.Call, ...]:
    module = ast.parse(source, filename="program.py", mode="exec")
    return tuple(
        sorted(
            (node for node in ast.walk(module) if isinstance(node, ast.Call) and isinstance(node.func, ast.Name)),
            key=lambda node: (node.lineno, node.col_offset),
        )
    )


def _literal(node: ast.AST) -> Any:
    if isinstance(node, ast.Constant) and type(node.value) in {str, int, bool}:
        return node.value
    raise ValueError("baseline requires literal arguments")


def _sampled_action_path(
    source: str, *, mode: str, ready: bool, reverse_parallel: bool
) -> Tuple[ast.Call, ...]:
    module = ast.parse(source, filename="program.py", mode="exec")
    functions = {
        item.name: item
        for item in module.body
        if isinstance(item, ast.FunctionDef)
    }
    if "task" not in functions:
        raise ValueError("baseline task function missing")
    inputs = {"mode": mode, "ready": ready}

    def predicate(node: ast.AST) -> bool:
        if isinstance(node, ast.Name) and node.id in inputs:
            return bool(inputs[node.id])
        if isinstance(node, ast.UnaryOp) and isinstance(node.op, ast.Not) and isinstance(node.operand, ast.Name):
            return not bool(inputs[node.operand.id])
        if isinstance(node, ast.Compare) and isinstance(node.left, ast.Name) and len(node.ops) == 1 and len(node.comparators) == 1:
            equal = inputs[node.left.id] == _literal(node.comparators[0])
            return equal if isinstance(node.ops[0], ast.Eq) else not equal
        raise ValueError("unsupported baseline predicate")

    def block(statements) -> list[ast.Call]:
        observed = []
        for statement in statements:
            if isinstance(statement, ast.Pass):
                continue
            if isinstance(statement, ast.If):
                observed.extend(block(statement.body if predicate(statement.test) else statement.orelse))
                continue
            if isinstance(statement, ast.For):
                count = _literal(statement.iter.args[0])
                for _ in range(int(count)):
                    observed.extend(block(statement.body))
                continue
            if not isinstance(statement, ast.Expr) or not isinstance(statement.value, ast.Call) or not isinstance(statement.value.func, ast.Name):
                raise ValueError("unsupported baseline statement")
            call = statement.value
            name = call.func.id
            if name == "barrier":
                continue
            if name == "parallel":
                lane_names = [node.id for node in call.args if isinstance(node, ast.Name)]
                if len(lane_names) != 2:
                    raise ValueError("invalid baseline parallel")
                if reverse_parallel:
                    lane_names.reverse()
                for lane_name in lane_names:
                    observed.extend(block(functions[lane_name].body))
                continue
            observed.append(call)
        return observed

    return tuple(block(functions["task"].body))


def _evaluate_boundary_calls(calls: Tuple[ast.Call, ...]) -> Mapping[str, Any]:
    resources = {}
    grasps = set()
    unknown = False
    transitions = 0
    for call in calls:
        name = call.func.id
        if name not in {"acquire", "release", "close", "open", "transfer_authority", "move", "wait", "parallel", "barrier", "range"}:
            continue
        if name in {"parallel", "barrier", "range"}:
            continue
        transitions += 1
        args = [_literal(value) for value in call.args]
        if name == "acquire":
            arm, resource = args
            if resource in resources and resources[resource] != arm:
                return {"verdict": "violated", "reason_codes": ["BOUNDARY_RESOURCE_CONFLICT"], "states": transitions + 1, "transitions": transitions}
            resources[resource] = arm
        elif name == "release":
            arm, resource = args
            if resources.get(resource) != arm:
                return {"verdict": "violated", "reason_codes": ["BOUNDARY_RELEASE_NONOWNER"], "states": transitions + 1, "transitions": transitions}
            resources.pop(resource)
        elif name == "close":
            arm, object_id, _duration = args
            if (arm, object_id) in grasps:
                return {"verdict": "violated", "reason_codes": ["BOUNDARY_OBJECT_DOUBLE_CLOSE"], "states": transitions + 1, "transitions": transitions}
            grasps.add((arm, object_id))
        elif name == "open":
            arm, object_id, _duration = args
            if (arm, object_id) not in grasps:
                return {"verdict": "violated", "reason_codes": ["BOUNDARY_OPEN_NONOWNER"], "states": transitions + 1, "transitions": transitions}
            grasps.discard((arm, object_id))
        elif name in {"move", "transfer_authority"}:
            unknown = True
    return {
        "verdict": "unknown" if unknown else "verified-within-bounds",
        "reason_codes": ["TIMING_OR_GEOMETRY_ERASED"] if unknown else [],
        "states": transitions + 1,
        "transitions": transitions,
    }


def run_non_timed_baseline(source: str, *, program_id: str) -> Mapping[str, Any]:
    results = [
        _evaluate_boundary_calls(
            _sampled_action_path(
                source, mode=mode, ready=ready, reverse_parallel=reverse
            )
        )
        for mode in ("fast", "safe")
        for ready in (False, True)
        for reverse in (False, True)
    ]
    if any(item["verdict"] == "violated" for item in results):
        verdict, reasons = "violated", ["ACTION_BOUNDARY_VIOLATION"]
    elif any(item["verdict"] != "verified-within-bounds" for item in results):
        verdict, reasons = "unknown", ["TIMING_OR_GEOMETRY_ERASED"]
    else:
        verdict, reasons = "verified-within-bounds", []
    return {
        "verdict": verdict,
        "reason_codes": reasons,
        "states": sum(int(item["states"]) for item in results),
        "transitions": sum(int(item["transitions"]) for item in results),
        "model_time_ns": 0,
        "finite_paths": 8,
    }


def run_random_dynamic_testing(source: str, *, program_id: str) -> Mapping[str, Any]:
    violations = 0
    unknown = 0
    draw_records = []
    for draw in range(100):
        digest = hashlib.sha256(f"EXP-S4-002:random-dynamic:{program_id}:{draw}".encode("utf-8")).digest()
        mode = ("fast", "safe")[digest[0] % 2]
        ready = bool(digest[1] % 2)
        reverse = bool(digest[2] % 2)
        path = _sampled_action_path(source, mode=mode, ready=ready, reverse_parallel=reverse)
        prefix = 0 if not path else 1 + int.from_bytes(digest[3:11], "big") % len(path)
        result = _evaluate_boundary_calls(path[:prefix])
        if result["verdict"] == "violated":
            violations += 1
        elif result["verdict"] != "verified-within-bounds":
            unknown += 1
        draw_records.append({"draw": draw, "mode": mode, "ready": ready, "reverse_parallel": reverse, "prefix_actions": prefix, "verdict": result["verdict"]})
    verdict = "violated" if violations else ("unknown" if unknown else "verified-within-bounds")
    return {
        "verdict": verdict,
        "reason_codes": (["RANDOM_VIOLATION_OBSERVED"] if violations else (["RANDOM_DRAWS_INCONCLUSIVE"] if unknown else [])),
        "states": 100,
        "transitions": sum(item["prefix_actions"] for item in draw_records),
        "model_time_ns": 0,
        "draws": draw_records,
    }


def run_method_in_process(
    method_id: str,
    source: str,
    *,
    program_id: str,
    llm_binding: Mapping[str, Any] | None = None,
    total_timeout_seconds: float = 120.0,
) -> Mapping[str, Any]:
    """Worker-side method dispatch; never called by the formal parent inline."""

    if method_id == "bisafecode_full":
        return run_full_method(source, program_id=program_id)
    if method_id == "non_timed_action_boundary":
        return run_non_timed_baseline(source, program_id=program_id)
    if method_id == "random_dynamic_testing":
        return run_random_dynamic_testing(source, program_id=program_id)
    if method_id == "llm_as_judge":
        if llm_binding is None:
            raise ValueError("exact LLM binding is required")
        if llm_binding.get("provider_adapter_id") != "codex_cli_jsonl_v1":
            raise ValueError("formal LLM baseline requires the Codex CLI adapter")
        from .codex_cli_adapter import run_codex_cli_judge

        return run_codex_cli_judge(
            source,
            program_id=program_id,
            binding=llm_binding,
            total_timeout_seconds=total_timeout_seconds,
        )
    raise ValueError("unknown method")


def execute_method(
    method_id: str,
    source: str,
    *,
    program_id: str,
    stdout_path: Path,
    stderr_path: Path,
    repository_root: Path,
    budget: Mapping[str, int] | None = None,
    llm_binding: Mapping[str, Any] | None = None,
) -> Mapping[str, Any]:
    """Run one program×method in a hard-limited subprocess."""

    effective_budget = dict(COMMON_BUDGET if budget is None else budget)
    isolated = run_isolated_job(
        "method",
        {
            "method_id": method_id,
            "source": source,
            "program_id": program_id,
            "llm_binding": llm_binding,
        },
        stdout_path=stdout_path,
        stderr_path=stderr_path,
        budget=effective_budget,
        repository_root=repository_root,
    )
    outcome = isolated["outcome"] if isinstance(isolated.get("outcome"), dict) else None
    status = isolated["status"]
    verdict = outcome.get("verdict") if status == "ok" and outcome else None
    reasons = list(isolated["reason_codes"])
    if status == "ok" and outcome:
        reasons = list(outcome.get("reason_codes", ()))
        if outcome.get("execution_status") == "timeout" or "wall-time-bound-exhausted" in reasons:
            status, verdict = "timeout", None
        elif outcome.get("execution_status") in {
            "cli_or_service_error",
            "quota_window_interruption",
        }:
            status, verdict = str(outcome["execution_status"]), None
        elif any(
            reason in reasons
            for reason in (
                "state-bound-exhausted",
                "transition-bound-exhausted",
                "global-time-bound-exhausted",
            )
        ):
            status, verdict = "resource_truncated", None
    record = {
        "evidence_status": FORMAL_RAW_STATUS,
        "program_id": program_id,
        "method_id": method_id,
        "status": status,
        "verdict": verdict,
        "runtime_ms": isolated["runtime_ms"],
        "peak_memory_bytes": isolated["peak_memory_bytes"],
        "states": outcome.get("states") if outcome else None,
        "transitions": outcome.get("transitions") if outcome else None,
        "model_time_ns": outcome.get("model_time_ns") if outcome else None,
        "timeout": status == "timeout",
        "reason_codes": reasons,
        "stdout_sha256": isolated["stdout_sha256"],
        "stderr_sha256": isolated["stderr_sha256"],
        "stdout_size_bytes": isolated["stdout_size_bytes"],
        "stderr_size_bytes": isolated["stderr_size_bytes"],
        "worker_exit_code": isolated["worker_exit_code"],
        "budget": effective_budget,
        "artifact": {
            "worker_status": status,
            "worker_exit_code": isolated["worker_exit_code"],
            "outcome": outcome,
        },
    }
    if method_id == "llm_as_judge" and outcome:
        record["llm_judgment_accounting"] = dict(outcome.get("aggregation", {}))
    return record


assert_equal_common_budgets()
