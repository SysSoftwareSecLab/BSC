"""Frozen preparation and pure predicates for the C3-v2 MuJoCo replay.

The MuJoCo execution lives in ``tools/run_c3_v2_mujoco_oracle.py`` so the
input and decision contracts can be tested on machines without MuJoCo.  The
replay deliberately recomputes qpos from the frozen trajectory definition on
a uniform grid; it never consumes the FCL state table as trajectory input.
"""

from __future__ import annotations

import csv
from dataclasses import dataclass
import hashlib
import io
import json
import math
from pathlib import Path
import tarfile
from typing import Any, Dict, Mapping, Optional, Sequence, Tuple

from .collision_candidate_generation import minimum_jerk_interpolate_binary64


SPEC_SCHEMA = "bisafecode.c3-v2-mujoco-oracle-spec/v0.1"
SPEC_STATUS = "FROZEN_BEFORE_MUJOCO_REPLAY"
INPUT_SCHEMA = "bisafecode.c3-v2-mujoco-oracle-input/v0.1"
RESULT_SCHEMA = "bisafecode.c3-v2-mujoco-oracle-result/v0.1"
EXPECTED_CANDIDATE = "C3V2-RH100"


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
        json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=True, allow_nan=False)
        + "\n"
    ).encode("ascii")


def load_oracle_spec(path: Path) -> Mapping[str, Any]:
    record = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(record, Mapping):
        raise ValueError("C3-v2 MuJoCo oracle specification must be an object")
    if record.get("schema") != SPEC_SCHEMA or record.get("status") != SPEC_STATUS:
        raise ValueError("C3-v2 MuJoCo oracle specification is not frozen")
    if record.get("experiment_id") != "EXP-S2-034" or record.get("candidate_id") != EXPECTED_CANDIDATE:
        raise ValueError("C3-v2 MuJoCo oracle experiment or candidate changed")
    if record.get("paper_result_eligible") is not False:
        raise ValueError("C3-v2 MuJoCo oracle cannot be paper-result eligible before adjudication")
    grid = record.get("temporal_grid", {})
    if grid.get("period_ns") != 1_000_000 or grid.get("expected_sample_count") != 12_001:
        raise ValueError("C3-v2 MuJoCo temporal grid changed")
    if grid.get("must_recompute_from_candidate_definition") is not True:
        raise ValueError("C3-v2 MuJoCo replay must recompute the trajectory")
    if grid.get("must_not_read_fcl_state_table_for_qpos") is not True:
        raise ValueError("C3-v2 MuJoCo replay may not reuse FCL qpos rows")
    predicate = record.get("contact_predicate", {})
    if predicate.get("penetrating_contact") != "contact.dist < 0":
        raise ValueError("C3-v2 MuJoCo contact predicate changed")
    if predicate.get("thresholds_mutable_after_execution") is not False:
        raise ValueError("C3-v2 MuJoCo thresholds must remain frozen")
    if record.get("human_review", {}).get("automatic_result_cannot_complete_review") is not True:
        raise ValueError("C3-v2 MuJoCo automated replay cannot complete human review")
    return record


def _safe_archive_members(archive: tarfile.TarFile) -> Mapping[str, tarfile.TarInfo]:
    result: Dict[str, tarfile.TarInfo] = {}
    for member in archive.getmembers():
        path = Path(member.name)
        if path.is_absolute() or ".." in path.parts or member.issym() or member.islnk():
            raise ValueError(f"unsafe C3-v2 evidence archive member: {member.name}")
        if member.name in result:
            raise ValueError(f"duplicate C3-v2 evidence archive member: {member.name}")
        result[member.name] = member
    return result


def _unique_member_by_suffix(
    members: Mapping[str, tarfile.TarInfo], suffix: str, label: str
) -> tarfile.TarInfo:
    matches = [member for name, member in members.items() if name.endswith(suffix)]
    if len(matches) != 1 or not matches[0].isfile():
        raise ValueError(f"{label} must occur once as a regular archive member")
    return matches[0]


def _read_member(archive: tarfile.TarFile, member: tarfile.TarInfo) -> bytes:
    stream = archive.extractfile(member)
    if stream is None:
        raise ValueError(f"cannot read archive member: {member.name}")
    return stream.read()


def _require_member_identity(raw: bytes, record: Mapping[str, Any], prefix: str) -> None:
    expected_size = record.get(f"{prefix}_size_bytes")
    if expected_size is not None and len(raw) != expected_size:
        raise ValueError(f"{prefix} member size mismatch")
    if sha256_bytes(raw) != record.get(f"{prefix}_sha256"):
        raise ValueError(f"{prefix} member SHA-256 mismatch")


