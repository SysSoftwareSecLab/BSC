"""RQ3 scalability preparation and failure-inclusive summaries.

PREP_ONLY_NOT_FORMAL_STAGE4_EVIDENCE

This module extracts preregistered structural factors from
source/IR and summarizes already-produced run records.  It never runs the
verifier and never reads oracle labels.
"""

from __future__ import annotations

import ast
import hashlib
import math
import random
import statistics
from collections import Counter, defaultdict
from collections.abc import Iterable, Mapping, Sequence
from typing import Any

from bisafecode.stage4_controlled.property_adapters import build_full_method_inputs
from bisafecode.stage4_controlled.generator import TRAJECTORIES
from bisafecode.stage4_unseen.time_bounds import source_time_upper_bound_ns
from bisafecode.timed_ir import ActionNode

from . import PREP_STATUS


SCALABILITY_FACTORS = (
    "ir_node_count",
    "ir_action_node_count",
    "branch_count",
    "max_loop_bound",
    "parallel_region_count",
    "parallel_width",
    "simultaneously_active_object_upper_bound",
    "simultaneously_active_resource_upper_bound",
    "geometric_segment_count",
    "interval_refinement_level",
)

REQUIRED_RUN_FIELDS = (
    "run_id",
    "program_id",
    "method_id",
    "axis",
    "requested_level",
    "repetition_index",
    "status",
    "verdict",
    "runtime_ms",
    "checker_elapsed_ms",
    "peak_rss_bytes",
    "explored_states",
    "explored_transitions",
)

_SOURCE_APIS = {
    "acquire",
    "release",
    "close",
    "open",
    "transfer_authority",
    "move",
    "wait",
    "barrier",
    "parallel",
}


def _literal_string(node: ast.AST) -> str | None:
    if isinstance(node, ast.Constant) and isinstance(node.value, str):
        return node.value
    return None


def _range_bound(node: ast.AST) -> int | None:
    if not isinstance(node, ast.Call) or not isinstance(node.func, ast.Name):
        return None
    if node.func.id != "range" or not 1 <= len(node.args) <= 3:
        return None
    values: list[int] = []
    for argument in node.args:
        if not isinstance(argument, ast.Constant) or not isinstance(argument.value, int):
            return None
        values.append(argument.value)
    if len(values) == 1:
        start, stop, step = 0, values[0], 1
    elif len(values) == 2:
        start, stop, step = values[0], values[1], 1
    else:
        start, stop, step = values
    if step == 0:
        return None
    return len(range(start, stop, step))


def extract_program_complexity(
    *,
    source: str,
    program_id: str,
    interval_refinement_level: int = 0,
) -> dict[str, Any]:
    """Extract RQ3 factors without executing the checker or consulting labels."""

    tree = ast.parse(source, filename=f"<{program_id}>", mode="exec")
    calls = [node for node in ast.walk(tree) if isinstance(node, ast.Call)]
    named_calls = [
        node
        for node in calls
        if isinstance(node.func, ast.Name) and node.func.id in _SOURCE_APIS
    ]
    call_counts = Counter(node.func.id for node in named_calls if isinstance(node.func, ast.Name))

    objects: set[str] = set()
    resources: set[str] = set()
    trajectories: set[str] = set()
    for call in named_calls:
        assert isinstance(call.func, ast.Name)
        if call.func.id in {"close", "open"} and len(call.args) >= 2:
            value = _literal_string(call.args[1])
            if value:
                objects.add(value)
        elif call.func.id == "transfer_authority" and call.args:
            value = _literal_string(call.args[0])
            if value:
                objects.add(value)
        elif call.func.id in {"acquire", "release"} and len(call.args) >= 2:
            value = _literal_string(call.args[1])
            if value:
                resources.add(value)
        elif call.func.id == "move" and len(call.args) >= 2:
            value = _literal_string(call.args[1])
            if value:
                trajectories.add(value)

    loop_bounds = [
        bound
        for node in ast.walk(tree)
        if isinstance(node, ast.For)
        for bound in [_range_bound(node.iter)]
        if bound is not None
    ]
    program, _environment = build_full_method_inputs(
        source=source,
        program_id=program_id,
    )
    input_valuations = math.prod(len(item.values) for item in program.finite_inputs)

    return {
        "prep_status": PREP_STATUS,
        "program_id": program_id,
        "source_sha256": hashlib.sha256(source.encode("utf-8")).hexdigest(),
        "source_api_call_count": len(named_calls),
        "ir_node_count": len(program.nodes),
        "ir_action_node_count": sum(
            isinstance(node, ActionNode) for node in program.nodes
        ),
        "branch_count": sum(isinstance(node, ast.If) for node in ast.walk(tree)),
        "loop_count": len(loop_bounds),
        "max_loop_bound": max(loop_bounds, default=0),
        "parallel_region_count": call_counts["parallel"],
        "parallel_width": 2 if call_counts["parallel"] else 1,
        "barrier_count": call_counts["barrier"],
        "geometric_segment_count": call_counts["move"],
        "object_count": len(objects),
        "resource_count": len(resources),
        "simultaneously_active_object_upper_bound": len(objects),
        "simultaneously_active_resource_upper_bound": len(resources),
        "trajectory_reference_count": len(trajectories),
        "finite_input_valuations": input_valuations,
        "source_time_upper_bound_ns": source_time_upper_bound_ns(
            source,
            {
                value["sha256"]: int(value["duration_ns"])
                for value in TRAJECTORIES.values()
            },
        ),
        "interval_refinement_level": int(interval_refinement_level),
    }


