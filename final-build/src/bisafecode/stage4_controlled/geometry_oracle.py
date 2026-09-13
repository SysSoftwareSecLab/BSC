"""Independent analytic continuous-geometry oracle for EXP-S4-002.

FREEZE_ONLY_NOT_FORMAL_STAGE4_EVIDENCE

Required coverage is derived here from frozen scene pair policy and validated
move/action intervals.  A caller cannot declare its own coverage set.  The
producer binds every trajectory artifact and sidecar and records exact input
identities before the adjudicator can return a verdict.
"""

from __future__ import annotations

import hashlib
import json
import math
from pathlib import Path
from typing import Any, Dict, Mapping, Sequence, Tuple

from . import FREEZE_STATUS
from .identity import CONTRACT_DIR, expected_geometry_oracle_identities, repository_root, sha256_file


NUMERIC_ERROR_BOUND_M = 1e-12
MAX_QUERY_COUNT = 200_000
MODEL_STEP_NS = 1_000_000


def signed_sphere_distance_m(
    left_center: Sequence[float], left_radius_m: float, right_center: Sequence[float], right_radius_m: float
) -> float:
    if len(left_center) != 3 or len(right_center) != 3:
        raise ValueError("sphere centers require three coordinates")
    if left_radius_m < 0 or right_radius_m < 0:
        raise ValueError("sphere radii must be nonnegative")
    squared = sum((float(a) - float(b)) ** 2 for a, b in zip(left_center, right_center))
    if not math.isfinite(squared):
        raise ValueError("non-finite sphere coordinates")
    return math.sqrt(squared) - left_radius_m - right_radius_m


def _result(verdict: str, reasons: Sequence[str], witness=None):
    return {
        "evidence_status": FREEZE_STATUS,
        "schema": "bisafecode.stage4.controlled.geometry-oracle-result/v2",
        "verdict": verdict,
        "reason_codes": list(reasons),
        "witness": witness,
    }


def _load_json(path: Path) -> Mapping[str, Any]:
    return json.loads(path.read_text(encoding="utf-8"))


def _canonical_sha256(value: Any) -> str:
    return hashlib.sha256(
        json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=True).encode("ascii")
    ).hexdigest()


def _frozen_assets(root: Path) -> Mapping[str, Any]:
    base = root / CONTRACT_DIR / "assets"
    model = _load_json(base / "model.json")
    scene = _load_json(base / "scene.json")
    trajectories = {}
    for path in sorted((base / "trajectories").glob("*.json")):
        if path.name.endswith(".sidecar.json"):
            continue
        document = _load_json(path)
        sidecar_path = path.with_name(path.stem + ".sidecar.json")
        sidecar = _load_json(sidecar_path)
        artifact_sha = sha256_file(path)
        if sidecar.get("trajectory_artifact_sha256") != artifact_sha:
            raise ValueError("trajectory sidecar artifact identity mismatch")
        trajectories[str(sidecar.get("trajectory_content_sha256"))] = {
            "artifact": document,
            "artifact_sha256": artifact_sha,
            "sidecar": sidecar,
            "sidecar_sha256": sha256_file(sidecar_path),
        }
    if len(trajectories) != 4 or any(len(key) != 64 for key in trajectories):
        raise ValueError("frozen trajectory content identities incomplete")
    return {"model": model, "scene": scene, "trajectories": trajectories}


def _complete_action_pairs(trace: Mapping[str, Any]) -> Tuple[Mapping[str, Any], ...]:
    if trace.get("complete") is not True or not isinstance(trace.get("events"), list):
        raise ValueError("trace is incomplete")
    events = trace["events"]
    if not events or events[-1].get("event_kind") != "terminal":
        raise ValueError("terminal trace record missing")
    starts: Dict[int, Mapping[str, Any]] = {}
    ends: Dict[int, Mapping[str, Any]] = {}
    for event in events:
        ordinal = event.get("action_execution_ordinal")
        if event.get("event_kind") == "action_start":
            if not isinstance(ordinal, int) or ordinal in starts:
                raise ValueError("duplicate or invalid action start")
            starts[ordinal] = event
        elif event.get("event_kind") == "action_end":
            if not isinstance(ordinal, int) or ordinal in ends:
                raise ValueError("duplicate or invalid action end")
            ends[ordinal] = event
    expected = set(range(int(trace.get("source_action_count", -1))))
    if set(starts) != expected or set(ends) != expected:
        raise ValueError("action coverage incomplete")
    pairs = []
    for ordinal in sorted(expected):
        start, end = starts[ordinal], ends[ordinal]
        binding = ("action_kind", "canonical_action_ordinal", "source_span", "arm", "object_id", "resource_id", "trajectory_hash")
        if any(start.get(key) != end.get(key) for key in binding):
            raise ValueError("action start/end binding mismatch")
        if start.get("time_ns") > end.get("time_ns"):
            raise ValueError("negative action interval")
        pairs.append({"ordinal": ordinal, "start": start, "end": end})
    return tuple(pairs)