def build_oracle_input(repo_root: Path, specification: Mapping[str, Any]) -> Mapping[str, Any]:
    """Extract the minimum frozen RH100 record from the immutable FCL archive."""

    source = specification["source_fcl_evidence"]
    archive_path = repo_root / source["archive_path"]
    if archive_path.stat().st_size != source["archive_size_bytes"]:
        raise ValueError("C3-v2 FCL source archive size mismatch")
    if sha256_path(archive_path) != source["archive_sha256"]:
        raise ValueError("C3-v2 FCL source archive SHA-256 mismatch")

    with tarfile.open(archive_path, "r:gz") as archive:
        members = _safe_archive_members(archive)
        report_member = _unique_member_by_suffix(
            members, source["prefilter_report_member_suffix"], "C3-v2 prefilter report"
        )
        state_member = _unique_member_by_suffix(
            members, source["state_table_member_suffix"], "C3-v2 RH100 state table"
        )
        evaluation_member = _unique_member_by_suffix(
            members, source["evaluation_member_suffix"], "C3-v2 RH100 FCL evaluation"
        )
        report_raw = _read_member(archive, report_member)
        state_raw = _read_member(archive, state_member)
        evaluation_raw = _read_member(archive, evaluation_member)

    if sha256_bytes(report_raw) != source["prefilter_report_sha256"]:
        raise ValueError("C3-v2 prefilter report SHA-256 mismatch")
    _require_member_identity(state_raw, source, "state_table")
    if sha256_bytes(evaluation_raw) != source["evaluation_sha256"]:
        raise ValueError("C3-v2 evaluation SHA-256 mismatch")

    report = json.loads(report_raw)
    matches = [item for item in report.get("candidates", []) if item.get("candidate_id") == EXPECTED_CANDIDATE]
    if len(matches) != 1:
        raise ValueError("C3-v2 RH100 candidate definition is not unique")
    candidate = matches[0]["candidate_definition"]
    if sha256_bytes(canonical_json_bytes(candidate)) != source["candidate_definition_canonical_sha256"]:
        raise ValueError("C3-v2 RH100 candidate definition SHA-256 mismatch")

    state_rows = csv.DictReader(io.StringIO(state_raw.decode("utf-8")), delimiter="\t")
    first = next(state_rows, None)
    if first is None or first.get("scenario") != "C3V2-RH100__N000000__T0000000000000000000":
        raise ValueError("C3-v2 RH100 state table does not start at the frozen endpoint")
    finger_names = tuple(
        f"openarm_{arm}_finger_joint{index}" for arm in ("left", "right") for index in (1, 2)
    )
    initial_fingers = {name: float(first[name]) for name in finger_names}
    if any(not math.isfinite(value) for value in initial_fingers.values()):
        raise ValueError("C3-v2 initial finger values are non-finite")

    evaluation = json.loads(evaluation_raw)
    witness = evaluation.get("target_witness")
    if evaluation.get("accepted") is not True or not isinstance(witness, Mapping):
        raise ValueError("C3-v2 source evaluation did not accept RH100")
    if witness.get("sample_index") != 1346 or witness.get("signed_distance_m") != source["reference_first_witness_signed_distance_m"]:
        raise ValueError("C3-v2 source witness changed")
    if witness.get("scenario", "").rsplit("__T", 1)[-1] != f"{source['reference_first_witness_time_ns']:019d}":
        raise ValueError("C3-v2 source witness time changed")
    if [witness.get("entity_first"), witness.get("entity_second")] != source["reference_first_witness_pair"]:
        raise ValueError("C3-v2 source witness pair changed")

    result = {
        "schema": INPUT_SCHEMA,
        "experiment_id": specification["experiment_id"],
        "candidate_id": EXPECTED_CANDIDATE,
        "paper_result_eligible": False,
        "candidate_definition": candidate,
        "candidate_definition_canonical_sha256": source["candidate_definition_canonical_sha256"],
        "initial_fingers_rad_or_m": initial_fingers,
        "fcl_reference": {
            "source_archive_sha256": source["archive_sha256"],
            "state_table_sha256": source["state_table_sha256"],
            "evaluation_sha256": source["evaluation_sha256"],
            "first_witness_time_ns": source["reference_first_witness_time_ns"],
            "first_witness_pair": source["reference_first_witness_pair"],
            "first_witness_signed_distance_m": source["reference_first_witness_signed_distance_m"],
            "endpoint_minimum_signed_distance_m": evaluation["endpoint_minimum"]["signed_distance_m"],
        },
        "derivation": {
            "state_table_use": "identity-and-initial-fingers-only; never used for replay qpos",
            "trajectory_source": "candidate_definition",
            "collision_label_source": "pending independent MuJoCo replay and human review",
        },
    }
    validate_oracle_input(result, specification)
    return result