def _quantile(values: Sequence[float], probability: float) -> float | None:
    if not values:
        return None
    ordered = sorted(float(value) for value in values)
    if len(ordered) == 1:
        return ordered[0]
    position = (len(ordered) - 1) * probability
    lower = math.floor(position)
    upper = math.ceil(position)
    if lower == upper:
        return ordered[lower]
    weight = position - lower
    return ordered[lower] * (1.0 - weight) + ordered[upper] * weight


def _bootstrap_median_ci(
    values: Sequence[float],
    *,
    seed_text: str,
    replicates: int,
) -> list[float | None]:
    if not values:
        return [None, None]
    seed = int.from_bytes(hashlib.sha256(seed_text.encode("utf-8")).digest()[:8], "big")
    rng = random.Random(seed)
    bootstrapped: list[float] = []
    for _ in range(replicates):
        sample = [float(values[rng.randrange(len(values))]) for _ in values]
        bootstrapped.append(float(statistics.median(sample)))
    return [_quantile(bootstrapped, 0.025), _quantile(bootstrapped, 0.975)]


def _metric_summary(
    rows: Sequence[Mapping[str, Any]],
    field: str,
    *,
    seed_text: str,
    bootstrap_replicates: int,
) -> dict[str, Any]:
    values = [float(row[field]) for row in rows if row.get(field) is not None]
    return {
        "observed_n": len(values),
        "median": float(statistics.median(values)) if values else None,
        "q1": _quantile(values, 0.25),
        "q3": _quantile(values, 0.75),
        "median_bootstrap_95_ci": _bootstrap_median_ci(
            values,
            seed_text=f"{seed_text}:{field}",
            replicates=bootstrap_replicates,
        ),
    }


def validate_scalability_rows(rows: Sequence[Mapping[str, Any]]) -> None:
    seen: set[tuple[str, str]] = set()
    for index, row in enumerate(rows):
        missing = [field for field in REQUIRED_RUN_FIELDS if field not in row]
        if missing:
            raise ValueError(f"row {index} missing required fields: {missing}")
        key = (str(row["run_id"]), str(row["method_id"]))
        if key in seen:
            raise ValueError(f"duplicate scalability run row: {key}")
        seen.add(key)
        if not str(row["axis"]):
            raise ValueError("scalability axis must be nonempty")
        if isinstance(row["requested_level"], bool) or not isinstance(
            row["requested_level"], int
        ):
            raise ValueError("requested scalability level must be an integer")
        if (
            isinstance(row["repetition_index"], bool)
            or not isinstance(row["repetition_index"], int)
            or row["repetition_index"] < 1
        ):
            raise ValueError("repetition_index must be a positive integer")
        if row["status"] not in {
            "ok",
            "timeout",
            "missing",
            "exception",
            "provider_failure",
            "resource_truncated",
        }:
            raise ValueError(f"unsupported run status: {row['status']}")
        if row["status"] == "ok" and row["verdict"] not in {
            "verified-within-bounds",
            "violated",
            "unknown",
            "invalid",
        }:
            raise ValueError(f"unsupported four-value verdict: {row['verdict']}")
        if row["status"] != "ok" and row["verdict"] is not None:
            raise ValueError("non-ok scalability row verdict must be null")
        for factor in SCALABILITY_FACTORS:
            if factor not in row:
                raise ValueError(f"row {index} missing scalability factor: {factor}")


