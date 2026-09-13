"""Executable, label-blind Stage 4 RQ2 ablation variants.

PREP_ONLY_NOT_FORMAL_STAGE4_EVIDENCE

The functions in this module accept source text only.  They never accept an
oracle label, expected verdict, target class, or verifier result.
"""

from __future__ import annotations

import ast
import hashlib
from pathlib import Path
from typing import Any, Mapping

from bisafecode.explicit_state import (
    IntervalAssessment,
    IntervalStatus,
    PointAssessment,
    SearchBounds,
)
from bisafecode.model_checking import (
    BoundedExplicitStateModelChecker,
    PropertyCheckerBinding,
    callable_implementation_sha256,
)
from bisafecode.stage4_controlled.generator import TRAJECTORIES
from bisafecode.stage4_controlled.identity import compute_asset_identity
from bisafecode.stage4_controlled import FORMAL_RAW_STATUS
from bisafecode.stage4_controlled.isolation import run_isolated_job
from bisafecode.stage4_controlled.methods import (
    COMMON_BUDGET,
    run_full_method,
    run_non_timed_baseline,
)
from bisafecode.stage4_controlled.property_adapters import (
    CHECKED_PAIRS,
    _allowed_attachment,
    _cached_live_speed_bounds,
    _centers,
    _distance,
    build_full_method_inputs,
    controlled_interval_property,
    controlled_point_property,
)
from bisafecode.stage4_unseen.time_bounds import source_time_upper_bound_ns
from bisafecode.timed_ir import ActionKind

from . import RQ2_VARIANTS


PROJECTIONS = ("handover", "resource", "geometry")
HANDOVER_APIS = {"close", "open", "transfer_authority"}
RESOURCE_APIS = {"acquire", "release"}
GEOMETRY_APIS = {"move"}
SHARED_CONTROL_APIS = {"wait", "barrier", "parallel", "range"}


def _literal(node: ast.AST) -> Any:
    if isinstance(node, ast.Constant) and type(node.value) in {str, int, bool}:
        return node.value
    raise ValueError("ablation projection requires literal API arguments")


class _ProjectionTransformer(ast.NodeTransformer):
    def __init__(self, projection: str):
        if projection not in PROJECTIONS:
            raise ValueError("unknown separated-state projection")
        self.projection = projection
        self.trajectory_durations = {
            str(value["sha256"]): int(value["duration_ns"])
            for value in TRAJECTORIES.values()
        }

    def visit_Expr(self, node: ast.Expr) -> ast.stmt:
        node = self.generic_visit(node)
        call = node.value
        if not isinstance(call, ast.Call) or not isinstance(call.func, ast.Name):
            return node
        name = call.func.id
        keep = (
            name in SHARED_CONTROL_APIS
            or (self.projection == "handover" and name in HANDOVER_APIS)
            or (self.projection == "resource" and name in RESOURCE_APIS)
            or (self.projection == "geometry" and name in GEOMETRY_APIS)
        )
        if keep:
            return node
        if name in {"close", "open"}:
            arm = _literal(call.args[0])
            duration_ns = _literal(call.args[2])
            return ast.copy_location(
                ast.Expr(
                    value=ast.Call(
                        func=ast.Name(id="wait", ctx=ast.Load()),
                        args=[ast.Constant(arm), ast.Constant(duration_ns)],
                        keywords=[],
                    )
                ),
                node,
            )
        if name == "move":
            arm = _literal(call.args[0])
            trajectory_hash = str(_literal(call.args[1]))
            if trajectory_hash not in self.trajectory_durations:
                raise ValueError("projection encountered an unfrozen trajectory")
            return ast.copy_location(
                ast.Expr(
                    value=ast.Call(
                        func=ast.Name(id="wait", ctx=ast.Load()),
                        args=[
                            ast.Constant(arm),
                            ast.Constant(self.trajectory_durations[trajectory_hash]),
                        ],
                        keywords=[],
                    )
                ),
                node,
            )
        if name in HANDOVER_APIS | RESOURCE_APIS | GEOMETRY_APIS:
            return ast.copy_location(ast.Pass(), node)
        return node


def project_source(source: str, projection: str) -> str:
    """Remove cross-property state while preserving control and elapsed time."""

    module = ast.parse(source, filename="program.py", mode="exec")
    transformed = _ProjectionTransformer(projection).visit(module)
    ast.fix_missing_locations(transformed)
    return (
        "# PREP_ONLY_NOT_FORMAL_STAGE4_EVIDENCE\n"
        f"# SEPARATED_STATE_PROJECTION={projection}\n"
        + ast.unparse(transformed)
        + "\n"
    )


def separated_projection_sources(source: str) -> Mapping[str, str]:
    return {projection: project_source(source, projection) for projection in PROJECTIONS}