@dataclass(frozen=True)
class ArmReplayTrajectory:
    joint_names: Tuple[str, ...]
    waypoints: Tuple[Tuple[float, ...], ...]
    segment_durations_ns: Tuple[int, ...]
    start_delay_ns: int

    @classmethod
    def from_record(cls, record: Mapping[str, Any], arm: str) -> "ArmReplayTrajectory":
        if record.get("arm") != arm or record.get("interpolation") != "quintic-minimum-jerk":
            raise ValueError(f"invalid {arm} trajectory identity")
        names = tuple(record.get("joint_names", ()))
        expected = tuple(f"openarm_{arm}_joint{index}" for index in range(1, 8))
        if names != expected:
            raise ValueError(f"{arm} trajectory joint order changed")
        try:
            waypoints = tuple(tuple(float.fromhex(value) for value in row) for row in record["waypoints_float_hex"])
        except (KeyError, TypeError, ValueError) as error:
            raise ValueError(f"invalid {arm} trajectory waypoint encoding") from error
        durations = tuple(record.get("segment_durations_ns", ()))
        start_delay = record.get("start_delay_ns")
        if len(waypoints) < 2 or len(durations) != len(waypoints) - 1:
            raise ValueError(f"{arm} trajectory waypoint/duration mismatch")
        if any(len(row) != 7 or any(not math.isfinite(value) for value in row) for row in waypoints):
            raise ValueError(f"{arm} trajectory waypoints must be finite 7-vectors")
        if any(isinstance(value, bool) or not isinstance(value, int) or value <= 0 for value in durations):
            raise ValueError(f"{arm} trajectory durations must be positive integers")
        if isinstance(start_delay, bool) or not isinstance(start_delay, int) or start_delay < 0:
            raise ValueError(f"{arm} trajectory start delay is invalid")
        return cls(names, waypoints, durations, start_delay)

    @property
    def end_ns(self) -> int:
        return self.start_delay_ns + sum(self.segment_durations_ns)

    def configuration_at(self, time_ns: int) -> Tuple[float, ...]:
        if time_ns <= self.start_delay_ns:
            return self.waypoints[0]
        local = time_ns - self.start_delay_ns
        motion_duration = sum(self.segment_durations_ns)
        if local >= motion_duration:
            return self.waypoints[-1]
        elapsed = 0
        for index, duration in enumerate(self.segment_durations_ns):
            if local <= elapsed + duration:
                numerator = local - elapsed
                return tuple(
                    minimum_jerk_interpolate_binary64(first, second, numerator, duration)
                    for first, second in zip(self.waypoints[index], self.waypoints[index + 1])
                )
            elapsed += duration
        raise AssertionError("unreachable C3-v2 trajectory time")


def validate_oracle_input(record: Mapping[str, Any], specification: Mapping[str, Any]) -> None:
    if record.get("schema") != INPUT_SCHEMA or record.get("experiment_id") != "EXP-S2-034":
        raise ValueError("C3-v2 MuJoCo oracle input schema or experiment changed")
    if record.get("candidate_id") != EXPECTED_CANDIDATE or record.get("paper_result_eligible") is not False:
        raise ValueError("C3-v2 MuJoCo oracle input candidate or boundary changed")
    candidate = record.get("candidate_definition")
    if not isinstance(candidate, Mapping):
        raise ValueError("C3-v2 MuJoCo oracle candidate definition is missing")
    actual = sha256_bytes(canonical_json_bytes(candidate))
    expected = specification["source_fcl_evidence"]["candidate_definition_canonical_sha256"]
    if actual != expected or record.get("candidate_definition_canonical_sha256") != expected:
        raise ValueError("C3-v2 MuJoCo oracle candidate definition identity mismatch")
    if candidate.get("candidate_id") != EXPECTED_CANDIDATE or candidate.get("global_duration_ns") != 12_000_000_000:
        raise ValueError("C3-v2 MuJoCo candidate ID or duration changed")
    if candidate.get("attached_object_active") is not False or candidate.get("scope_id") != "robot-fixture-empty":
        raise ValueError("C3-v2 MuJoCo candidate geometry scope changed")
    left = ArmReplayTrajectory.from_record(candidate["left_trajectory"], "left")
    right = ArmReplayTrajectory.from_record(candidate["right_trajectory"], "right")
    if max(left.end_ns, right.end_ns) != candidate["global_duration_ns"]:
        raise ValueError("C3-v2 MuJoCo trajectory duration mismatch")
    fingers = record.get("initial_fingers_rad_or_m")
    expected_names = {
        f"openarm_{arm}_finger_joint{index}" for arm in ("left", "right") for index in (1, 2)
    }
    if not isinstance(fingers, Mapping) or set(fingers) != expected_names:
        raise ValueError("C3-v2 MuJoCo initial finger set changed")
    if any(not isinstance(value, (int, float)) or isinstance(value, bool) or not math.isfinite(value) for value in fingers.values()):
        raise ValueError("C3-v2 MuJoCo initial fingers must be finite")
    reference = record.get("fcl_reference", {})
    source = specification["source_fcl_evidence"]
    if reference.get("source_archive_sha256") != source["archive_sha256"]:
        raise ValueError("C3-v2 MuJoCo FCL archive reference changed")
    if reference.get("first_witness_time_ns") != source["reference_first_witness_time_ns"]:
        raise ValueError("C3-v2 MuJoCo FCL witness time changed")


