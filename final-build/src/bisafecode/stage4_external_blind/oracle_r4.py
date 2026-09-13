"""Correctness-only external oracle extension for EXP-S4-008 R4.

The S4-002 oracle remains immutable historical evidence.  This extension adds
the two handover obligations exposed by independently authored programs:
receiver-close live geometry and the complete dual-pre/transfer/dual-post
release state machine.  It consumes source traces and frozen public assets
only; method outputs are neither accepted nor imported.
"""

from __future__ import annotations

from collections import defaultdict
import math
from pathlib import Path
from typing import Any, Mapping, Sequence

from bisafecode.stage4_controlled import FORMAL_RAW_STATUS, FREEZE_STATUS
from bisafecode.stage4_controlled.geometry_oracle import _frozen_assets, _state_at
from bisafecode.stage4_controlled.isolation import run_isolated_job
from bisafecode.stage4_controlled.oracle import evaluate_program_oracle


def _result(
    verdict: str, reasons: Sequence[str], witness: Mapping[str, Any] | None = None
) -> Mapping[str, Any]:
    return {
        "evidence_status": FREEZE_STATUS,
        "schema": "bisafecode.stage4.external-blind.handover-oracle-r4/v1",
        "verdict": verdict,
        "reason_codes": list(reasons),
        "witness": dict(witness or {}),
    }


def _inside_zone(
    center: Sequence[float], low: Sequence[float], high: Sequence[float]
) -> bool:
    return all(
        float(lower) <= float(value) <= float(upper)
        for value, lower, upper in zip(center, low, high)
    )


def produce_receiver_close_observations(
    trace: Mapping[str, Any], *, root: Path | None = None
) -> Mapping[str, Mapping[str, Any]]:
    """Produce live geometry for every second-arm close in source order."""

    assets = _frozen_assets((root or Path(__file__).resolve().parents[3]).resolve())
    zone = assets["scene"]["handover_zone"]
    low = zone["min_xyz_m"]
    high = zone["max_xyz_m"]
    grasps: dict[str, set[str]] = defaultdict(set)
    observations: dict[str, Mapping[str, Any]] = {}
    for event in trace.get("events", []):
        kind = event.get("event_kind")
        object_id = event.get("object_id")
        arm = event.get("arm")
        if kind == "close" and isinstance(object_id, str) and isinstance(arm, str):
            if grasps[object_id] and arm not in grasps[object_id]:
                state = _state_at(trace, int(event["time_ns"]), assets)
                left = state["centers"]["left_tool_sphere"]
                right = state["centers"]["right_tool_sphere"]
                observations[str(event["action_execution_ordinal"])] = {
                    "producer_executed": True,
                    "inside_handover_zone": _inside_zone(left, low, high)
                    and _inside_zone(right, low, high),
                    "both_arms_stationary": not state["speed"]["left"]
                    and not state["speed"]["right"],
                    "dual_grasp_consistent": state["attachments"].get(object_id, set())
                    == {"left", "right"}
                    and math.dist(left, right) <= 0.18,
                    "left_tool_center_m": list(left),
                    "right_tool_center_m": list(right),
                }
            grasps[object_id].add(arm)
        elif kind == "open" and isinstance(object_id, str) and isinstance(arm, str):
            grasps[object_id].discard(arm)
    return observations


def evaluate_external_handover_trace_r4(
    trace: Mapping[str, Any],
    *,
    receiver_close_observations: Mapping[str, Mapping[str, Any]],
) -> Mapping[str, Any]:
    """Check the complete handover state machine on one source trace."""

    objects: dict[str, dict[str, Any]] = defaultdict(
        lambda: {
            "phase": "free",
            "grasps": set(),
            "authority": None,
            "sender": None,
            "receiver": None,
        }
    )
    for event in trace.get("events", []):
        kind = event.get("event_kind")
        object_id = event.get("object_id")
        if not isinstance(object_id, str):
            continue
        state = objects[object_id]
        if kind == "close":
            arm = event.get("arm")
            if arm not in {"left", "right"}:
                return _result("invalid", ["CLOSE_ARM_INVALID"], event)
            if state["phase"] == "free":
                state.update(
                    phase="sender_only",
                    grasps={arm},
                    authority=arm,
                    sender=arm,
                    receiver=None,
                )
                continue
            if (
                state["phase"] != "sender_only"
                or arm in state["grasps"]
                or len(state["grasps"]) != 1
            ):
                return _result("violated", ["OBJECT_DOUBLE_CLOSE"], event)
            observation = receiver_close_observations.get(
                str(event.get("action_execution_ordinal"))
            )
            if not isinstance(observation, Mapping) or observation.get("producer_executed") is not True:
                return _result("unknown", ["RECEIVER_CLOSE_GEOMETRY_MISSING"], event)
            if observation.get("both_arms_stationary") is not True:
                return _result("violated", ["RECEIVER_CLOSE_WHILE_MOVING"], event)
            if observation.get("inside_handover_zone") is not True:
                return _result("violated", ["RECEIVER_GRASP_OUTSIDE_HANDOVER_ZONE"], event)
            if observation.get("dual_grasp_consistent") is not True:
                return _result("violated", ["INCONSISTENT_DUAL_ATTACHMENT"], event)
            state["grasps"].add(arm)
            state["receiver"] = arm
            state["phase"] = "dual_pre_transfer"
        elif kind == "transfer_authority":
            sender = event.get("sender")
            receiver = event.get("receiver")
            if (
                state["phase"] != "dual_pre_transfer"
                or sender != state["sender"]
                or receiver != state["receiver"]
                or state["authority"] != sender
                or state["grasps"] != {sender, receiver}
            ):
                return _result("violated", ["ILLEGAL_AUTHORITY_TRANSFER"], event)
            state["authority"] = receiver
            state["phase"] = "dual_post_transfer"
        elif kind == "open":
            arm = event.get("arm")
            if arm not in state["grasps"]:
                return _result("violated", ["OBJECT_OPEN_BY_NONOWNER"], event)
            if state["phase"] == "dual_pre_transfer":
                reason = (
                    "SENDER_RELEASE_BEFORE_TRANSFER"
                    if arm == state["sender"]
                    else "RECEIVER_RELEASE_DURING_HANDOVER"
                )
                return _result("violated", [reason], event)
            if state["phase"] == "dual_post_transfer":
                if arm != state["sender"]:
                    return _result("violated", ["RECEIVER_RELEASE_DURING_HANDOVER"], event)
                state["grasps"].remove(arm)
                state["sender"] = None
                state["phase"] = "receiver_only"
                continue
            if state["phase"] not in {"sender_only", "receiver_only"}:
                return _result("invalid", ["OBJECT_PHASE_INVALID"], event)
            state.update(
                phase="free",
                grasps=set(),
                authority=None,
                sender=None,
                receiver=None,
            )
    return _result("verified-within-bounds", [])