def validate_trajectory_start_bindings(
    trace: Mapping[str, Any], *, root: Path | None = None
) -> Mapping[str, Any]:
    """Independently replay move endpoints and validate every move start.

    This implementation consumes only the source-event trace and frozen
    trajectory artifacts.  It does not import or inspect the verifier's
    successor relation or verdict.
    """

    root = (root or repository_root()).resolve()
    try:
        assets = _frozen_assets(root)
        _complete_action_pairs(trace)
    except (ValueError, TypeError, KeyError) as error:
        return _result(
            "invalid",
            ["GEOMETRY_SOURCE_TRACE_INVALID"],
            {"error": type(error).__name__},
        )
    current_q: Dict[str, Tuple[float, ...]] = {
        "left": (0.0,) * 7,
        "right": (0.0,) * 7,
    }
    active: Dict[int, Tuple[str, Mapping[str, Any]]] = {}
    for event in trace["events"]:
        if event.get("action_kind") != "move":
            continue
        kind = event.get("event_kind")
        ordinal = event.get("action_execution_ordinal")
        trajectory_hash = str(event.get("trajectory_hash"))
        entry = assets["trajectories"].get(trajectory_hash)
        arm = event.get("arm")
        if (
            not isinstance(ordinal, int)
            or arm not in current_q
            or entry is None
            or entry["artifact"].get("arm") != arm
        ):
            return _result("invalid", ["TRAJECTORY_BINDING_INVALID"], event)
        if kind == "action_start":
            if any(value[0] == arm for value in active.values()):
                return _result("invalid", ["TRAJECTORY_EXECUTION_OVERLAP"], event)
            expected = tuple(
                float(value) for value in entry["artifact"]["points"][0]["positions"]
            )
            observed = current_q[str(arm)]
            if len(expected) != len(observed) or any(
                abs(left - right) > 1e-12
                for left, right in zip(observed, expected)
            ):
                return _result(
                    "invalid",
                    ["TRAJECTORY_START_BINDING_MISMATCH"],
                    {
                        "action_execution_ordinal": ordinal,
                        "arm": arm,
                        "trajectory_hash": trajectory_hash,
                        "observed_current_q": list(observed),
                        "required_start_q": list(expected),
                    },
                )
            active[ordinal] = (str(arm), entry)
        elif kind == "action_end":
            bound = active.pop(ordinal, None)
            if bound is None or bound[0] != arm or bound[1] is not entry:
                return _result("invalid", ["TRAJECTORY_END_BINDING_MISMATCH"], event)
            current_q[str(arm)] = tuple(
                float(value) for value in entry["artifact"]["points"][-1]["positions"]
            )
    if active:
        return _result("invalid", ["TRAJECTORY_END_BINDING_MISSING"])
    result = _result("verified-within-bounds", [])
    result["move_start_bindings_checked"] = sum(
        event.get("event_kind") == "action_start"
        and event.get("action_kind") == "move"
        for event in trace["events"]
    )
    return result


def derive_required_pair_intervals(
    trace: Mapping[str, Any], *, root: Path | None = None
) -> Tuple[Mapping[str, Any], ...]:
    """Derive coverage solely from frozen scene policy and source move intervals."""

    root = (root or repository_root()).resolve()
    assets = _frozen_assets(root)
    pairs = tuple(tuple(value) for value in assets["scene"]["pair_policy"]["checked_pairs"])
    if not pairs:
        raise ValueError("frozen checked-pair policy is empty")
    required = []
    for action in _complete_action_pairs(trace):
        start, end = action["start"], action["end"]
        if start.get("action_kind") != "move":
            continue
        trajectory_hash = start.get("trajectory_hash")
        if trajectory_hash not in assets["trajectories"]:
            raise ValueError("move trajectory is not a frozen content identity")
        start_ns, end_ns = int(start["time_ns"]), int(end["time_ns"])
        if end_ns <= start_ns:
            raise ValueError("move interval is empty")
        cursor = start_ns
        while cursor < end_ns:
            interval_end = min(end_ns, cursor + MODEL_STEP_NS)
            for left, right in pairs:
                required.append(
                    {
                        "pair_id": f"{left}|{right}",
                        "start_ns": cursor,
                        "end_ns": interval_end,
                        "move_action_execution_ordinal": action["ordinal"],
                        "trajectory_hash": trajectory_hash,
                    }
                )
            cursor = interval_end
    return tuple(required)


