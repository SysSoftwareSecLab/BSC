"""Independent complete-source logical oracle for future EXP-S4-002 labels.

FREEZE_ONLY_NOT_FORMAL_STAGE4_EVIDENCE

The oracle reconstructs the expected action/event inventory from immutable
source and frozen trajectory durations.  It therefore never accepts a
caller-declared ``complete`` bit, count, or ordinal as evidence of coverage.
It has no dependency on verifier successor/transition/property code.
"""

from __future__ import annotations

from collections import defaultdict
from typing import Any, Dict, Mapping, Sequence

from . import FREEZE_STATUS
from .identity import expected_logical_oracle_identities
from .trace_extractor import IndependentTraceError, extract_all_finite_traces, extract_event_trace


def _result(verdict: str, reasons: Sequence[str], witness: Mapping[str, Any] = None):
    return {
        "evidence_status": FREEZE_STATUS,
        "schema": "bisafecode.stage4.controlled.logical-oracle-result/v2",
        "verdict": verdict,
        "reason_codes": list(reasons),
        "witness": dict(witness or {}),
    }


def _binding(event: Mapping[str, Any]) -> tuple:
    return (
        event.get("program_id"),
        event.get("event_ordinal"),
        event.get("time_ns"),
        event.get("event_kind"),
        event.get("action_kind"),
        event.get("action_execution_ordinal"),
        event.get("canonical_action_ordinal"),
        event.get("source_span"),
        event.get("arm"),
        event.get("object_id"),
        event.get("resource_id"),
        event.get("trajectory_hash"),
        event.get("sender"),
        event.get("receiver"),
        event.get("barrier_id"),
        event.get("terminal_status"),
    )


def _validate_trace_coverage(
    trace: Mapping[str, Any],
    *,
    source: str,
    trajectory_durations_ns: Mapping[str, int],
) -> tuple[Mapping[str, Any] | None, Mapping[str, Any] | None]:
    if trace.get("complete") is not True or not isinstance(trace.get("events"), list):
        return None, _result("unknown", ["TRACE_INCOMPLETE"])
    inputs = trace.get("inputs")
    if not isinstance(inputs, dict):
        return None, _result("invalid", ["TRACE_INPUT_DOMAIN_INVALID"])
    try:
        expected = extract_event_trace(
            source,
            program_id=str(trace.get("program_id")),
            inputs=inputs,
            trajectory_durations_ns=trajectory_durations_ns,
        )
    except (IndependentTraceError, SyntaxError, ValueError, TypeError) as error:
        return None, _result("invalid", ["SOURCE_OR_INPUT_INVALID"], {"error": type(error).__name__})
    if trace.get("source_sha256") != expected["source_sha256"]:
        return None, _result("invalid", ["TRACE_SOURCE_IDENTITY_MISMATCH"])
    if trace.get("source_action_count") != expected["source_action_count"]:
        return None, _result("unknown", ["TRACE_ACTION_COUNT_MISMATCH"])
    if trace.get("terminal_time_ns") != expected["terminal_time_ns"]:
        return None, _result("unknown", ["TRACE_TERMINAL_MISMATCH"])
    observed_events = trace["events"]
    if len(observed_events) != len(expected["events"]):
        return None, _result("unknown", ["TRACE_EVENT_COUNT_MISMATCH"])
    if any(not isinstance(event, dict) for event in observed_events):
        return None, _result("invalid", ["TRACE_EVENT_MALFORMED"])
    if [_binding(event) for event in observed_events] != [
        _binding(event) for event in expected["events"]
    ]:
        return None, _result("unknown", ["TRACE_SOURCE_ACTION_BINDING_MISMATCH"])
    terminal = [event for event in observed_events if event.get("event_kind") == "terminal"]
    if len(terminal) != 1 or terminal[0] is not observed_events[-1] or terminal[0].get("terminal_status") != "normal":
        return None, _result("unknown", ["TRACE_TERMINAL_MISMATCH"])
    executed = int(expected["source_action_count"])
    for action_ordinal in range(executed):
        pair = [
            event.get("event_kind")
            for event in observed_events
            if event.get("action_execution_ordinal") == action_ordinal
            and event.get("event_kind") in {"action_start", "action_end"}
        ]
        if sorted(pair) != ["action_end", "action_start"]:
            return None, _result("unknown", ["TRACE_ACTION_PHASE_INCOMPLETE"], {"action_execution_ordinal": action_ordinal})
    return expected, None