class _RemoveAuthorityTransfer(ast.NodeTransformer):
    """Build the grasp/attachment side without logical-authority state."""

    def visit_Expr(self, node: ast.Expr) -> ast.stmt:
        node = self.generic_visit(node)
        if (
            isinstance(node.value, ast.Call)
            and isinstance(node.value.func, ast.Name)
            and node.value.func.id == "transfer_authority"
        ):
            return ast.copy_location(ast.Pass(), node)
        return node


def _grasp_attachment_only_source(source: str) -> str:
    module = _RemoveAuthorityTransfer().visit(
        ast.parse(source, filename="program.py", mode="exec")
    )
    ast.fix_missing_locations(module)
    return (
        "# PREP_ONLY_NOT_FORMAL_STAGE4_EVIDENCE\n"
        "# SEPARATED_GRASP_AUTHORITY_STATE=grasp_attachment_only\n"
        + ast.unparse(module)
        + "\n"
    )


def _authority_local_check(source: str) -> Mapping[str, Any]:
    """Check authority-call syntax without consuming physical grasp state.

    This intentionally cannot decide whether the receiver physically grasps the
    object.  That missing relation is the exact state join removed by this
    ablation; it is not reconstructed from the source or from an oracle label.
    """

    calls = []
    for node in ast.walk(ast.parse(source, filename="program.py", mode="exec")):
        if not (
            isinstance(node, ast.Call)
            and isinstance(node.func, ast.Name)
            and node.func.id == "transfer_authority"
        ):
            continue
        if len(node.args) != 3:
            return {
                "verdict": "invalid",
                "reason_codes": ["SEPARATED_AUTHORITY_CALL_ARITY_INVALID"],
                "transfer_count": len(calls),
            }
        object_id, sender, receiver = (_literal(value) for value in node.args)
        if (
            object_id not in {"payload_alpha", "payload_beta"}
            or sender not in {"left", "right"}
            or receiver not in {"left", "right"}
            or sender == receiver
        ):
            return {
                "verdict": "invalid",
                "reason_codes": ["SEPARATED_AUTHORITY_CALL_DOMAIN_INVALID"],
                "transfer_count": len(calls),
            }
        calls.append((object_id, sender, receiver))
    return {
        "verdict": "verified-within-bounds",
        "reason_codes": (
            ["PHYSICAL_GRASP_AUTHORITY_JOIN_REMOVED"] if calls else []
        ),
        "transfer_count": len(calls),
        "physical_grasp_state_consumed": False,
    }


def _attached_object_ids(world) -> set[str]:
    return {item.object_id for item in world.objects if item.grasps}


def _no_attached_pairs(world):
    removed = _attached_object_ids(world)
    return tuple(
        pair
        for pair in CHECKED_PAIRS
        if not (removed & set(pair)) and not _allowed_attachment(world, pair)
    )


def no_attached_object_point_property(state) -> PointAssessment:
    centers = _centers(state.world)
    for pair in _no_attached_pairs(state.world):
        if _distance(centers, pair) <= 0:
            return PointAssessment(
                IntervalStatus.COLLISION,
                "no-attached-object-point-collision",
                state.time_ns,
                entity_pair=pair,
            )
    return PointAssessment(
        IntervalStatus.SAFE,
        "no-attached-object-point-separated",
        state.time_ns,
        certificate_ref="stage4-rq2-no-attached-object-point/v1",
    )


def no_attached_object_interval_property(
    state, start_ns: int, end_ns: int, active
) -> IntervalAssessment:
    centers = _centers(state.world)
    pairs = _no_attached_pairs(state.world)
    minimum = min((_distance(centers, pair) for pair in pairs), default=float("inf"))
    bounds = _cached_live_speed_bounds()
    speed_upper = 0.0
    for action in active:
        if action.kind is not ActionKind.MOVE:
            continue
        if action.trajectory_hash not in bounds:
            raise ValueError("active move lacks a frozen trajectory speed bound")
        speed_upper += bounds[action.trajectory_hash]
    lower = minimum - speed_upper * ((end_ns - start_ns) * 1e-9)
    if minimum <= 0:
        pair = next(pair for pair in pairs if _distance(centers, pair) <= 0)
        return IntervalAssessment(
            IntervalStatus.COLLISION,
            "no-attached-object-interval-start-collision",
            (start_ns, min(end_ns, start_ns + 1)),
            entity_pair=pair,
        )
    if lower <= 0:
        return IntervalAssessment(
            IntervalStatus.UNKNOWN,
            "no-attached-object-interval-conservative-unresolved",
            (start_ns, end_ns),
        )
    return IntervalAssessment(
        IntervalStatus.SAFE,
        "no-attached-object-interval-separated",
        (start_ns, end_ns),
        certificate_ref="stage4-rq2-no-attached-object-interval/v1",
    )


