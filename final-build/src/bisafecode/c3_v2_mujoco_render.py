"""Pure validation helpers for the EXP-S2-034 frozen-keyframe render gate."""

from __future__ import annotations

from collections import Counter
import math
from typing import Any, Mapping, Sequence


def validate_render_spec(specification: Mapping[str, Any]) -> None:
    frames = specification.get("ordered_frames")
    if not isinstance(frames, list) or len(frames) != 5:
        raise ValueError("render specification must contain exactly five ordered frames")
    names = [frame.get("name") for frame in frames]
    times = [frame.get("time_ns") for frame in frames]
    if len(set(names)) != len(names) or any(not isinstance(name, str) or not name for name in names):
        raise ValueError("render frame names must be unique non-empty strings")
    if len(set(times)) != len(times) or any(not isinstance(value, int) or value < 0 for value in times):
        raise ValueError("render frame times must be unique non-negative integers")
    if times != sorted(times):
        raise ValueError("render frames must be in chronological order")
    rendering = specification.get("rendering", {})
    if rendering.get("width") != 640 or rendering.get("height") != 480:
        raise ValueError("render dimensions must remain frozen at 640 by 480")
    if rendering.get("contact_points_enabled") is not True:
        raise ValueError("contact-point visualization must remain enabled")
    execution = specification.get("execution_contract", {})
    if execution.get("oracle_grid_replay_allowed") is not False:
        raise ValueError("render gate must prohibit oracle-grid replay")
    if execution.get("dynamics_stepping_allowed") is not False:
        raise ValueError("render gate must prohibit dynamics stepping")
    if execution.get("paper_result_eligible") is not False:
        raise ValueError("render gate cannot be paper-result eligible")


def validate_frozen_keyframes(
    keyframes: Mapping[str, Any],
    specification: Mapping[str, Any],
) -> None:
    validate_render_spec(specification)
    expected = specification["ordered_frames"]
    expected_names = [frame["name"] for frame in expected]
    if set(keyframes) != set(expected_names):
        raise ValueError("keyframe name set differs from the frozen render specification")
    for frame_contract in expected:
        name = frame_contract["name"]
        frame = keyframes[name]
        if frame.get("time_ns") != frame_contract["time_ns"]:
            raise ValueError(f"keyframe time differs from frozen contract: {name}")
        qpos = frame.get("qpos_by_joint_name")
        if not isinstance(qpos, dict) or len(qpos) != 18:
            raise ValueError(f"keyframe must contain exactly 18 named qpos values: {name}")
        if any(not isinstance(value, (int, float)) or not math.isfinite(value) for value in qpos.values()):
            raise ValueError(f"keyframe contains a non-finite qpos value: {name}")
        contacts = frame.get("contacts")
        if not isinstance(contacts, list):
            raise ValueError(f"keyframe contacts must be a list: {name}")
        negative_cross = sum(row.get("penetrating") is True and row.get("kind") == "cross-arm" for row in contacts)
        negative_world = sum(row.get("penetrating") is True and row.get("kind") == "robot-world" for row in contacts)
        if negative_cross != frame.get("negative_cross_arm_contacts"):
            raise ValueError(f"keyframe cross-arm count is internally inconsistent: {name}")
        if negative_world != frame.get("negative_robot_world_contacts"):
            raise ValueError(f"keyframe robot-world count is internally inconsistent: {name}")
    if keyframes["start"]["negative_cross_arm_contacts"] != 0:
        raise ValueError("start keyframe must be cross-arm contact free")
    if keyframes["end"]["negative_cross_arm_contacts"] != 0:
        raise ValueError("end keyframe must be cross-arm contact free")
    for name in (
        "first_mujoco_cross_arm_witness",
        "fcl_reference_witness",
        "deepest_mujoco_cross_arm_witness",
    ):
        if keyframes[name]["negative_cross_arm_contacts"] < 1:
            raise ValueError(f"interior witness keyframe has no negative cross-arm contact: {name}")


def contact_identity(row: Mapping[str, Any]) -> tuple[str, str, str, bool]:
    return (
        str(row["geom_first"]),
        str(row["geom_second"]),
        str(row["kind"]),
        bool(row["penetrating"]),
    )


def compare_replayed_contacts(
    expected: Sequence[Mapping[str, Any]],
    observed: Sequence[Mapping[str, Any]],
    tolerance_m: float,
) -> Mapping[str, Any]:
    if not math.isfinite(tolerance_m) or tolerance_m < 0.0:
        raise ValueError("contact comparison tolerance must be finite and non-negative")
    expected_counts = Counter(contact_identity(row) for row in expected)
    observed_counts = Counter(contact_identity(row) for row in observed)
    if expected_counts != observed_counts:
        raise ValueError("replayed contact pair/kind/penetration multiset differs from frozen keyframe")
    expected_groups: dict[tuple[str, str, str, bool], list[float]] = {}
    observed_groups: dict[tuple[str, str, str, bool], list[float]] = {}
    for source, destination in ((expected, expected_groups), (observed, observed_groups)):
        for row in source:
            distance = float(row["distance_m"])
            if not math.isfinite(distance):
                raise ValueError("contact comparison received a non-finite distance")
            destination.setdefault(contact_identity(row), []).append(distance)
    maximum_difference = 0.0
    for identity in sorted(expected_groups):
        expected_distances = sorted(expected_groups[identity])
        observed_distances = sorted(observed_groups[identity])
        for expected_distance, observed_distance in zip(expected_distances, observed_distances):
            difference = abs(expected_distance - observed_distance)
            maximum_difference = max(maximum_difference, difference)
            if difference > tolerance_m:
                raise ValueError("replayed contact distance differs from frozen keyframe")
    return {
        "status": "PASS",
        "contact_count": len(observed),
        "unique_contact_identities": len(observed_counts),
        "maximum_absolute_distance_difference_m": maximum_difference,
        "tolerance_m": tolerance_m,
    }
