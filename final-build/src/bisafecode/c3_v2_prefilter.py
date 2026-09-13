"""Deterministic FK-only prefilter for the versioned C3-v2 development family.

This module is intentionally isolated from the frozen EXP-S2-032 C4 pipeline.
It ranks C3-v2 joint trajectories by TCP separation without running FCL or
assigning a collision or oracle label.  The proxy is a development cost
reduction mechanism, not a safety check.
"""

from __future__ import annotations

from dataclasses import replace
from fractions import Fraction
import json
import math
from pathlib import Path
from typing import Any, Dict, Mapping, Optional, Sequence, Tuple
import xml.etree.ElementTree as ET

from .collision_candidate_generation import (
    ArmPath,
    CollisionCandidate,
    CollisionGenerationInputs,
    generate_candidates,
    sample_times,
)


SPEC_SCHEMA = "bisafecode.c3-v2-development-spec/v0.1"
SPEC_STATUS = "FROZEN_BEFORE_C3_V2_FCL"
REPORT_SCHEMA = "bisafecode.c3-v2-fk-prefilter-report/v0.1"
Matrix4 = Tuple[Tuple[float, float, float, float], ...]
Vector3 = Tuple[float, float, float]


def _identity() -> Matrix4:
    return (
        (1.0, 0.0, 0.0, 0.0),
        (0.0, 1.0, 0.0, 0.0),
        (0.0, 0.0, 1.0, 0.0),
        (0.0, 0.0, 0.0, 1.0),
    )


def _multiply(first: Matrix4, second: Matrix4) -> Matrix4:
    return tuple(
        tuple(sum(first[row][index] * second[index][column] for index in range(4)) for column in range(4))
        for row in range(4)
    )


def _translation(vector: Sequence[float]) -> Matrix4:
    x, y, z = vector
    return (
        (1.0, 0.0, 0.0, float(x)),
        (0.0, 1.0, 0.0, float(y)),
        (0.0, 0.0, 1.0, float(z)),
        (0.0, 0.0, 0.0, 1.0),
    )


def _axis_rotation(axis: Sequence[float], angle: float) -> Matrix4:
    norm = math.sqrt(sum(float(value) ** 2 for value in axis))
    if not math.isfinite(norm) or norm <= 0.0 or not math.isfinite(angle):
        raise ValueError("rotation axis and angle must be finite and nonzero")
    x, y, z = (float(value) / norm for value in axis)
    cosine = math.cos(angle)
    sine = math.sin(angle)
    one_minus = 1.0 - cosine
    return (
        (cosine + x * x * one_minus, x * y * one_minus - z * sine, x * z * one_minus + y * sine, 0.0),
        (y * x * one_minus + z * sine, cosine + y * y * one_minus, y * z * one_minus - x * sine, 0.0),
        (z * x * one_minus - y * sine, z * y * one_minus + x * sine, cosine + z * z * one_minus, 0.0),
        (0.0, 0.0, 0.0, 1.0),
    )


def _rpy_rotation(rpy: Sequence[float]) -> Matrix4:
    roll, pitch, yaw = rpy
    return _multiply(
        _multiply(_axis_rotation((0.0, 0.0, 1.0), yaw), _axis_rotation((0.0, 1.0, 0.0), pitch)),
        _axis_rotation((1.0, 0.0, 0.0), roll),
    )


def _numbers(value: Optional[str], default: Vector3) -> Vector3:
    if value is None:
        return default
    result = tuple(float(item) for item in value.split())
    if len(result) != 3 or any(not math.isfinite(item) for item in result):
        raise ValueError("URDF vector must contain three finite numbers")
    return result  # type: ignore[return-value]


def _fraction(value: Any, field: str) -> Fraction:
    if not isinstance(value, list) or len(value) != 2:
        raise ValueError(f"{field} must be [numerator, denominator]")
    numerator, denominator = value
    if any(isinstance(item, bool) or not isinstance(item, int) for item in value):
        raise ValueError(f"{field} must contain integers")
    if numerator < 0 or denominator <= 0:
        raise ValueError(f"{field} must be non-negative")
    return Fraction(numerator, denominator)


def _ceil_quantum(value: Fraction, quantum: int) -> int:
    if quantum <= 0:
        raise ValueError("duration quantum must be positive")
    denominator = value.denominator * quantum
    units = (value.numerator + denominator - 1) // denominator
    return units * quantum