def _trajectory_q(entry: Mapping[str, Any], local_ns: int) -> Tuple[float, ...]:
    points = entry["artifact"]["points"]
    duration = int(points[-1]["time_ns"])
    if not 0 <= local_ns <= duration:
        raise ValueError("trajectory query out of range")
    for start, end in zip(points, points[1:]):
        start_ns, end_ns = int(start["time_ns"]), int(end["time_ns"])
        if local_ns > end_ns:
            continue
        width_ns = end_ns - start_ns
        if width_ns <= 0:
            raise ValueError("trajectory points are not strictly ordered")
        u = (local_ns - start_ns) / width_ns
        # Frozen assets use cubic Hermite interpolation.  The controlled
        # trajectories currently pin every waypoint velocity to zero, so the
        # segment reduces to the standard smoothstep polynomial.
        if any(float(value) != 0.0 for value in (*start["velocities"], *end["velocities"])):
            raise ValueError("analytic oracle requires zero-velocity controlled waypoints")
        blend = 3.0 * u * u - 2.0 * u * u * u
        return tuple(
            float(a) + (float(b) - float(a)) * blend
            for a, b in zip(start["positions"], end["positions"])
        )
    raise ValueError("trajectory query was not covered by a segment")


def _trajectory_speed_bound(entry: Mapping[str, Any]) -> float:
    points = entry["artifact"]["points"]
    per_joint = [0.0, 0.0, 0.0]
    for start, end in zip(points, points[1:]):
        duration_s = (int(end["time_ns"]) - int(start["time_ns"])) * 1e-9
        if duration_s <= 0:
            raise ValueError("trajectory segment duration must be positive")
        for index, (left, right) in enumerate(
            zip(start["positions"][:3], end["positions"][:3])
        ):
            per_joint[index] = max(
                per_joint[index], 1.5 * abs(float(right) - float(left)) / duration_s
            )
    return 0.2 * math.sqrt(sum(value * value for value in per_joint))


def _tool_center(arm: str, q: Sequence[float]) -> Tuple[float, float, float]:
    x0 = -0.45 if arm == "left" else 0.45
    return (x0 + 0.20 * q[0], 0.30 + 0.20 * q[1], 0.45 + 0.20 * q[2])


def _state_at(trace: Mapping[str, Any], time_ns: int, assets: Mapping[str, Any]) -> Mapping[str, Any]:
    q = {"left": (0.0,) * 7, "right": (0.0,) * 7}
    speed = {"left": 0.0, "right": 0.0}
    attachments: Dict[str, set] = {}
    authority: Dict[str, str] = {}
    for event in trace["events"]:
        if int(event.get("time_ns", 0)) > time_ns:
            break
        kind = event.get("event_kind")
        if kind == "action_end" and event.get("action_kind") == "move":
            arm = str(event["arm"])
            entry = assets["trajectories"][str(event["trajectory_hash"])]
            q[arm] = _trajectory_q(entry, int(entry["artifact"]["points"][-1]["time_ns"]))
        elif kind == "close":
            attachments.setdefault(str(event["object_id"]), set()).add(str(event["arm"]))
            authority.setdefault(str(event["object_id"]), str(event["arm"]))
        elif kind == "open":
            attachments.setdefault(str(event["object_id"]), set()).discard(str(event["arm"]))
        elif kind == "transfer_authority":
            authority[str(event["object_id"])] = str(event["receiver"])
    for action in _complete_action_pairs(trace):
        start, end = action["start"], action["end"]
        if start.get("action_kind") != "move" or not int(start["time_ns"]) <= time_ns < int(end["time_ns"]):
            continue
        arm = str(start["arm"])
        entry = assets["trajectories"][str(start["trajectory_hash"])]
        q[arm] = _trajectory_q(entry, time_ns - int(start["time_ns"]))
        speed[arm] = _trajectory_speed_bound(entry)
    centers = {f"{arm}_tool_sphere": _tool_center(arm, q[arm]) for arm in ("left", "right")}
    initial = assets["scene"].get("initial_object_centers_m", {})
    for object_id in ("payload_alpha", "payload_beta"):
        owners = attachments.get(object_id, set())
        owner = authority.get(object_id)
        if owner in owners:
            centers[object_id] = centers[f"{owner}_tool_sphere"]
        elif object_id in initial:
            centers[object_id] = tuple(float(value) for value in initial[object_id])
        else:
            raise ValueError("object pose is unavailable")
    return {"centers": centers, "speed": speed, "attachments": attachments, "authority": authority}