def evaluate_external_program_oracle_r4(
    source: str,
    *,
    program_id: str,
    trajectory_durations_ns: Mapping[str, int],
    traces: Sequence[Mapping[str, Any]],
) -> Mapping[str, Any]:
    """Compose immutable S4-002 evidence with the R4 handover extension."""

    base = evaluate_program_oracle(
        source,
        program_id=program_id,
        trajectory_durations_ns=trajectory_durations_ns,
        traces=traces,
    )
    extension_results = []
    observation_by_input = {}
    for trace in traces:
        key = (
            f"mode={trace['inputs']['mode']};"
            f"ready={str(trace['inputs']['ready']).lower()}"
        )
        observations = produce_receiver_close_observations(trace)
        observation_by_input[key] = observations
        extension_results.append(
            evaluate_external_handover_trace_r4(
                trace, receiver_close_observations=observations
            )
        )
    extension_verdict = "verified-within-bounds"
    extension_reasons: list[str] = []
    for candidate in ("invalid", "violated", "unknown"):
        matched = [result for result in extension_results if result["verdict"] == candidate]
        if matched:
            extension_verdict = candidate
            extension_reasons = list(matched[0]["reason_codes"])
            break
    base_verdict = base.get("verdict")
    if base_verdict == "invalid" or extension_verdict == "invalid":
        verdict = "invalid"
    elif base_verdict == "unsafe" or extension_verdict == "violated":
        verdict = "unsafe"
    elif base_verdict != "safe" or extension_verdict != "verified-within-bounds":
        verdict = "unknown"
    else:
        verdict = "safe"
    return {
        "evidence_status": FREEZE_STATUS,
        "schema": "bisafecode.stage4.external-blind.combined-oracle-r4/v1",
        "program_id": program_id,
        "verdict": verdict,
        "reason_codes": sorted(set(list(base.get("reason_codes", [])) + extension_reasons)),
        "base_s4_002_oracle": base,
        "external_handover_extension": {
            "verdict": extension_verdict,
            "reason_codes": extension_reasons,
            "finite_input_results": extension_results,
            "receiver_close_observations_by_input": observation_by_input,
        },
        "verifier_outputs_visible": False,
        "method_outputs_visible": False,
    }


def execute_external_oracle_r4(
    source: str,
    *,
    program_id: str,
    stdout_path: Path,
    stderr_path: Path,
    repository_root: Path,
    budget: Mapping[str, int],
) -> Mapping[str, Any]:
    isolated = run_isolated_job(
        "external_oracle_r4",
        {"source": source, "program_id": program_id},
        stdout_path=stdout_path,
        stderr_path=stderr_path,
        budget=budget,
        repository_root=repository_root,
    )
    outcome = isolated["outcome"] if isinstance(isolated.get("outcome"), dict) else None
    result = outcome.get("result") if outcome and isinstance(outcome.get("result"), dict) else None
    status = isolated["status"]
    verdict = result.get("verdict") if status == "ok" and result else None
    reasons = (
        list(result.get("reason_codes", ()))
        if verdict is not None
        else list(isolated["reason_codes"])
    )
    evidence: Any = result if result is not None else {
        "evidence_status": FORMAL_RAW_STATUS,
        "schema": "bisafecode.stage4.external-blind.oracle-r4-failure/v1",
        "status": status,
        "verdict": None,
        "reason_codes": reasons,
    }
    traces: Any = outcome.get("traces") if outcome else {
        "evidence_status": FORMAL_RAW_STATUS,
        "schema": "bisafecode.stage4.external-blind.oracle-r4-traces-failure/v1",
        "traces": [],
    }
    return {
        "evidence_status": FORMAL_RAW_STATUS,
        "program_id": program_id,
        "status": status,
        "verdict": verdict,
        "runtime_ms": isolated["runtime_ms"],
        "peak_memory_bytes": isolated["peak_memory_bytes"],
        "reason_codes": reasons,
        "timeout": status == "timeout",
        "worker_exit_code": isolated["worker_exit_code"],
        "stdout_sha256": isolated["stdout_sha256"],
        "stderr_sha256": isolated["stderr_sha256"],
        "stdout_size_bytes": isolated["stdout_size_bytes"],
        "stderr_size_bytes": isolated["stderr_size_bytes"],
        "traces": traces,
        "evidence": evidence,
        "verifier_outputs_visible": False,
        "method_outputs_visible": False,
    }