def replay_components(record: Mapping[str, Any], specification: Mapping[str, Any]) -> Mapping[str, Any]:
    validate_oracle_input(record, specification)
    candidate = record["candidate_definition"]
    return {
        "left": ArmReplayTrajectory.from_record(candidate["left_trajectory"], "left"),
        "right": ArmReplayTrajectory.from_record(candidate["right_trajectory"], "right"),
        "initial_fingers": dict(record["initial_fingers_rad_or_m"]),
        "duration_ns": candidate["global_duration_ns"],
    }


def uniform_replay_times(specification: Mapping[str, Any], duration_ns: int) -> Tuple[int, ...]:
    grid = specification["temporal_grid"]
    if duration_ns != grid["expected_duration_ns"]:
        raise ValueError("C3-v2 MuJoCo replay duration changed")
    period = grid["period_ns"]
    result = tuple(range(0, duration_ns + 1, period))
    if result[0] != 0 or result[-1] != duration_ns or len(result) != grid["expected_sample_count"]:
        raise ValueError("C3-v2 MuJoCo replay grid is inconsistent")
    return result


def qpos_by_joint_name(components: Mapping[str, Any], time_ns: int) -> Mapping[str, float]:
    left: ArmReplayTrajectory = components["left"]
    right: ArmReplayTrajectory = components["right"]
    result = dict(components["initial_fingers"])
    result.update(zip(left.joint_names, left.configuration_at(time_ns)))
    result.update(zip(right.joint_names, right.configuration_at(time_ns)))
    if len(result) != 18 or any(not math.isfinite(value) for value in result.values()):
        raise ValueError("C3-v2 MuJoCo qpos map is incomplete or non-finite")
    return result


def geom_side(name: str, world_names: Sequence[str]) -> str:
    if name.startswith("openarm_left_") and name.endswith("_collision"):
        return "left"
    if name.startswith("openarm_right_") and name.endswith("_collision"):
        return "right"
    if name in world_names:
        return "world"
    return "ignored"


def contact_kind(first: str, second: str, world_names: Sequence[str]) -> str:
    sides = {geom_side(first, world_names), geom_side(second, world_names)}
    if sides == {"left", "right"}:
        return "cross-arm"
    if "world" in sides and ("left" in sides or "right" in sides):
        return "robot-world"
    return "other"


def classify_automated_summary(summary: Mapping[str, Any]) -> str:
    endpoints = summary.get("endpoints", {})
    for name in ("start", "end"):
        endpoint = endpoints.get(name, {})
        if endpoint.get("negative_cross_arm_contacts", 0) or endpoint.get("negative_robot_world_contacts", 0):
            return "DISAGREE_MUJOCO_ENDPOINT_CONTACT"
    if summary.get("first_negative_cross_arm") is None:
        return "DISAGREE_MUJOCO_NO_INTERIOR_CROSS_ARM_CONTACT"
    reference = summary.get("fcl_reference", {})
    if not reference.get("negative_cross_arm_contacts", 0):
        return "DISAGREE_MUJOCO_AT_FCL_REFERENCE_TIME"
    if summary.get("negative_robot_world_before_or_at_first_cross_arm", 0):
        return "DISAGREE_MUJOCO_EARLY_WORLD_CONTACT"
    return "PASS_AUTOMATED_MUJOCO_REPLAY_PENDING_HUMAN_REVIEW"