def load_c3_v2_spec(path: Path) -> Mapping[str, Any]:
    record = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(record, Mapping):
        raise ValueError("C3-v2 specification root must be an object")
    if record.get("schema") != SPEC_SCHEMA or record.get("status") != SPEC_STATUS:
        raise ValueError("unsupported or unfrozen C3-v2 specification")
    if record.get("paper_result_eligible") is not False:
        raise ValueError("C3-v2 development cannot be paper-result eligible")
    boundary = record.get("c4_non_interference", {})
    if boundary.get("frozen_experiment") != "EXP-S2-032":
        raise ValueError("C3-v2 must preserve the frozen C4 experiment")
    if boundary.get("separate_output_namespace") != "EXP-S2-033_C3_V2":
        raise ValueError("C3-v2 output namespace is not isolated")
    source = record.get("source", {})
    if source.get("generation_spec") != "03_experiments/canonical_v0_2/collision_candidate_generation_spec.json":
        raise ValueError("C3-v2 source generation specification changed")
    if source.get("base_candidate_id") != "C3-L00-R00-S00-P01":
        raise ValueError("C3-v2 must use the declared shortcut base candidate")
    if source.get("base_path_variant") != "shortcut":
        raise ValueError("C3-v2 base path must be shortcut")
    fractions = record.get("candidate_sweep", {}).get("right_hold_fractions")
    parsed = tuple(_fraction(value, f"right_hold_fractions[{index}]") for index, value in enumerate(fractions or ()))
    if not parsed or len(set(parsed)) != len(parsed) or any(value <= Fraction(1, 5) or value > 1 for value in parsed):
        raise ValueError("C3-v2 hold fractions must be unique and in (0.2, 1]")
    selected = record.get("kinematic_prefilter", {}).get("selected_count_for_fcl")
    if isinstance(selected, bool) or not isinstance(selected, int) or not 0 < selected <= len(parsed):
        raise ValueError("selected_count_for_fcl is invalid")
    selected_order = record.get("kinematic_prefilter", {}).get("selected_candidate_order_for_fcl")
    if selected_order != ["C3V2-RH100", "C3V2-RH050", "C3V2-RH060", "C3V2-RH075"]:
        raise ValueError("C3-v2 selected FCL candidate order changed")
    if len(selected_order) != selected:
        raise ValueError("C3-v2 selected count and frozen order differ")
    predicate = record.get("formal_fcl_construction_predicate", {})
    if predicate.get("scope_id") != "robot-fixture-empty" or predicate.get("first_match_only") is not True:
        raise ValueError("C3-v2 formal predicate scope or first-match rule changed")
    if predicate.get("thresholds_mutable_after_run") is not False:
        raise ValueError("C3-v2 thresholds must remain immutable after FCL")
    if record.get("independent_evaluation_after_candidate_freeze", {}).get("screening_result_is_not_ground_truth") is not True:
        raise ValueError("C3-v2 screening cannot own ground-truth labels")
    return record


def resolved_urdf_bytes(repo_root: Path, inputs: CollisionGenerationInputs) -> bytes:
    """Read the already-bound resolved URDF member without extracting the archive."""

    import tarfile

    source_bundle = inputs.specification["source_bundle"]
    member_names = [
        item["name"]
        for item in source_bundle["members"]
        if item["name"].endswith("openarm_v1_bimanual_resolved.urdf")
    ]
    if len(member_names) != 1:
        raise ValueError("resolved URDF member is not unique")
    with tarfile.open(repo_root / source_bundle["path"], "r:gz") as archive:
        stream = archive.extractfile(member_names[0])
        if stream is None:
            raise ValueError("resolved URDF member cannot be read")
        return stream.read()