def evaluate_logical_event_trace(
    trace: Mapping[str, Any],
    *,
    source: str,
    trajectory_durations_ns: Mapping[str, int],
    identities: Mapping[str, str],
    transfer_observations: Mapping[str, Mapping[str, Any]] | None = None,
) -> Mapping[str, Any]:
    """Evaluate one complete finite-input trace without verifier visibility."""

    if dict(identities) != dict(expected_logical_oracle_identities()):
        return _result("invalid", ["ORACLE_IDENTITY_INVALID"])
    _expected, coverage_error = _validate_trace_coverage(
        trace, source=source, trajectory_durations_ns=trajectory_durations_ns
    )
    if coverage_error is not None:
        return coverage_error
    observations = transfer_observations or {}
    resource_owner: Dict[str, str] = {}
    object_grasps: Dict[str, set] = defaultdict(set)
    object_authority: Dict[str, str] = {}
    moving = {"left": False, "right": False}
    for event in trace["events"]:
        kind = event.get("event_kind")
        arm = event.get("arm")
        object_id = event.get("object_id")
        resource_id = event.get("resource_id")
        if kind == "action_start" and event.get("action_kind") == "move":
            moving[arm] = True
        elif kind == "action_end" and event.get("action_kind") == "move":
            moving[arm] = False
        elif kind == "acquire":
            if resource_id in resource_owner:
                return _result("violated", ["RESOURCE_DOUBLE_ACQUIRE"], event)
            resource_owner[resource_id] = arm
        elif kind == "release":
            if resource_owner.get(resource_id) != arm:
                return _result("violated", ["RESOURCE_RELEASE_BY_NONOWNER"], event)
            resource_owner.pop(resource_id)
        elif kind == "close":
            if arm in object_grasps[object_id]:
                return _result("violated", ["OBJECT_DOUBLE_CLOSE"], event)
            if len(object_grasps[object_id]) >= 2:
                return _result("violated", ["OBJECT_DOUBLE_CLOSE"], event)
            object_grasps[object_id].add(arm)
            object_authority.setdefault(object_id, arm)
        elif kind == "open":
            if arm not in object_grasps[object_id]:
                return _result("violated", ["OBJECT_OPEN_BY_NONOWNER"], event)
            object_grasps[object_id].discard(arm)
            if not object_grasps[object_id]:
                object_authority.pop(object_id, None)
        elif kind == "transfer_authority":
            sender = event.get("sender")
            receiver = event.get("receiver")
            observation = observations.get(str(event.get("action_execution_ordinal")))
            if object_grasps[object_id] != {"left", "right"}:
                return _result("violated", ["TRANSFER_WITHOUT_DUAL_GRASP"], event)
            if moving["left"] or moving["right"]:
                return _result("violated", ["TRANSFER_WHILE_MOVING"], event)
            if not isinstance(observation, dict) or observation.get("producer_executed") is not True:
                return _result("unknown", ["TRANSFER_GEOMETRY_OBSERVATION_MISSING"], event)
            if observation.get("both_arms_stationary") is not True:
                return _result("violated", ["TRANSFER_WHILE_MOVING"], event)
            if observation.get("dual_grasp_consistent") is not True:
                return _result("violated", ["TRANSFER_WITHOUT_DUAL_GRASP"], event)
            if observation.get("inside_handover_zone") is not True:
                return _result("violated", ["TRANSFER_OUTSIDE_HANDOVER_ZONE"], event)
            if object_authority.get(object_id) != sender:
                return _result("violated", ["SENDER_RELEASE_BEFORE_TRANSFER"], event)
            if receiver not in {"left", "right"} or sender == receiver:
                return _result("invalid", ["TRANSFER_PARTICIPANT_INVALID"], event)
            object_authority[object_id] = receiver
    return _result("verified-within-bounds", [])


def evaluate_program_logical_oracle(
    source: str,
    *,
    program_id: str,
    trajectory_durations_ns: Mapping[str, int],
    identities: Mapping[str, str],
    traces: Sequence[Mapping[str, Any]],
    transfer_observations_by_input: Mapping[str, Mapping[str, Mapping[str, Any]]] | None = None,
) -> Mapping[str, Any]:
    """Require and aggregate all four frozen mode×ready input valuations."""

    try:
        expected = extract_all_finite_traces(
            source, program_id=program_id, trajectory_durations_ns=trajectory_durations_ns
        )
    except (IndependentTraceError, SyntaxError, ValueError, TypeError) as error:
        return _result("invalid", ["SOURCE_OR_INPUT_INVALID"], {"error": type(error).__name__})
    index = {
        f"mode={trace.get('inputs', {}).get('mode')};ready={str(trace.get('inputs', {}).get('ready')).lower()}": trace
        for trace in traces
    }
    expected_keys = {
        f"mode={trace['inputs']['mode']};ready={str(trace['inputs']['ready']).lower()}"
        for trace in expected
    }
    if set(index) != expected_keys or len(traces) != 4:
        return _result("unknown", ["FINITE_INPUT_COVERAGE_INCOMPLETE"])
    results = []
    for key in sorted(expected_keys):
        results.append(
            evaluate_logical_event_trace(
                index[key],
                source=source,
                trajectory_durations_ns=trajectory_durations_ns,
                identities=identities,
                transfer_observations=(transfer_observations_by_input or {}).get(key, {}),
            )
        )
    for verdict in ("invalid", "violated", "unknown"):
        matches = [result for result in results if result["verdict"] == verdict]
        if matches:
            return _result(verdict, matches[0]["reason_codes"], matches[0].get("witness"))
    if len(results) != 4 or any(result["verdict"] != "verified-within-bounds" for result in results):
        return _result("unknown", ["PROGRAM_LOGICAL_AGGREGATION_INCOMPLETE"])
    result = _result("verified-within-bounds", [])
    result["finite_input_traces_checked"] = 4
    return result