def summarize_scalability(
    rows: Sequence[Mapping[str, Any]],
    *,
    bootstrap_replicates: int = 10_000,
    evidence_status: str = PREP_STATUS,
    paper_result_eligible: bool = False,
) -> dict[str, Any]:
    """Summarize all scheduled rows; timeouts/errors remain in denominators."""

    if bootstrap_replicates <= 0:
        raise ValueError("bootstrap_replicates must be positive")
    validate_scalability_rows(rows)
    primary_groups: dict[tuple[str, int], list[Mapping[str, Any]]] = defaultdict(list)
    factor_groups: dict[tuple[str, Any], list[Mapping[str, Any]]] = defaultdict(list)
    for row in rows:
        primary_groups[(str(row["axis"]), int(row["requested_level"]))].append(row)
        for factor in SCALABILITY_FACTORS:
            factor_groups[(factor, row[factor])].append(row)

    def summarize_group(
        group: Sequence[Mapping[str, Any]], *, seed_text: str
    ) -> dict[str, Any]:
        statuses = Counter(str(row["status"]) for row in group)
        verdicts = Counter(
            str(row["verdict"]) for row in group if row["status"] == "ok"
        )
        scheduled_n = len(group)
        decided = verdicts["verified-within-bounds"] + verdicts["violated"]
        return {
            "scheduled_n": scheduled_n,
            "status_counts": dict(sorted(statuses.items())),
            "verdict_counts": dict(sorted(verdicts.items())),
            "completion_rate": statuses["ok"] / scheduled_n,
            "coverage": decided / scheduled_n,
            "timeout_rate": statuses["timeout"] / scheduled_n,
            "resource_truncation_rate": statuses["resource_truncated"] / scheduled_n,
            "unknown_rate": verdicts["unknown"] / scheduled_n,
            "invalid_rate": verdicts["invalid"] / scheduled_n,
            "runtime_ms": _metric_summary(
                group,
                "runtime_ms",
                seed_text=seed_text,
                bootstrap_replicates=bootstrap_replicates,
            ),
            "checker_elapsed_ms": _metric_summary(
                group,
                "checker_elapsed_ms",
                seed_text=seed_text,
                bootstrap_replicates=bootstrap_replicates,
            ),
            "peak_rss_bytes": _metric_summary(
                group,
                "peak_rss_bytes",
                seed_text=seed_text,
                bootstrap_replicates=bootstrap_replicates,
            ),
            "explored_states": _metric_summary(
                group,
                "explored_states",
                seed_text=seed_text,
                bootstrap_replicates=bootstrap_replicates,
            ),
            "explored_transitions": _metric_summary(
                group,
                "explored_transitions",
                seed_text=seed_text,
                bootstrap_replicates=bootstrap_replicates,
            ),
        }

    axis_summaries = []
    for (axis, level), group in sorted(primary_groups.items()):
        axis_summaries.append(
            {
                "axis": axis,
                "requested_level": level,
                **summarize_group(group, seed_text=f"axis:{axis}:{level}"),
            }
        )

    factor_summaries = []
    for (factor, level), group in sorted(
        factor_groups.items(), key=lambda item: (item[0][0], str(item[0][1]))
    ):
        factor_summaries.append(
            {
                "factor": factor,
                "level": level,
                **summarize_group(group, seed_text=f"factor:{factor}:{level}"),
            }
        )

    axis_curves = []
    for axis in sorted({item["axis"] for item in axis_summaries}):
        points = [item for item in axis_summaries if item["axis"] == axis]
        points.sort(key=lambda item: item["requested_level"])

        def ratio(field: str) -> float | None:
            first = points[0][field]["median"]
            last = points[-1][field]["median"]
            if first in {None, 0} or last is None:
                return None
            return float(last) / float(first)

        axis_curves.append(
            {
                "axis": axis,
                "levels": [item["requested_level"] for item in points],
                "largest_over_smallest_level_median_ratio": {
                    field: ratio(field)
                    for field in (
                        "runtime_ms",
                        "checker_elapsed_ms",
                        "peak_rss_bytes",
                        "explored_states",
                        "explored_transitions",
                    )
                },
                "completion_rate_at_largest_level": points[-1]["completion_rate"],
                "timeout_rate_at_largest_level": points[-1]["timeout_rate"],
            }
        )

    return {
        "schema": "bisafecode.stage4.rq3-scalability-summary/v2",
        "evidence_status": evidence_status,
        "paper_result_eligible": paper_result_eligible,
        "scheduled_run_count": len(rows),
        "bootstrap_replicates": bootstrap_replicates,
        "primary_axis_summaries": axis_summaries,
        "axis_curves": axis_curves,
        "secondary_structural_factor_summaries": factor_summaries,
        "notes": [
            "All scheduled rows remain in coverage/timeout/unknown denominators.",
            "Primary comparisons are only within the same preregistered axis/template.",
            "Cross-axis structural-factor summaries are descriptive because templates differ.",
            "End-to-end worker runtime and checker-only elapsed time are reported separately.",
            "Resource metrics use only values present in immutable raw records.",
        ],
    }