def _run_bmc(
    source: str,
    *,
    program_id: str,
    variant_id: str,
    point_checker,
    interval_checker,
) -> Mapping[str, Any]:
    program, environment = build_full_method_inputs(source, program_id=program_id)
    asset_root = compute_asset_identity()["root_sha256"]
    point_binding = PropertyCheckerBinding(
        name=f"stage4-rq2-{variant_id}-point/v1",
        implementation_sha256=callable_implementation_sha256(point_checker),
        evidence_sha256=(asset_root,),
        input_contract="exact SearchState plus frozen controlled asset root",
        deterministic=True,
        side_effect_free=True,
    )
    interval_binding = PropertyCheckerBinding(
        name=f"stage4-rq2-{variant_id}-interval/v1",
        implementation_sha256=callable_implementation_sha256(interval_checker),
        evidence_sha256=(asset_root,),
        input_contract="SearchState, interval, active actions, frozen asset root",
        deterministic=True,
        side_effect_free=True,
    )
    result = BoundedExplicitStateModelChecker(
        program,
        environment,
        interval_checker=interval_checker,
        point_checker=point_checker,
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
        "variant_id": variant_id,
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


def run_separated_state_ablation(
    source: str, *, program_id: str
) -> Mapping[str, Any]:
    grasp_source = _grasp_attachment_only_source(source)
    grasp_result = _run_bmc(
        grasp_source,
        program_id=f"{program_id}::grasp_attachment",
        variant_id="separated-grasp-attachment",
        point_checker=controlled_point_property,
        interval_checker=controlled_interval_property,
    )
    authority_result = _authority_local_check(source)
    verdicts = {grasp_result["verdict"], authority_result["verdict"]}
    if "invalid" in verdicts:
        verdict = "invalid"
    elif "violated" in verdicts:
        verdict = "violated"
    elif "unknown" in verdicts:
        verdict = "unknown"
    else:
        verdict = "verified-within-bounds"
    reasons = sorted(
        {
            *(f"GRASP::{reason}" for reason in grasp_result["reason_codes"]),
            *(f"AUTHORITY::{reason}" for reason in authority_result["reason_codes"]),
        }
    )
    return {
        "variant_id": "ablation_separated_state_property",
        "verdict": verdict,
        "reason_codes": reasons,
        "states": int(grasp_result["states"]),
        "transitions": int(grasp_result["transitions"]),
        "model_time_ns": int(grasp_result["model_time_ns"]),
        "grasp_attachment_source_sha256": hashlib.sha256(
            grasp_source.encode("utf-8")
        ).hexdigest(),
        "grasp_attachment_result": grasp_result,
        "authority_local_result": authority_result,
        "removed_join": "physical grasp/attachment state -> logical authority transfer precondition",
    }


def run_no_attached_object_geometry_ablation(
    source: str, *, program_id: str
) -> Mapping[str, Any]:
    return _run_bmc(
        source,
        program_id=program_id,
        variant_id="ablation_no_attached_object_geometry",
        point_checker=no_attached_object_point_property,
        interval_checker=no_attached_object_interval_property,
    )


def run_ablation_variant(
    variant_id: str, source: str, *, program_id: str
) -> Mapping[str, Any]:
    """Run one label-blind RQ2 variant in process for an isolated caller."""

    if variant_id not in RQ2_VARIANTS:
        raise ValueError("unknown RQ2 variant")
    if variant_id == "bisafecode_full":
        return {"variant_id": variant_id, **run_full_method(source, program_id=program_id)}
    if variant_id == "ablation_non_timed":
        return {
            "variant_id": variant_id,
            **run_non_timed_baseline(source, program_id=program_id),
        }
    if variant_id == "ablation_separated_state_property":
        return run_separated_state_ablation(source, program_id=program_id)
    return run_no_attached_object_geometry_ablation(source, program_id=program_id)


def execute_ablation_variant(
    variant_id: str,
    source: str,
    *,
    program_id: str,
    stdout_path: Path,
    stderr_path: Path,
    repository_root: Path,
    budget: Mapping[str, int],
) -> Mapping[str, Any]:
    """Run one label-blind RQ2 variant in a fresh hard-limited worker."""

    isolated = run_isolated_job(
        "ablation",
        {"variant_id": variant_id, "source": source, "program_id": program_id},
        stdout_path=stdout_path,
        stderr_path=stderr_path,
        budget=budget,
        repository_root=repository_root,
    )
    outcome = isolated["outcome"] if isinstance(isolated.get("outcome"), dict) else None
    status = isolated["status"]
    verdict = outcome.get("verdict") if status == "ok" and outcome else None
    reasons = list(outcome.get("reason_codes", ())) if outcome else list(isolated["reason_codes"])
    return {
        "evidence_status": FORMAL_RAW_STATUS,
        "program_id": program_id,
        "variant_id": variant_id,
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
        "budget": dict(budget),
        "artifact": outcome,
    }