class UrdfKinematicModel:
    """Small deterministic URDF FK implementation for the TCP proxy only."""

    def __init__(self, urdf_bytes: bytes):
        root = ET.fromstring(urdf_bytes)
        joints: Dict[str, Mapping[str, Any]] = {}
        child_links = set()
        parent_links = set()
        for item in root.findall("joint"):
            parent_element = item.find("parent")
            child_element = item.find("child")
            if parent_element is None or child_element is None:
                raise ValueError("URDF joint is missing parent or child")
            parent = parent_element.attrib["link"]
            child = child_element.attrib["link"]
            origin = item.find("origin")
            axis = item.find("axis")
            joints[child] = {
                "name": item.attrib["name"],
                "type": item.attrib["type"],
                "parent": parent,
                "xyz": _numbers(origin.get("xyz") if origin is not None else None, (0.0, 0.0, 0.0)),
                "rpy": _numbers(origin.get("rpy") if origin is not None else None, (0.0, 0.0, 0.0)),
                "axis": _numbers(axis.get("xyz") if axis is not None else None, (1.0, 0.0, 0.0)),
            }
            child_links.add(child)
            parent_links.add(parent)
        roots = parent_links - child_links
        if len(roots) != 1:
            raise ValueError("URDF must have exactly one root link")
        self._root_link = next(iter(roots))
        self._joints = joints

    def link_transform(self, link: str, joint_positions: Mapping[str, float]) -> Matrix4:
        chain = []
        current = link
        while current != self._root_link:
            if current not in self._joints:
                raise ValueError(f"link is not connected to the URDF root: {link}")
            joint = self._joints[current]
            chain.append(joint)
            current = joint["parent"]
        result = _identity()
        for joint in reversed(chain):
            result = _multiply(result, _translation(joint["xyz"]))
            result = _multiply(result, _rpy_rotation(joint["rpy"]))
            joint_type = joint["type"]
            value = float(joint_positions.get(joint["name"], 0.0))
            if joint_type in {"revolute", "continuous"}:
                result = _multiply(result, _axis_rotation(joint["axis"], value))
            elif joint_type == "prismatic":
                result = _multiply(result, _translation(tuple(component * value for component in joint["axis"])))
            elif joint_type != "fixed":
                raise ValueError(f"unsupported URDF joint type: {joint_type}")
        return result

    def link_position(self, link: str, joint_positions: Mapping[str, float]) -> Vector3:
        transform = self.link_transform(link, joint_positions)
        return (transform[0][3], transform[1][3], transform[2][3])


def _joint_positions(candidate: CollisionCandidate, time_ns: int, fingers: Mapping[str, float]) -> Dict[str, float]:
    result = dict(fingers)
    result.update(zip(candidate.left.joint_names, candidate.left.configuration_at(time_ns)))
    result.update(zip(candidate.right.joint_names, candidate.right.configuration_at(time_ns)))
    return result


def _distance(first: Vector3, second: Vector3) -> float:
    return math.sqrt(sum((a - b) ** 2 for a, b in zip(first, second)))


def validate_fk_against_keyframes(
    model: UrdfKinematicModel,
    inputs: CollisionGenerationInputs,
    specification: Mapping[str, Any],
) -> Mapping[str, Any]:
    proxy = specification["kinematic_prefilter"]
    tolerance = float(proxy["maximum_tcp_position_error_m"])
    results = {}
    for state_name in proxy["fk_validation_states"]:
        state = inputs.keyframes[state_name]
        arm_results = {}
        for arm in ("left", "right"):
            position = model.link_position(f"openarm_{arm}_hand_tcp", state["qpos_by_joint_name"])
            target = tuple(float(value) for value in state["pose_checks"][arm]["target_m"])
            error = _distance(position, target)  # type: ignore[arg-type]
            if error > tolerance:
                raise ValueError(f"FK validation exceeds tolerance for {state_name}/{arm}: {error}")
            arm_results[arm] = {
                "computed_tcp_m": list(position),
                "target_tcp_m": list(target),
                "position_error_m": error,
            }
        results[state_name] = arm_results
    return results


def generate_c3_v2_candidates(
    inputs: CollisionGenerationInputs,
    specification: Mapping[str, Any],
) -> Tuple[CollisionCandidate, ...]:
    base_id = specification["source"]["base_candidate_id"]
    matches = [candidate for candidate in generate_candidates(inputs) if candidate.candidate_id == base_id]
    if len(matches) != 1:
        raise ValueError("C3-v2 base candidate is not unique")
    base = matches[0]
    if base.case_id != "C3" or base.attached_object_active or base.scope_id != "robot-fixture-empty":
        raise ValueError("C3-v2 base candidate identity is invalid")
    if base.generation_parameters.get("path_variant") != "shortcut":
        raise ValueError("C3-v2 base candidate is not the shortcut path")
    if base.left.start_delay_ns != 0 or base.right.start_delay_ns != 0:
        raise ValueError("C3-v2 base candidate must start without a phase delay")

    reference = max(base.left.motion_duration_ns, base.right.motion_duration_ns)
    quantum = int(inputs.specification["joint_trajectory"]["duration_quantum_ns"])
    candidates = []
    for rank, fraction_record in enumerate(specification["candidate_sweep"]["right_hold_fractions"]):
        fraction = _fraction(fraction_record, f"right_hold_fractions[{rank}]")
        delay_ns = _ceil_quantum(reference * fraction, quantum)
        percent = round(float(fraction) * 100)
        candidate = replace(
            base,
            candidate_id=f"C3V2-RH{percent:03d}",
            rank=rank,
            right=replace(base.right, start_delay_ns=delay_ns),
            generation_parameters={
                "development_lineage": "C3-v2",
                "base_candidate_id": base_id,
                "right_hold_fraction": list(fraction_record),
                "right_start_delay_ns": delay_ns,
                "proxy_only_before_fcl": True,
            },
        )
        candidates.append(candidate)
    if len({candidate.candidate_id for candidate in candidates}) != len(candidates):
        raise ValueError("C3-v2 candidate IDs are not unique")
    return tuple(candidates)