def produce_geometry_evidence(
    trace: Mapping[str, Any], *, root: Path | None = None
) -> Mapping[str, Any]:
    """Execute the frozen analytic producer on one complete source trace."""

    root = (root or repository_root()).resolve()
    assets = _frozen_assets(root)
    identities = expected_geometry_oracle_identities(root)
    trajectory_binding = validate_trajectory_start_bindings(trace, root=root)
    if trajectory_binding["verdict"] != "verified-within-bounds":
        return {
            "evidence_status": FREEZE_STATUS,
            "schema": "bisafecode.stage4.controlled.geometry-production/v3",
            "producer_executed": True,
            "identities": identities,
            "trace_source_sha256": trace.get("source_sha256"),
            "trajectory_binding_validation": trajectory_binding,
            "coverage_derivation_root_sha256": None,
            "required_interval_count": 0,
            "intervals": [],
            "transfer_observations": {},
        }
    required = derive_required_pair_intervals(trace, root=root)
    radii = {item["entity_id"]: float(item["radius_m"]) for item in assets["model"]["semantics"]["entities"]}
    intervals = []
    for item in required:
        left, right = str(item["pair_id"]).split("|", 1)
        start_state = _state_at(trace, int(item["start_ns"]), assets)
        end_state = _state_at(trace, int(item["end_ns"]), assets)
        attached_contact = any(
            object_id in {left, right}
            and f"{arm}_tool_sphere" in {left, right}
            and arm in start_state["attachments"].get(object_id, set())
            for object_id in ("payload_alpha", "payload_beta")
            for arm in ("left", "right")
        )
        entity_speed = {
            "left_tool_sphere": start_state["speed"]["left"],
            "right_tool_sphere": start_state["speed"]["right"],
            "payload_alpha": start_state["speed"].get(start_state["authority"].get("payload_alpha", ""), 0.0),
            "payload_beta": start_state["speed"].get(start_state["authority"].get("payload_beta", ""), 0.0),
        }
        intervals.append(
            {
                **item,
                "allowed_attachment_contact": attached_contact,
                "start_signed_distance_m": signed_sphere_distance_m(start_state["centers"][left], radii[left], start_state["centers"][right], radii[right]),
                "end_signed_distance_m": signed_sphere_distance_m(end_state["centers"][left], radii[left], end_state["centers"][right], radii[right]),
                "relative_speed_upper_m_s": entity_speed[left] + entity_speed[right],
            }
        )
    transfers = {}
    zone = assets["scene"]["handover_zone"]
    for event in trace["events"]:
        if event.get("event_kind") != "transfer_authority":
            continue
        state = _state_at(trace, int(event["time_ns"]), assets)
        left = state["centers"]["left_tool_sphere"]
        right = state["centers"]["right_tool_sphere"]
        inside = all(
            float(zone["min_xyz_m"][index]) <= value <= float(zone["max_xyz_m"][index])
            for center in (left, right)
            for index, value in enumerate(center)
        )
        object_id = str(event["object_id"])
        transfers[str(event["action_execution_ordinal"])] = {
            "producer_executed": True,
            "inside_handover_zone": inside,
            "both_arms_stationary": not state["speed"]["left"] and not state["speed"]["right"],
            "dual_grasp_consistent": state["attachments"].get(object_id, set()) == {"left", "right"}
            and math.dist(left, right) <= 0.18,
        }
    return {
        "evidence_status": FREEZE_STATUS,
        "schema": "bisafecode.stage4.controlled.geometry-production/v3",
        "producer_executed": True,
        "identities": identities,
        "trace_source_sha256": trace.get("source_sha256"),
        "trajectory_binding_validation": trajectory_binding,
        "coverage_derivation_root_sha256": _canonical_sha256(required),
        "required_interval_count": len(required),
        "intervals": intervals,
        "transfer_observations": transfers,
    }


