"""Deterministic, pre-execution generation of C1--C4 screening candidates.

This module creates joint-state inputs only.  It does not call FCL, assign an
oracle label, or claim a continuous collision result.  All degrees of freedom
come from a content-addressed generation specification committed before the
Ubuntu screening run.
"""

from __future__ import annotations

import csv
from functools import lru_cache
import hashlib
import io
import json
import math
from dataclasses import dataclass, replace
from fractions import Fraction
from pathlib import Path
import tarfile
from typing import Any, Dict, Iterable, Mapping, Optional, Sequence, Tuple
import xml.etree.ElementTree as ET

from .collision_case_protocol import load_collision_case_selection_protocol


SPEC_SCHEMA = "bisafecode.collision-candidate-generation-spec/v0.2"
SPEC_STATUS = "FROZEN_BEFORE_SCREENING"
MANIFEST_SCHEMA = "bisafecode.collision-candidate-batch/v0.2"
ARM_NAMES = ("left", "right")
JOINTS_PER_ARM = 7


def sha256_bytes(value: bytes) -> str:
    return hashlib.sha256(value).hexdigest()


def sha256_path(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def canonical_json_bytes(value: Mapping[str, Any]) -> bytes:
    return (
        json.dumps(
            value,
            sort_keys=True,
            separators=(",", ":"),
            ensure_ascii=True,
            allow_nan=False,
        )
        + "\n"
    ).encode("ascii")


def _require_sha256(value: Any, field: str) -> str:
    if not isinstance(value, str) or len(value) != 64:
        raise ValueError(f"{field} must be a lowercase SHA-256 digest")
    if any(character not in "0123456789abcdef" for character in value):
        raise ValueError(f"{field} must be a lowercase SHA-256 digest")
    return value


def _require_positive_int(value: Any, field: str) -> int:
    if isinstance(value, bool) or not isinstance(value, int) or value <= 0:
        raise ValueError(f"{field} must be a positive integer")
    return value


def _fraction(value: Sequence[int], field: str) -> Fraction:
    if not isinstance(value, list) or len(value) != 2:
        raise ValueError(f"{field} must be [numerator, denominator]")
    numerator, denominator = value
    if any(isinstance(item, bool) or not isinstance(item, int) for item in value):
        raise ValueError(f"{field} must contain integers")
    if numerator < 0 or denominator <= 0:
        raise ValueError(f"{field} must be a non-negative rational")
    return Fraction(numerator, denominator)


@lru_cache(maxsize=262144)
def minimum_jerk_blend_exact(numerator: int, denominator: int) -> Fraction:
    """Evaluate 10u^3 - 15u^4 + 6u^5 without intermediate floating point.

    Integer nanoseconds define ``u`` exactly.  Returning a ``Fraction`` makes
    the arithmetic rule explicit and prevents architecture-dependent fused or
    extended-precision evaluation from changing a generated state table.
    """

    if isinstance(numerator, bool) or isinstance(denominator, bool):
        raise ValueError("minimum-jerk time ratio must use integers")
    if not isinstance(numerator, int) or not isinstance(denominator, int):
        raise ValueError("minimum-jerk time ratio must use integers")
    if denominator <= 0 or numerator < 0 or numerator > denominator:
        raise ValueError("minimum-jerk time ratio must satisfy 0 <= numerator <= denominator")
    u = Fraction(numerator, denominator)
    return 10 * u**3 - 15 * u**4 + 6 * u**5


def round_fraction_to_binary64(value: Fraction) -> float:
    """Round an exact rational once to the nearest IEEE-754 binary64 value."""

    result = float(value)
    if not math.isfinite(result):
        raise ValueError("exact trajectory evaluation overflowed binary64")
    return result


def minimum_jerk_interpolate_binary64(
    first: float,
    second: float,
    numerator: int,
    denominator: int,
) -> float:
    """Interpolate exact binary64 endpoints and round only the final value."""

    if not math.isfinite(first) or not math.isfinite(second):
        raise ValueError("minimum-jerk endpoints must be finite")
    blend = minimum_jerk_blend_exact(numerator, denominator)
    return _interpolate_binary64_with_exact_blend(first, second, blend)


def _interpolate_binary64_with_exact_blend(
    first: float,
    second: float,
    blend: Fraction,
) -> float:
    first_exact = Fraction.from_float(first)
    second_exact = Fraction.from_float(second)
    return round_fraction_to_binary64(first_exact + (second_exact - first_exact) * blend)


def canonical_binary64_decimal(value: float) -> str:
    """Serialize a finite binary64 with one stable, round-trippable spelling."""

    if not math.isfinite(value):
        raise ValueError("state tables cannot contain non-finite binary64 values")
    return format(value, ".17g")


def exact_affine_binary64(base: float, increment: float, multiplier: int) -> float:
    """Apply the C1 endpoint perturbation exactly, then round once."""

    if isinstance(multiplier, bool) or not isinstance(multiplier, int):
        raise ValueError("affine multiplier must be an integer")
    exact = Fraction.from_float(base) + Fraction.from_float(increment) * multiplier
    return round_fraction_to_binary64(exact)


@dataclass(frozen=True)
class JointLimit:
    lower: float
    upper: float
    velocity: float

    def __post_init__(self) -> None:
        if not all(math.isfinite(value) for value in (self.lower, self.upper, self.velocity)):
            raise ValueError("joint limits must be finite")
        if self.lower >= self.upper or self.velocity <= 0.0:
            raise ValueError("joint limits or velocity are invalid")


@dataclass(frozen=True)
class ArmPath:
    arm: str
    joint_names: Tuple[str, ...]
    waypoints: Tuple[Tuple[float, ...], ...]
    segment_durations_ns: Tuple[int, ...]
    start_delay_ns: int = 0

    def __post_init__(self) -> None:
        if self.arm not in ARM_NAMES:
            raise ValueError("unknown arm")
        if len(self.joint_names) != JOINTS_PER_ARM or len(set(self.joint_names)) != JOINTS_PER_ARM:
            raise ValueError("arm path requires seven unique joint names")
        if len(self.waypoints) < 2 or len(self.segment_durations_ns) != len(self.waypoints) - 1:
            raise ValueError("arm path waypoint/duration mismatch")
        if isinstance(self.start_delay_ns, bool) or self.start_delay_ns < 0:
            raise ValueError("arm path delay must be non-negative")
        for waypoint in self.waypoints:
            if len(waypoint) != JOINTS_PER_ARM or any(not math.isfinite(value) for value in waypoint):
                raise ValueError("arm waypoint must contain seven finite values")
        if any(isinstance(value, bool) or value <= 0 for value in self.segment_durations_ns):
            raise ValueError("segment durations must be positive integers")

    @property
    def motion_duration_ns(self) -> int:
        return sum(self.segment_durations_ns)

    @property
    def global_end_ns(self) -> int:
        return self.start_delay_ns + self.motion_duration_ns

    @property
    def global_boundaries_ns(self) -> Tuple[int, ...]:
        values = [self.start_delay_ns]
        current = self.start_delay_ns
        for duration in self.segment_durations_ns:
            current += duration
            values.append(current)
        return tuple(values)

    @lru_cache(maxsize=262144)
    def configuration_at(self, global_time_ns: int) -> Tuple[float, ...]:
        if global_time_ns <= self.start_delay_ns:
            return self.waypoints[0]
        local = global_time_ns - self.start_delay_ns
        if local >= self.motion_duration_ns:
            return self.waypoints[-1]
        elapsed = 0
        for index, duration in enumerate(self.segment_durations_ns):
            if local <= elapsed + duration:
                numerator = local - elapsed
                first = self.waypoints[index]
                second = self.waypoints[index + 1]
                blend = minimum_jerk_blend_exact(numerator, duration)
                return tuple(
                    _interpolate_binary64_with_exact_blend(a, b, blend)
                    for a, b in zip(first, second)
                )
            elapsed += duration
        raise AssertionError("unreachable arm path time")

    def as_record(self) -> dict:
        return {
            "arm": self.arm,
            "joint_names": list(self.joint_names),
            "waypoints_float_hex": [[value.hex() for value in row] for row in self.waypoints],
            "segment_durations_ns": list(self.segment_durations_ns),
            "start_delay_ns": self.start_delay_ns,
            "interpolation": "quintic-minimum-jerk",
            "endpoint_velocity_rad_s": 0.0,
            "endpoint_acceleration_rad_s2": 0.0,
        }

    @property
    def content_hash(self) -> str:
        return sha256_bytes(canonical_json_bytes(self.as_record()))


@dataclass(frozen=True)
class CollisionCandidate:
    case_id: str
    candidate_id: str
    rank: int
    scope_id: str
    attached_object_active: bool
    left: ArmPath
    right: ArmPath
    generation_parameters: Mapping[str, Any]
    structural_status: str
    structural_reasons: Tuple[str, ...] = ()
    matched_kinematic_candidate_id: Optional[str] = None

    @property
    def global_duration_ns(self) -> int:
        return max(self.left.global_end_ns, self.right.global_end_ns)

    @property
    def is_screenable_without_attachment_transform(self) -> bool:
        return self.structural_status == "READY" and not self.attached_object_active

    def as_record(self) -> dict:
        record = {
            "case_id": self.case_id,
            "candidate_id": self.candidate_id,
            "rank": self.rank,
            "scope_id": self.scope_id,
            "attached_object_active": self.attached_object_active,
            "left_trajectory": self.left.as_record(),
            "left_trajectory_sha256": self.left.content_hash,
            "right_trajectory": self.right.as_record(),
            "right_trajectory_sha256": self.right.content_hash,
            "global_duration_ns": self.global_duration_ns,
            "generation_parameters": dict(self.generation_parameters),
            "structural_status": self.structural_status,
            "structural_reasons": list(self.structural_reasons),
            "requires_attachment_transform": self.attached_object_active,
            "screening_status": "NOT_EXECUTED",
            "paper_result_eligible": False,
        }
        if self.matched_kinematic_candidate_id is not None:
            record["matched_kinematic_candidate_id"] = self.matched_kinematic_candidate_id
        return record

    @property
    def content_hash(self) -> str:
        return sha256_bytes(canonical_json_bytes(self.as_record()))


@dataclass(frozen=True)
class CollisionGenerationInputs:
    specification: Mapping[str, Any]
    specification_file_sha256: str
    specification_content_sha256: str
    protocol_content_sha256: str
    keyframes: Mapping[str, Any]
    shortcut_paths: Mapping[str, Any]
    joint_limits: Mapping[str, JointLimit]
    initial_fingers: Mapping[str, float]
    source_member_hashes: Mapping[str, str]


def validate_generation_spec(record: Mapping[str, Any]) -> None:
    if record.get("schema") != SPEC_SCHEMA or record.get("status") != SPEC_STATUS:
        raise ValueError("unsupported or already-executed generation specification")
    if record.get("paper_result_eligible") is not False:
        raise ValueError("candidate generation is not a paper result")
    if record.get("case_order") != ["C1", "C2", "C3", "C4"]:
        raise ValueError("case order must be C1, C2, C3, C4")

    counts = record.get("candidate_counts")
    families = record.get("families")
    if not isinstance(counts, Mapping) or not isinstance(families, Mapping):
        raise ValueError("candidate counts and families are required")
    if set(families) != {"C1", "C2", "C3", "C4"}:
        raise ValueError("generation spec must define exactly C1--C4")
    c1_count = len(families["C1"]["amplitudes_rad"]) * len(families["C1"]["direction_templates"])
    c2_count = c1_count
    c3_count = (
        len(families["C3"]["phase_delays"])
        * len(families["C3"]["duration_scales"])
        * len(families["C3"]["path_variants"])
    )
    c4_count = (
        len(families["C4"]["right_guard_segments"])
        * len(families["C4"]["phase_delays"])
        * len(families["C4"]["duration_scales"])
    )
    expected = {"C1": c1_count, "C2": c2_count, "C3": c3_count, "C4": c4_count}
    if any(counts.get(case_id) != value for case_id, value in expected.items()):
        raise ValueError("declared per-case candidate counts do not match grids")
    if counts.get("total") != sum(expected.values()):
        raise ValueError("declared total candidate count does not match grids")
    if families["C2"].get("kinematics_identical_to") != "C1":
        raise ValueError("C2 must be kinematically matched to C1")
    if families["C4"].get("matched_ablation_scope") != "robot-fixture-empty":
        raise ValueError("C4 must declare the empty-scope matched ablation")

    attachment = record.get("attachment", {})
    if attachment.get("body_id") != "carried_base":
        raise ValueError("the frozen carried object must be carried_base")
    if attachment.get("reference_state") != "left_operating":
        raise ValueError("the attachment reference state must be left_operating")
    if attachment.get("numeric_transform_status_before_ubuntu_fk") != "BLOCKED":
        raise ValueError("numeric attachment transform must remain blocked before Ubuntu FK")

    trajectory = record.get("joint_trajectory", {})
    if trajectory.get("interpolation") != "quintic-minimum-jerk":
        raise ValueError("only the frozen quintic generator is supported")
    if trajectory.get("endpoint_velocity_rad_s") != 0.0 or trajectory.get("endpoint_acceleration_rad_s2") != 0.0:
        raise ValueError("candidate endpoint derivatives must be zero")
    for field in ("minimum_segment_duration_ns", "duration_quantum_ns"):
        _require_positive_int(trajectory.get(field), field)
    if trajectory.get("phase_delay_reference") != "maximum scaled left/right path duration before delays":
        raise ValueError("phase-delay reference is not frozen")
    deterministic_fields = {
        "evaluation_arithmetic": "exact-rational-from-binary64-waypoints-and-integer-nanoseconds",
        "intermediate_rounding": "none",
        "final_rounding": "once-to-ieee-754-binary64-roundTiesToEven",
        "state_table_numeric_serialization": "finite-binary64-as-ascii-format-.17g",
    }
    for field, expected_value in deterministic_fields.items():
        if trajectory.get(field) != expected_value:
            raise ValueError(f"deterministic trajectory field is not frozen: {field}")

    sampling = record.get("sampling", {})
    _require_positive_int(sampling.get("maximum_period_ns"), "maximum_period_ns")
    increment = sampling.get("maximum_adjacent_joint_increment_rad")
    if isinstance(increment, bool) or not isinstance(increment, (int, float)) or increment <= 0.0:
        raise ValueError("maximum joint increment must be positive")
    if sampling.get("screening_is_continuous_certificate") is not False:
        raise ValueError("sampled generation cannot be called a continuous certificate")

    for case_id in ("C3", "C4"):
        for index, item in enumerate(families[case_id]["phase_delays"]):
            for arm in ARM_NAMES:
                value = _fraction(item[arm], f"{case_id}.phase[{index}].{arm}")
                if value > 1:
                    raise ValueError("phase delay fraction cannot exceed one")
        for index, scale in enumerate(families[case_id]["duration_scales"]):
            if _fraction(scale, f"{case_id}.duration_scale[{index}]") <= 0:
                raise ValueError("duration scale must be positive")

    execution = record.get("execution", {})
    required_true = (
        "generate_all_candidate_definitions_before_screening",
        "screen_sequentially_per_case",
        "select_first_accepted_candidate",
        "retain_every_attempted_candidate",
        "do_not_run_later_candidates_after_first_acceptance",
    )
    if any(execution.get(field) is not True for field in required_true):
        raise ValueError("execution discipline is incomplete")
    if execution.get("no_match_result") != "FAIL_NO_MATCH_WITH_THRESHOLDS_UNCHANGED":
        raise ValueError("no-match behavior must fail without threshold changes")
    _require_positive_int(execution.get("per_probe_timeout_s"), "per_probe_timeout_s")


def load_generation_inputs(repo_root: Path, spec_path: Path) -> CollisionGenerationInputs:
    repo_root = repo_root.resolve()
    spec_path = spec_path.resolve()
    record = json.loads(spec_path.read_text(encoding="utf-8"))
    if not isinstance(record, Mapping):
        raise ValueError("generation specification root must be an object")
    validate_generation_spec(record)

    spec_file_sha256 = sha256_path(spec_path)
    spec_content_sha256 = sha256_bytes(canonical_json_bytes(record))
    protocol_ref = record["selection_protocol"]
    protocol_path = repo_root / protocol_ref["path"]
    if sha256_path(protocol_path) != _require_sha256(protocol_ref["file_sha256"], "protocol file hash"):
        raise ValueError("selection protocol file hash mismatch")
    protocol = load_collision_case_selection_protocol(str(protocol_path))
    if protocol.sha256 != protocol_ref["canonical_content_sha256"]:
        raise ValueError("selection protocol content hash mismatch")

    repository_inputs = record["repository_inputs"]
    repository_hash_fields = (
        ("fixed_state_path", "fixed_state_sha256"),
        ("world_path", "world_sha256"),
        ("empty_attachments_path", "empty_attachments_sha256"),
        ("attachment_target_path", "attachment_target_sha256"),
        ("inventory_path", "inventory_file_sha256"),
        ("backend_evidence_path", "backend_evidence_sha256"),
    )
    for path_field, hash_field in repository_hash_fields:
        path = repo_root / repository_inputs[path_field]
        if sha256_path(path) != _require_sha256(repository_inputs[hash_field], hash_field):
            raise ValueError(f"repository input hash mismatch: {path_field}")
    validate_attachment_target(
        repo_root / repository_inputs["attachment_target_path"],
        record["attachment"],
    )

    source_bundle = record["source_bundle"]
    archive_path = repo_root / source_bundle["path"]
    if sha256_path(archive_path) != _require_sha256(source_bundle["sha256"], "source bundle hash"):
        raise ValueError("source bundle hash mismatch")
    member_records = {item["name"]: item for item in source_bundle["members"]}
    member_bytes: Dict[str, bytes] = {}
    with tarfile.open(archive_path, "r:gz") as archive:
        members_by_name: Dict[str, list] = {}
        for member in archive.getmembers():
            members_by_name.setdefault(member.name, []).append(member)
        for name, item in member_records.items():
            matches = members_by_name.get(name, [])
            if len(matches) != 1 or not matches[0].isfile():
                raise ValueError(f"source member must occur exactly once as a regular file: {name}")
            stream = archive.extractfile(matches[0])
            if stream is None:
                raise ValueError(f"cannot read source member: {name}")
            data = stream.read()
            if sha256_bytes(data) != _require_sha256(item["sha256"], f"member hash: {name}"):
                raise ValueError(f"source member hash mismatch: {name}")
            member_bytes[name] = data

    keyframe_name = "gate_a_probe/gate_a2_validation/validated_keyframes.json"
    shortcut_name = "gate_a_probe/gate_a3_contact_aware/shortcut_paths.json"
    urdf_name = "gate_a_probe/outputs/openarm_v1_bimanual_resolved.urdf"
    keyframes = json.loads(member_bytes[keyframe_name].decode("utf-8"))
    shortcut_paths = json.loads(member_bytes[shortcut_name].decode("utf-8"))
    joint_limits = parse_arm_joint_limits(member_bytes[urdf_name])
    initial_fingers = validate_fixed_state(
        repo_root / repository_inputs["fixed_state_path"],
        keyframes,
    )
    return CollisionGenerationInputs(
        specification=record,
        specification_file_sha256=spec_file_sha256,
        specification_content_sha256=spec_content_sha256,
        protocol_content_sha256=protocol.sha256,
        keyframes=keyframes,
        shortcut_paths=shortcut_paths,
        joint_limits=joint_limits,
        initial_fingers=initial_fingers,
        source_member_hashes={name: sha256_bytes(data) for name, data in member_bytes.items()},
    )


def parse_arm_joint_limits(urdf_bytes: bytes) -> Mapping[str, JointLimit]:
    root = ET.fromstring(urdf_bytes)
    result: Dict[str, JointLimit] = {}
    expected = {
        f"openarm_{arm}_joint{index}"
        for arm in ARM_NAMES
        for index in range(1, JOINTS_PER_ARM + 1)
    }
    for joint in root.findall("joint"):
        name = joint.attrib.get("name")
        if name not in expected:
            continue
        limit = joint.find("limit")
        if limit is None:
            raise ValueError(f"joint has no limit element: {name}")
        result[name] = JointLimit(
            float(limit.attrib["lower"]),
            float(limit.attrib["upper"]),
            float(limit.attrib["velocity"]),
        )
    if set(result) != expected:
        raise ValueError("resolved URDF does not contain the exact 14 arm joint limits")
    return result


def validate_fixed_state(path: Path, keyframes: Mapping[str, Any]) -> Mapping[str, float]:
    rows = list(csv.DictReader(path.read_text(encoding="utf-8").splitlines(), delimiter="\t"))
    if len(rows) != 1 or rows[0].get("scenario") != "fixture_sample_0":
        raise ValueError("fixed-state table must contain only fixture_sample_0")
    expected = keyframes["both_outside"]["qpos_by_joint_name"]
    row = rows[0]
    if set(row) != {"scenario", *expected}:
        raise ValueError("fixed-state variables do not match the frozen both_outside state")
    for name, value in expected.items():
        if float(row[name]) != float(value):
            raise ValueError(f"fixed-state value differs from both_outside: {name}")
    fingers = {
        name: float(value)
        for name, value in expected.items()
        if "finger_joint" in name
    }
    if len(fingers) != 4:
        raise ValueError("expected four explicit finger variables")
    return fingers


def validate_attachment_target(path: Path, attachment: Mapping[str, Any]) -> None:
    fieldnames = [
        "reference_scenario",
        "body_id",
        "parent_link",
        "size_x",
        "size_y",
        "size_z",
        "target_x",
        "target_y",
        "target_z",
        "target_qx",
        "target_qy",
        "target_qz",
        "target_qw",
        "touch_links_csv",
    ]
    reader = csv.DictReader(path.read_text(encoding="utf-8").splitlines(), delimiter="\t")
    if reader.fieldnames != fieldnames:
        raise ValueError("attachment target header differs from the frozen contract")
    rows = list(reader)
    if len(rows) != 1:
        raise ValueError("attachment target must contain exactly one row")
    row = rows[0]
    scalar_expected = {
        "reference_scenario": attachment["reference_state"],
        "body_id": attachment["body_id"],
        "parent_link": attachment["parent_link"],
        "touch_links_csv": ",".join(attachment["touch_links"]),
    }
    for name, expected in scalar_expected.items():
        if row[name] != expected:
            raise ValueError(f"attachment target differs from specification: {name}")
    numeric_groups = (
        (("size_x", "size_y", "size_z"), attachment["size_m"]),
        (("target_x", "target_y", "target_z"), attachment["global_target_position_m"]),
        (("target_qx", "target_qy", "target_qz", "target_qw"), attachment["global_target_quaternion_xyzw"]),
    )
    for names, expected_values in numeric_groups:
        for name, expected in zip(names, expected_values):
            if float(row[name]) != float(expected):
                raise ValueError(f"attachment target differs from specification: {name}")


def arm_joint_names(arm: str) -> Tuple[str, ...]:
    return tuple(f"openarm_{arm}_joint{index}" for index in range(1, JOINTS_PER_ARM + 1))


def keyframe_arm(keyframes: Mapping[str, Any], state: str, arm: str) -> Tuple[float, ...]:
    values = keyframes[state]["qpos_by_joint_name"]
    return tuple(float(values[name]) for name in arm_joint_names(arm))


def shortcut_arm(shortcut_paths: Mapping[str, Any], name: str) -> Tuple[Tuple[float, ...], ...]:
    rows = shortcut_paths["paths"][name]
    if [row["waypoint"] for row in rows] != list(range(len(rows))):
        raise ValueError(f"shortcut waypoint order is not contiguous: {name}")
    result = tuple(tuple(float(value) for value in row["q_rad"]) for row in rows)
    if any(len(row) != JOINTS_PER_ARM for row in result):
        raise ValueError(f"shortcut path is not seven-dimensional: {name}")
    return result


def _deduplicate_adjacent(waypoints: Iterable[Tuple[float, ...]]) -> Tuple[Tuple[float, ...], ...]:
    result = []
    for waypoint in waypoints:
        if not result or waypoint != result[-1]:
            result.append(waypoint)
    if len(result) < 2:
        raise ValueError("candidate path collapsed to fewer than two waypoints")
    return tuple(result)


def _ceil_fraction(value: Fraction) -> int:
    return (value.numerator + value.denominator - 1) // value.denominator


def _ceil_quantum(value_ns: Fraction, quantum_ns: int) -> int:
    return _ceil_fraction(value_ns / quantum_ns) * quantum_ns


def segment_duration_ns(
    first: Sequence[float],
    second: Sequence[float],
    limits: Sequence[JointLimit],
    *,
    velocity_factor: float,
    minimum_ns: int,
    quantum_ns: int,
    scale: Fraction = Fraction(1, 1),
) -> int:
    required_seconds = max(
        abs(Fraction.from_float(b) - Fraction.from_float(a))
        / Fraction.from_float(limit.velocity)
        for a, b, limit in zip(first, second, limits)
    ) * Fraction.from_float(velocity_factor)
    required_ns = required_seconds * 1_000_000_000
    unscaled = max(Fraction(minimum_ns, 1), required_ns)
    return _ceil_quantum(unscaled * scale, quantum_ns)


def _make_path(
    inputs: CollisionGenerationInputs,
    arm: str,
    waypoints: Iterable[Tuple[float, ...]],
    *,
    scale: Fraction = Fraction(1, 1),
    start_delay_ns: int = 0,
) -> ArmPath:
    points = _deduplicate_adjacent(waypoints)
    names = arm_joint_names(arm)
    limits = tuple(inputs.joint_limits[name] for name in names)
    trajectory = inputs.specification["joint_trajectory"]
    durations = tuple(
        segment_duration_ns(
            first,
            second,
            limits,
            velocity_factor=float(trajectory["duration_velocity_factor"]),
            minimum_ns=int(trajectory["minimum_segment_duration_ns"]),
            quantum_ns=int(trajectory["duration_quantum_ns"]),
            scale=scale,
        )
        for first, second in zip(points, points[1:])
    )
    return ArmPath(arm, names, points, durations, start_delay_ns)


def _joint_limit_reasons(
    candidate: CollisionCandidate,
    inputs: CollisionGenerationInputs,
    *,
    generated_endpoint: bool,
) -> Tuple[str, ...]:
    tolerance = float(
        inputs.specification["joint_trajectory"][
            "generated_endpoint_joint_limit_tolerance_rad"
            if generated_endpoint
            else "source_joint_limit_tolerance_rad"
        ]
    )
    reasons = []
    for path in (candidate.left, candidate.right):
        for waypoint_index, waypoint in enumerate(path.waypoints):
            for name, value in zip(path.joint_names, waypoint):
                limit = inputs.joint_limits[name]
                if value < limit.lower - tolerance or value > limit.upper + tolerance:
                    reasons.append(f"{path.arm}:waypoint={waypoint_index}:{name}:outside-urdf-limit")
    return tuple(sorted(set(reasons)))


def _with_delays(
    left: ArmPath,
    right: ArmPath,
    delay_record: Mapping[str, Sequence[int]],
    quantum_ns: int,
) -> Tuple[ArmPath, ArmPath]:
    reference = max(left.motion_duration_ns, right.motion_duration_ns)
    left_delay = _ceil_quantum(reference * _fraction(delay_record["left"], "left delay"), quantum_ns)
    right_delay = _ceil_quantum(reference * _fraction(delay_record["right"], "right delay"), quantum_ns)
    return replace(left, start_delay_ns=left_delay), replace(right, start_delay_ns=right_delay)


def generate_candidates(inputs: CollisionGenerationInputs) -> Tuple[CollisionCandidate, ...]:
    spec = inputs.specification
    families = spec["families"]
    quantum_ns = int(spec["joint_trajectory"]["duration_quantum_ns"])
    candidates = []

    both_left = keyframe_arm(inputs.keyframes, "both_outside", "left")
    both_right = keyframe_arm(inputs.keyframes, "both_outside", "right")
    rank = 0
    for amplitude in families["C1"]["amplitudes_rad"]:
        for direction_index, directions in enumerate(families["C1"]["direction_templates"]):
            left_end = tuple(
                exact_affine_binary64(value, float(amplitude), direction)
                for value, direction in zip(both_left, directions["left"])
            )
            right_end = tuple(
                exact_affine_binary64(value, float(amplitude), direction)
                for value, direction in zip(both_right, directions["right"])
            )
            left = _make_path(inputs, "left", (both_left, left_end))
            right = _make_path(inputs, "right", (both_right, right_end))
            common_duration = max(left.motion_duration_ns, right.motion_duration_ns)
            left = replace(left, segment_durations_ns=(common_duration,))
            right = replace(right, segment_durations_ns=(common_duration,))
            candidate_id = f"C1-A{round(float(amplitude) * 1000):03d}-D{direction_index:02d}"
            candidate = CollisionCandidate(
                "C1",
                candidate_id,
                rank,
                "robot-fixture-empty",
                False,
                left,
                right,
                {"amplitude_rad": float(amplitude), "direction_index": direction_index},
                "READY",
            )
            reasons = _joint_limit_reasons(candidate, inputs, generated_endpoint=True)
            if reasons:
                candidate = replace(candidate, structural_status="REJECTED_PRE_SCREEN", structural_reasons=reasons)
            candidates.append(candidate)
            rank += 1

    c1_candidates = tuple(candidates)
    rank = 0
    for c1 in c1_candidates:
        candidate_id = c1.candidate_id.replace("C1-", "C2-", 1)
        candidates.append(
            CollisionCandidate(
                "C2",
                candidate_id,
                rank,
                "robot-fixture-carried-base",
                True,
                c1.left,
                c1.right,
                c1.generation_parameters,
                c1.structural_status,
                c1.structural_reasons,
                matched_kinematic_candidate_id=c1.candidate_id,
            )
        )
        rank += 1

    c3_family = families["C3"]
    c3_left_start = keyframe_arm(inputs.keyframes, c3_family["start_state"], "left")
    c3_left_end = keyframe_arm(inputs.keyframes, c3_family["end_state"], "left")
    c3_right_start = keyframe_arm(inputs.keyframes, c3_family["start_state"], "right")
    c3_right_end = keyframe_arm(inputs.keyframes, c3_family["end_state"], "right")
    rank = 0
    for phase_index, phase in enumerate(c3_family["phase_delays"]):
        for scale_index, scale_record in enumerate(c3_family["duration_scales"]):
            scale = _fraction(scale_record, "C3 duration scale")
            for path_index, variant in enumerate(c3_family["path_variants"]):
                if variant["name"] == "direct":
                    left_points = (c3_left_start, c3_left_end)
                    right_points = (c3_right_start, c3_right_end)
                elif variant["name"] == "shortcut":
                    left_points = (c3_left_start,) + shortcut_arm(inputs.shortcut_paths, variant["left"]) + (c3_left_end,)
                    right_points = (c3_right_start,) + shortcut_arm(inputs.shortcut_paths, variant["right"]) + (c3_right_end,)
                else:
                    raise ValueError("unknown C3 path variant")
                left = _make_path(inputs, "left", left_points, scale=scale)
                right = _make_path(inputs, "right", right_points, scale=scale)
                left, right = _with_delays(left, right, phase, quantum_ns)
                left_percent = int(_fraction(phase["left"], "left delay") * 100)
                right_percent = int(_fraction(phase["right"], "right delay") * 100)
                candidate_id = (
                    f"C3-L{left_percent:02d}-R{right_percent:02d}-"
                    f"S{scale_index:02d}-P{path_index:02d}"
                )
                candidate = CollisionCandidate(
                    "C3",
                    candidate_id,
                    rank,
                    "robot-fixture-empty",
                    False,
                    left,
                    right,
                    {
                        "phase_index": phase_index,
                        "left_delay_fraction": list(phase["left"]),
                        "right_delay_fraction": list(phase["right"]),
                        "duration_scale": list(scale_record),
                        "path_variant": variant["name"],
                    },
                    "READY",
                )
                reasons = _joint_limit_reasons(candidate, inputs, generated_endpoint=False)
                if reasons:
                    candidate = replace(candidate, structural_status="REJECTED_PRE_SCREEN", structural_reasons=reasons)
                candidates.append(candidate)
                rank += 1

    c4_family = families["C4"]
    left_in = shortcut_arm(inputs.shortcut_paths, c4_family["left_path"])
    right_in = shortcut_arm(inputs.shortcut_paths, c4_family["right_guard_path"])
    rank = 0
    for guard_index, (first_index, second_index) in enumerate(c4_family["right_guard_segments"]):
        right_points = (right_in[first_index], right_in[second_index])
        for phase_index, phase in enumerate(c4_family["phase_delays"]):
            for scale_index, scale_record in enumerate(c4_family["duration_scales"]):
                scale = _fraction(scale_record, "C4 duration scale")
                left = _make_path(inputs, "left", left_in, scale=scale)
                right = _make_path(inputs, "right", right_points, scale=scale)
                left, right = _with_delays(left, right, phase, quantum_ns)
                left_percent = int(_fraction(phase["left"], "left delay") * 100)
                right_percent = int(_fraction(phase["right"], "right delay") * 100)
                candidate_id = (
                    f"C4-G{guard_index:02d}-L{left_percent:02d}-R{right_percent:02d}-"
                    f"S{scale_index:02d}"
                )
                candidate = CollisionCandidate(
                    "C4",
                    candidate_id,
                    rank,
                    "robot-fixture-carried-base",
                    True,
                    left,
                    right,
                    {
                        "guard_segment_index": guard_index,
                        "right_guard_waypoints": [first_index, second_index],
                        "phase_index": phase_index,
                        "left_delay_fraction": list(phase["left"]),
                        "right_delay_fraction": list(phase["right"]),
                        "duration_scale": list(scale_record),
                    },
                    "READY",
                )
                reasons = _joint_limit_reasons(candidate, inputs, generated_endpoint=False)
                if reasons:
                    candidate = replace(candidate, structural_status="REJECTED_PRE_SCREEN", structural_reasons=reasons)
                candidates.append(candidate)
                rank += 1

    expected_counts = inputs.specification["candidate_counts"]
    actual_counts = {case_id: sum(candidate.case_id == case_id for candidate in candidates) for case_id in expected_counts if case_id != "total"}
    if any(actual_counts[case_id] != expected_counts[case_id] for case_id in actual_counts):
        raise AssertionError("generated candidate count differs from frozen specification")
    if len(candidates) != expected_counts["total"]:
        raise AssertionError("generated total differs from frozen specification")
    return tuple(candidates)


def sample_times(candidate: CollisionCandidate, inputs: CollisionGenerationInputs) -> Tuple[int, ...]:
    if candidate.structural_status != "READY":
        return ()
    sampling = inputs.specification["sampling"]
    period = int(sampling["maximum_period_ns"])
    increment = float(sampling["maximum_adjacent_joint_increment_rad"])
    end = candidate.global_duration_ns
    times = set(range(0, end + 1, period))
    times.add(end)
    times.update(candidate.left.global_boundaries_ns)
    times.update(candidate.right.global_boundaries_ns)

    def configuration(time_ns: int) -> Tuple[float, ...]:
        return candidate.left.configuration_at(time_ns) + candidate.right.configuration_at(time_ns)

    pending = [(first, second) for first, second in zip(sorted(times), sorted(times)[1:])]
    while pending:
        first, second = pending.pop()
        q_first = configuration(first)
        q_second = configuration(second)
        if max(abs(a - b) for a, b in zip(q_first, q_second)) <= increment:
            continue
        midpoint = (first + second) // 2
        if midpoint in (first, second):
            raise ValueError("cannot satisfy adjacent joint increment at integer-ns resolution")
        times.add(midpoint)
        pending.append((first, midpoint))
        pending.append((midpoint, second))
    ordered = tuple(sorted(times))
    if ordered[0] != 0 or ordered[-1] != end:
        raise AssertionError("sample grid lost global endpoints")
    for first, second in zip(ordered, ordered[1:]):
        if second - first > period:
            raise AssertionError("sample period exceeds frozen maximum")
        q_first = configuration(first)
        q_second = configuration(second)
        if max(abs(a - b) for a, b in zip(q_first, q_second)) > increment:
            raise AssertionError("sample increment exceeds frozen maximum")
    return ordered


def candidate_state_rows(
    candidate: CollisionCandidate,
    inputs: CollisionGenerationInputs,
) -> Tuple[Mapping[str, Any], ...]:
    rows = []
    for index, time_ns in enumerate(sample_times(candidate, inputs)):
        row: Dict[str, Any] = {
            "scenario": f"{candidate.candidate_id}__N{index:06d}__T{time_ns:019d}",
            "candidate_id": candidate.candidate_id,
            "sample_index": index,
            "time_ns": time_ns,
        }
        row.update(dict(zip(candidate.left.joint_names, candidate.left.configuration_at(time_ns))))
        row.update(dict(zip(candidate.right.joint_names, candidate.right.configuration_at(time_ns))))
        row.update(inputs.initial_fingers)
        rows.append(row)
    return tuple(rows)


def candidate_batch_manifest(
    inputs: CollisionGenerationInputs,
    candidates: Sequence[CollisionCandidate],
) -> dict:
    by_case = {
        case_id: [candidate for candidate in candidates if candidate.case_id == case_id]
        for case_id in inputs.specification["case_order"]
    }
    records = []
    for candidate in candidates:
        record = candidate.as_record()
        times = sample_times(candidate, inputs)
        record["sample_count"] = len(times)
        record["sample_times_sha256"] = (
            sha256_bytes(canonical_json_bytes({"times_ns": list(times)})) if times else None
        )
        record["candidate_sha256"] = candidate.content_hash
        records.append(record)
    return {
        "schema": MANIFEST_SCHEMA,
        "status": "PREPARED_NOT_SCREENED",
        "paper_result_eligible": False,
        "generation_spec_file_sha256": inputs.specification_file_sha256,
        "generation_spec_content_sha256": inputs.specification_content_sha256,
        "selection_protocol_content_sha256": inputs.protocol_content_sha256,
        "source_member_hashes": dict(sorted(inputs.source_member_hashes.items())),
        "candidate_counts": {case_id: len(values) for case_id, values in by_case.items()} | {"total": len(candidates)},
        "structural_status_counts": {
            status: sum(candidate.structural_status == status for candidate in candidates)
            for status in sorted({candidate.structural_status for candidate in candidates})
        },
        "attachment_transform_status": "BLOCKED_PENDING_UBUNTU_FK",
        "screening_backend_executed": False,
        "oracle_executed": False,
        "candidates": records,
    }


def state_tsv_bytes(
    candidate: CollisionCandidate,
    inputs: CollisionGenerationInputs,
) -> bytes:
    rows = candidate_state_rows(candidate, inputs)
    if not rows:
        raise ValueError("cannot emit states for a structurally rejected candidate")
    fieldnames = ["scenario"] + list(candidate.left.joint_names)
    fieldnames += ["openarm_left_finger_joint1", "openarm_left_finger_joint2"]
    fieldnames += list(candidate.right.joint_names)
    fieldnames += ["openarm_right_finger_joint1", "openarm_right_finger_joint2"]
    stream = io.StringIO(newline="")
    writer = csv.DictWriter(stream, fieldnames=fieldnames, delimiter="\t", lineterminator="\n", extrasaction="ignore")
    writer.writeheader()
    for row in rows:
        writer.writerow(
            {
                name: row[name]
                if name == "scenario"
                else canonical_binary64_decimal(float(row[name]))
                for name in fieldnames
            }
        )
    return stream.getvalue().encode("ascii")


def attachment_reference_state_tsv_bytes(inputs: CollisionGenerationInputs) -> bytes:
    """Emit the exact keyframe used to derive the carried-object transform.

    This performs no forward kinematics or collision query.  Those operations
    remain part of the hash-pinned Ubuntu MoveIt probe.
    """

    reference_state = inputs.specification["attachment"]["reference_state"]
    values = inputs.keyframes[reference_state]["qpos_by_joint_name"]
    fieldnames = ["scenario"] + list(arm_joint_names("left"))
    fieldnames += ["openarm_left_finger_joint1", "openarm_left_finger_joint2"]
    fieldnames += list(arm_joint_names("right"))
    fieldnames += ["openarm_right_finger_joint1", "openarm_right_finger_joint2"]
    if set(values) != set(fieldnames[1:]):
        raise ValueError("attachment reference keyframe does not exactly match the 18 robot variables")
    stream = io.StringIO(newline="")
    writer = csv.DictWriter(stream, fieldnames=fieldnames, delimiter="\t", lineterminator="\n")
    writer.writeheader()
    writer.writerow(
        {
            "scenario": reference_state,
            **{
                name: canonical_binary64_decimal(float(values[name]))
                for name in fieldnames[1:]
            },
        }
    )
    return stream.getvalue().encode("ascii")