def summarize_tcp_proxy(
    candidate: CollisionCandidate,
    inputs: CollisionGenerationInputs,
    model: UrdfKinematicModel,
) -> Mapping[str, Any]:
    minimum = None
    times = sample_times(candidate, inputs)
    for sample_index, time_ns in enumerate(times):
        positions = _joint_positions(candidate, time_ns, inputs.initial_fingers)
        left = model.link_position("openarm_left_hand_tcp", positions)
        right = model.link_position("openarm_right_hand_tcp", positions)
        distance = _distance(left, right)
        record = (distance, time_ns, sample_index, left, right)
        if minimum is None or record < minimum:
            minimum = record
    if minimum is None:
        raise ValueError("C3-v2 candidate has no sample times")
    return {
        "candidate_id": candidate.candidate_id,
        "generation_rank": candidate.rank,
        "candidate_definition": candidate.as_record(),
        "sample_count": len(times),
        "minimum_tcp_distance_m": minimum[0],
        "minimum_time_ns": minimum[1],
        "minimum_sample_index": minimum[2],
        "left_tcp_m": list(minimum[3]),
        "right_tcp_m": list(minimum[4]),
        "endpoint_tcp_distances_m": [
            _distance(
                model.link_position("openarm_left_hand_tcp", _joint_positions(candidate, time_ns, inputs.initial_fingers)),
                model.link_position("openarm_right_hand_tcp", _joint_positions(candidate, time_ns, inputs.initial_fingers)),
            )
            for time_ns in (times[0], times[-1])
        ],
    }


def build_c3_v2_prefilter_report(
    inputs: CollisionGenerationInputs,
    specification: Mapping[str, Any],
    urdf_bytes: bytes,
) -> Mapping[str, Any]:
    model = UrdfKinematicModel(urdf_bytes)
    validation = validate_fk_against_keyframes(model, inputs, specification)
    candidates = generate_c3_v2_candidates(inputs, specification)
    reports = [summarize_tcp_proxy(candidate, inputs, model) for candidate in candidates]
    ordered = sorted(reports, key=lambda item: (item["minimum_tcp_distance_m"], item["candidate_id"]))
    selected_count = int(specification["kinematic_prefilter"]["selected_count_for_fcl"])
    selected_ids = [item["candidate_id"] for item in ordered[:selected_count]]
    if selected_ids != specification["kinematic_prefilter"]["selected_candidate_order_for_fcl"]:
        raise ValueError("computed TCP ranking differs from the frozen C3-v2 FCL order")
    return {
        "schema": REPORT_SCHEMA,
        "status": "PREPARED_FK_PROXY_ONLY_NOT_FCL_SCREENED",
        "paper_result_eligible": False,
        "c4_modified": False,
        "formal_collision_backend_executed": False,
        "oracle_executed": False,
        "fk_validation": validation,
        "candidate_count": len(candidates),
        "selection_rule": specification["kinematic_prefilter"],
        "proxy_ranked_candidate_ids": [item["candidate_id"] for item in ordered],
        "selected_for_future_fcl": selected_ids,
        "candidates": reports,
        "interpretation": {
            "supported": "Deterministic TCP-distance ranking for the frozen C3-v2 development family.",
            "unsupported": [
                "collision or safety verdict",
                "continuous certificate",
                "independent oracle label",
                "complete C3 result",
                "paper performance result",
            ],
        },
    }