def adjudicate_interval_evidence(
    evidence: Mapping[str, Any],
    *,
    trace: Mapping[str, Any],
    identities: Mapping[str, str],
    root: Path | None = None,
) -> Mapping[str, Any]:
    """Recompute expected coverage, validate exact identities, then adjudicate."""

    root = (root or repository_root()).resolve()
    expected_identities = expected_geometry_oracle_identities(root)
    if dict(identities) != dict(expected_identities) or evidence.get("identities") != dict(expected_identities):
        return _result("invalid", ["GEOMETRY_IDENTITY_MISMATCH"])
    if evidence.get("producer_executed") is not True:
        return _result("unknown", ["GEOMETRY_PRODUCER_NOT_EXECUTED"])
    trajectory_binding = validate_trajectory_start_bindings(trace, root=root)
    if trajectory_binding["verdict"] != "verified-within-bounds":
        return trajectory_binding
    if evidence.get("trajectory_binding_validation") != trajectory_binding:
        return _result("invalid", ["TRAJECTORY_BINDING_EVIDENCE_MISMATCH"])
    try:
        required = derive_required_pair_intervals(trace, root=root)
    except (ValueError, TypeError, KeyError) as error:
        return _result("invalid", ["GEOMETRY_SOURCE_TRACE_INVALID"], {"error": type(error).__name__})
    if evidence.get("coverage_derivation_root_sha256") != _canonical_sha256(required):
        return _result("invalid", ["GEOMETRY_COVERAGE_IDENTITY_MISMATCH"])
    intervals = evidence.get("intervals")
    if not isinstance(intervals, list):
        return _result("unknown", ["GEOMETRY_COVERAGE_INCOMPLETE"])
    if not required:
        if intervals or evidence.get("required_interval_count") != 0:
            return _result("invalid", ["GEOMETRY_COVERAGE_MALFORMED"])
        return _result("unknown", ["NO_GEOMETRY_RELEVANT_MOVE_INTERVALS"])
    key_fields = ("pair_id", "start_ns", "end_ns", "move_action_execution_ordinal", "trajectory_hash")
    expected_keys = [tuple(item[key] for key in key_fields) for item in required]
    observed_keys = [tuple(item.get(key) for key in key_fields) for item in intervals if isinstance(item, dict)]
    if observed_keys != expected_keys or len(intervals) != len(required) or evidence.get("required_interval_count") != len(required):
        return _result("unknown", ["GEOMETRY_COVERAGE_INCOMPLETE"])
    if len(intervals) > MAX_QUERY_COUNT:
        return _result("unknown", ["GEOMETRY_RESOURCE_TRUNCATED"])
    unresolved = []
    for interval in intervals:
        values = tuple(interval.get(key) for key in ("start_ns", "end_ns", "start_signed_distance_m", "end_signed_distance_m", "relative_speed_upper_m_s"))
        if any(isinstance(value, bool) or not isinstance(value, (int, float)) for value in values):
            return _result("invalid", ["GEOMETRY_EVIDENCE_MALFORMED"])
        start_ns, end_ns, d_start, d_end, speed = values
        if end_ns <= start_ns or speed < 0 or not all(math.isfinite(float(value)) for value in values):
            return _result("invalid", ["GEOMETRY_EVIDENCE_MALFORMED"])
        if interval.get("allowed_attachment_contact") is True:
            continue
        if d_start + NUMERIC_ERROR_BOUND_M <= 0 or d_end + NUMERIC_ERROR_BOUND_M <= 0:
            return _result("violated", ["GEOMETRY_VIOLATION_WITNESS"], interval)
        conservative_lower = min(d_start, d_end) - speed * ((end_ns - start_ns) * 1e-9) - NUMERIC_ERROR_BOUND_M
        if conservative_lower <= 0:
            unresolved.append({"pair_id": interval.get("pair_id"), "start_ns": start_ns, "end_ns": end_ns, "conservative_lower_m": conservative_lower})
    if unresolved:
        return _result("unknown", ["GEOMETRY_INTERVAL_UNRESOLVED"], {"intervals": unresolved})
    return _result("verified-within-bounds", [])
