"""Frozen identity construction for the RH100 C3-v2 canonical program.

This module binds the already selected RH100 candidate to the restricted
Python surface language, Timed IR, and immutable joint trajectories.  It does
not query FCL or MuJoCo and does not establish a continuous collision result.
"""

from __future__ import annotations

import hashlib
import json
import math
from pathlib import Path
from typing import Any, Mapping

from .c3_v2_mujoco_oracle import ArmReplayTrajectory
from .restricted_python import ParserContext, TrajectoryBinding, parse_restricted_python
from .timed_ir import ActionNode, TimedProgram, canonical_program_bytes, program_sha256
from .trajectory import (
    FrozenJointTrajectory,
    POLYNOMIAL_EVALUATION_RULE,
    TrajectoryPoint,
)


SCHEMA = "bisafecode.c3-v2-rh100-identity/v0.1"
CANDIDATE_ID = "C3V2-RH100"
PROGRAM_FILENAME = "03_experiments/canonical_v0_3/c3_v2_rh100/program.py"
ORACLE_INPUT_SHA256 = "3cf1594dee0d8ed6971152b0917a190b7ed86624589d19246d3a5595cfc1bb6f"
CANDIDATE_DEFINITION_SHA256 = "a43f9848920dc52af0d64d842a288975d1465c83e4be1940bc17bdfa6cb69a85"
MODEL_URDF_SHA256 = "876d544622befe8e20aac6806133aab38de163032f463f0126468d7f8dd1df09"
ACTIVE_SRDF_SHA256 = "dba73d593eb3e8fd0f2a6eb3ffac360000e61fa38a8b6edb63fffef9e6755816"
GEOMETRY_INVENTORY_SHA256 = "f16d63b2d7e2688ecc8a15b9db55c252c223c419f7030ea56d1712cacbad03a9"
MUJOCO_SCENE_SHA256 = "1d760d3ce814a8fa0c8496301cac1b8802575032c9fb2d8d1bdf3ebc237a19f3"
ORACLE_SPEC_SHA256 = "5b6be9249d948b1215c45c0c95403735e33986373e405b0cc8ff843855e4a362"
MUJOCO_AUDIT_SHA256 = "56439e49296e541ef6149b38d5d4bcfe1ee483dd00f91687d4e17064d8bd2691"
HUMAN_REVIEW_AUDIT_SHA256 = "faba177e258ebe2c7c4489cd36d385f6a11ec726d6fb3c83b3e2cb9d14393321"


def canonical_json_bytes(value: Any) -> bytes:
    return (
        json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=True)
        + "\n"
    ).encode("ascii")


def sha256_bytes(value: bytes) -> str:
    return hashlib.sha256(value).hexdigest()


def sha256_path(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def load_oracle_input(path: Path) -> dict[str, Any]:
    if sha256_path(path) != ORACLE_INPUT_SHA256:
        raise ValueError("RH100 oracle input identity changed")
    record = json.loads(path.read_text(encoding="utf-8"))
    candidate = record.get("candidate_definition")
    if not isinstance(candidate, dict):
        raise ValueError("RH100 candidate definition is missing")
    if record.get("candidate_id") != CANDIDATE_ID:
        raise ValueError("RH100 candidate ID changed")
    if sha256_bytes(canonical_json_bytes(candidate)) != CANDIDATE_DEFINITION_SHA256:
        raise ValueError("RH100 candidate definition hash changed")
    return record


def load_pinned_json(path: Path, expected_sha256: str, label: str) -> dict[str, Any]:
    if sha256_path(path) != expected_sha256:
        raise ValueError(f"{label} identity changed")
    value = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(value, dict):
        raise ValueError(f"{label} must be a JSON object")
    return value


def _trajectory_from_candidate(
    candidate: Mapping[str, Any], arm: str
) -> FrozenJointTrajectory:
    replay = ArmReplayTrajectory.from_record(candidate[f"{arm}_trajectory"], arm)
    elapsed_ns = 0
    zero = (0.0,) * 7
    points = []
    for index, positions in enumerate(replay.waypoints):
        if index:
            elapsed_ns += replay.segment_durations_ns[index - 1]
        points.append(
            TrajectoryPoint.create(
                time_ns=elapsed_ns,
                positions=positions,
                velocities=zero,
                accelerations=zero,
            )
        )
    return FrozenJointTrajectory(
        arm=arm,
        joint_names=replay.joint_names,
        points=tuple(points),
        model_hash=MODEL_URDF_SHA256,
    )


def build_trajectories(
    oracle_input: Mapping[str, Any],
) -> tuple[FrozenJointTrajectory, FrozenJointTrajectory]:
    candidate = oracle_input["candidate_definition"]
    return (
        _trajectory_from_candidate(candidate, "left"),
        _trajectory_from_candidate(candidate, "right"),
    )


def environment_hash() -> str:
    identity = {
        "active_srdf_sha256": ACTIVE_SRDF_SHA256,
        "geometry_inventory_sha256": GEOMETRY_INVENTORY_SHA256,
        "model_urdf_sha256": MODEL_URDF_SHA256,
        "mujoco_scene_sha256": MUJOCO_SCENE_SHA256,
        "scope_id": "robot-fixture-empty",
    }
    return sha256_bytes(canonical_json_bytes(identity))


def canonical_source(left_hash: str, right_hash: str) -> str:
    return (
        "def left_branch():\n"
        f'    move("left", "{left_hash}")\n'
        "\n"
        "def right_branch():\n"
        '    wait("right", 7000000000)\n'
        f'    move("right", "{right_hash}")\n'
        "\n"
        "def task():\n"
        "    parallel(left_branch, right_branch)\n"
    )


def build_program(
    oracle_input: Mapping[str, Any],
) -> tuple[str, TimedProgram, tuple[FrozenJointTrajectory, FrozenJointTrajectory]]:
    trajectories = build_trajectories(oracle_input)
    source = canonical_source(*(trajectory.content_hash for trajectory in trajectories))
    context = ParserContext(
        program_name="c3_v2_rh100",
        finite_inputs=(),
        object_ids=(),
        resource_ids=(),
        trajectories=tuple(
            TrajectoryBinding(trajectory.content_hash, trajectory.duration_ns)
            for trajectory in trajectories
        ),
        environment_hash=environment_hash(),
        max_loop_bound=1,
    )
    program = parse_restricted_python(
        source,
        filename=PROGRAM_FILENAME,
        context=context,
    )
    return source, program, trajectories


def trajectory_record(trajectory: FrozenJointTrajectory) -> dict[str, Any]:
    return {
        "arm": trajectory.arm,
        "content_sha256": trajectory.content_hash,
        "duration_ns": trajectory.duration_ns,
        "interpolation": trajectory.interpolation_order.value,
        "joint_names": list(trajectory.joint_names),
        "model_hash": trajectory.model_hash,
        "points": [
            {
                "time_ns": point.time_ns,
                "positions_float_hex": [value.hex() for value in point.positions],
                "velocities_float_hex": [value.hex() for value in point.velocities],
                "accelerations_float_hex": [
                    value.hex() for value in (point.accelerations or ())
                ],
            }
            for point in trajectory.points
        ],
        "schema_version": trajectory.schema_version,
        "units": trajectory.units,
    }


def _frozen_configuration_at(
    trajectory: FrozenJointTrajectory, global_time_ns: int, start_delay_ns: int
) -> tuple[float, ...]:
    if global_time_ns <= start_delay_ns:
        return trajectory.points[0].positions
    local_ns = global_time_ns - start_delay_ns
    if local_ns >= trajectory.duration_ns:
        return trajectory.points[-1].positions
    for segment in trajectory.segments():
        if segment.start_ns <= local_ns <= segment.end_ns:
            return segment.evaluate(local_ns - segment.start_ns)[0]
    raise AssertionError("unreachable frozen trajectory time")


def interpolation_discrepancy(
    oracle_input: Mapping[str, Any],
    trajectories: tuple[FrozenJointTrajectory, FrozenJointTrajectory],
    *,
    grid_step_ns: int = 1_000_000,
) -> dict[str, Any]:
    """Quantify, but do not certify, the two binary64 evaluation rules."""

    if grid_step_ns <= 0 or 12_000_000_000 % grid_step_ns:
        raise ValueError("comparison grid must divide the frozen 12 s duration")
    candidate = oracle_input["candidate_definition"]
    replays = {
        arm: ArmReplayTrajectory.from_record(candidate[f"{arm}_trajectory"], arm)
        for arm in ("left", "right")
    }
    frozen = {trajectory.arm: trajectory for trajectory in trajectories}
    maximum = -1.0
    maximum_record: dict[str, Any] | None = None
    compared_scalars = 0
    for time_ns in range(0, 12_000_000_000 + 1, grid_step_ns):
        for arm in ("left", "right"):
            reference = replays[arm].configuration_at(time_ns)
            reconstructed = _frozen_configuration_at(
                frozen[arm], time_ns, replays[arm].start_delay_ns
            )
            for joint_index, (first, second) in enumerate(zip(reference, reconstructed)):
                difference = abs(first - second)
                if not math.isfinite(difference):
                    raise ValueError("non-finite interpolation discrepancy")
                compared_scalars += 1
                if difference > maximum:
                    maximum = difference
                    maximum_record = {
                        "arm": arm,
                        "joint_name": replays[arm].joint_names[joint_index],
                        "time_ns": time_ns,
                        "absolute_difference_rad": difference,
                        "candidate_value_float_hex": first.hex(),
                        "frozen_trajectory_value_float_hex": second.hex(),
                    }
    return {
        "candidate_rule": "exact-rational minimum-jerk blend; one final binary64 rounding",
        "frozen_trajectory_rule": (
            "stored binary64 quintic coefficients evaluated by explicit "
            "binary64 Horner operations"
        ),
        "frozen_trajectory_evaluation_rule_id": POLYNOMIAL_EVALUATION_RULE,
        "grid_start_ns": 0,
        "grid_end_ns": 12_000_000_000,
        "grid_step_ns": grid_step_ns,
        "sample_count": 12_000_000_000 // grid_step_ns + 1,
        "compared_scalar_count": compared_scalars,
        "maximum": maximum_record,
        "interpretation": "measurement only; must enter the numerical error envelope before any interval certificate",
    }


def action_source_records(program: TimedProgram) -> list[dict[str, Any]]:
    records = []
    for node in program.nodes:
        if not isinstance(node, ActionNode):
            continue
        records.append(
            {
                "node_id": node.node_id,
                "kind": node.action.kind.value,
                "arm": node.action.arm.value if node.action.arm else None,
                "trajectory_hash": node.action.trajectory_hash,
                "duration_ns": node.action.duration_ns,
                "source": {
                    "file": node.source.file,
                    "line": node.source.line,
                    "column": node.source.column,
                    "end_line": node.source.end_line,
                    "end_column": node.source.end_column,
                },
            }
        )
    return sorted(records, key=lambda item: (item["source"]["line"], item["node_id"]))


def core_identity_record(oracle_input: Mapping[str, Any]) -> dict[str, Any]:
    source, program, trajectories = build_program(oracle_input)
    return {
        "schema": SCHEMA,
        "candidate_id": CANDIDATE_ID,
        "paper_result_eligible": False,
        "source": {
            "file": PROGRAM_FILENAME,
            "sha256": sha256_bytes(source.encode("utf-8")),
            "text": source,
            "actions": action_source_records(program),
        },
        "timed_ir": {
            "sha256": program_sha256(program),
            "canonical": json.loads(canonical_program_bytes(program)),
        },
        "trajectories": [trajectory_record(item) for item in trajectories],
        "environment": {
            "environment_hash": environment_hash(),
            "resolved_urdf_sha256": MODEL_URDF_SHA256,
            "active_srdf_sha256": ACTIVE_SRDF_SHA256,
            "geometry_inventory_sha256": GEOMETRY_INVENTORY_SHA256,
            "mujoco_scene_sha256": MUJOCO_SCENE_SHA256,
            "scope_id": "robot-fixture-empty",
        },
        "interpolation_conversion_audit": interpolation_discrepancy(
            oracle_input, trajectories
        ),
        "provenance": {
            "oracle_input_sha256": ORACLE_INPUT_SHA256,
            "candidate_definition_sha256": CANDIDATE_DEFINITION_SHA256,
        },
        "limits": [
            "sampled FCL and MuJoCo evidence is not a continuous collision proof",
            "FCL and MuJoCo share OpenArm v1 collision-mesh provenance",
            "the frozen MJCF does not cover same-arm self-collision",
            "the interpolation conversion discrepancy is measured but not yet enclosed in an accepted numerical error budget",
            "collision version is simulation-only and must not execute on robot hardware",
        ],
    }


def _configuration_record(
    oracle_input: Mapping[str, Any], time_ns: int
) -> dict[str, list[str]]:
    candidate = oracle_input["candidate_definition"]
    return {
        arm: [
            value.hex()
            for value in ArmReplayTrajectory.from_record(
                candidate[f"{arm}_trajectory"], arm
            ).configuration_at(time_ns)
        ]
        for arm in ("left", "right")
    }


def build_identity_package(
    *,
    oracle_input_path: Path,
    oracle_spec_path: Path,
    geometry_inventory_path: Path,
    mujoco_audit_path: Path,
    human_review_audit_path: Path,
) -> dict[str, Any]:
    """Build the single RH100 identity package from hash-pinned evidence."""

    oracle_input = load_oracle_input(oracle_input_path)
    oracle_spec = load_pinned_json(oracle_spec_path, ORACLE_SPEC_SHA256, "oracle spec")
    inventory = load_pinned_json(
        geometry_inventory_path, GEOMETRY_INVENTORY_SHA256, "geometry inventory"
    )
    mujoco_audit = load_pinned_json(
        mujoco_audit_path, MUJOCO_AUDIT_SHA256, "MuJoCo audit"
    )
    human_audit = load_pinned_json(
        human_review_audit_path,
        HUMAN_REVIEW_AUDIT_SHA256,
        "human-review audit",
    )
    if human_audit.get("combined_status") != "PASS_HUMAN_KEYFRAME_SANITY_REVIEW":
        raise ValueError("RH100 human keyframe review has not passed")
    if human_audit.get("human_review_complete") is not True:
        raise ValueError("RH100 human keyframe review completion flag is absent")

    core = core_identity_record(oracle_input)
    fcl = oracle_input["fcl_reference"]
    mujoco = mujoco_audit["independently_recomputed"]
    fcl_time_ns = fcl["first_witness_time_ns"]
    start_ns = mujoco["grid_start_ns"]
    end_ns = mujoco["grid_end_ns"]
    package = {
        **core,
        "identity_status": "FROZEN_RH100_CANONICAL_COLLISION_COUNTEREXAMPLE",
        "human_review": {
            "status": human_audit["combined_status"],
            "signer": human_audit["human_signoff"]["signer"],
            "recorded_date": human_audit["human_signoff"]["recorded_date"],
            "reviewed_keyframes": human_audit["frames"],
            "explicit_exclusions": human_audit["human_signoff"]["explicit_exclusions"],
        },
        "anchors": {
            "start": {
                "time_ns": start_ns,
                "configuration_float_hex": _configuration_record(oracle_input, start_ns),
                "mujoco_negative_cross_arm_contacts": mujoco["endpoints"]["start_negative_cross_arm"],
            },
            "fcl_internal_witness": {
                "time_ns": fcl_time_ns,
                "configuration_float_hex": _configuration_record(oracle_input, fcl_time_ns),
                "entity_pair": fcl["first_witness_pair"],
                "signed_distance_m": fcl["first_witness_signed_distance_m"],
                "source_actions": [
                    item
                    for item in core["source"]["actions"]
                    if item["source"]["line"] in (2, 5)
                ],
            },
            "mujoco_first_internal_witness": mujoco["first_negative_cross_arm"],
            "mujoco_deepest_internal_witness": mujoco["deepest_negative_cross_arm"],
            "end": {
                "time_ns": end_ns,
                "configuration_float_hex": _configuration_record(oracle_input, end_ns),
                "mujoco_negative_cross_arm_contacts": mujoco["endpoints"]["end_negative_cross_arm"],
            },
        },
        "backends": {
            "fcl_construction": {
                "role": "sampled candidate-construction backend; not ground truth",
                "archive_sha256": oracle_spec["source_fcl_evidence"]["archive_sha256"],
                "evaluation_sha256": fcl["evaluation_sha256"],
                "state_table_sha256": fcl["state_table_sha256"],
                "moveit_version": "2.5.9",
                "fcl_version": "0.7.0",
                "checked_pair_count_per_sample": 340,
                "scope_id": "robot-fixture-empty",
                "resolved_urdf_sha256": MODEL_URDF_SHA256,
                "active_srdf_sha256": ACTIVE_SRDF_SHA256,
            },
            "mujoco_comparison": {
                "role": "separately implemented sampled comparison; shares mesh provenance",
                "archive_sha256": mujoco_audit["archive"]["sha256"],
                "scene_sha256": mujoco_audit["execution_identity"]["scene_sha256"],
                "mujoco_version": mujoco_audit["execution_identity"]["runtime"]["mujoco"],
                "numpy_version": mujoco_audit["execution_identity"]["runtime"]["numpy"],
                "sample_count": mujoco["sample_count"],
                "grid_step_ns": mujoco["unique_step_ns"][0],
                "negative_cross_arm_events": mujoco["negative_cross_arm_events"],
                "negative_robot_world_events": mujoco["negative_robot_world_events"],
                "sampled_contact_intervals_ns": mujoco["cross_arm_contact_intervals_ns"],
                "fcl_reference_contacts": mujoco["fcl_reference_time"]["contacts"],
            },
        },
        "geometry_provenance": {
            "collision_meshes": oracle_spec["model_contract"]["required_collision_meshes"],
            "unique_collision_mesh_count": len(
                oracle_spec["model_contract"]["required_collision_meshes"]
            ),
            "inventory_declared_status": inventory["status"],
            "unresolved_certificate_inputs": inventory["unresolved_certificate_inputs"],
            "shared_mesh_statement": oracle_spec["model_contract"]["independence_statement"],
            "same_arm_scope_limit": oracle_spec["model_contract"]["scope_limit"],
        },
        "canonical_set_role": {
            "suggested_slot": "C3: collision-negative canonical program (1 of 12)",
            "paired_safe_case": "not yet frozen; must use the same scene and comparable source/trajectory complexity without any point or interval collision",
            "ablation_boundary": {
                "whole_program_start_end_only": "expected to miss the internal collision because t=0 and t=12 s are contact-free",
                "all_action_boundaries": "not expected to miss: t=7 s is an action boundary and contains the deepest MuJoCo overlap",
                "scientific_consequence": "RH100 cannot by itself support the claim that a fair action-boundary checker misses timed collision; a distinct matched case is required for that ablation.",
            },
        },
        "continuous_certificate_gate": {
            "status": "NOT_COMPLETED",
            "required_inputs": [
                "signed-distance margin",
                "pair-specific relative-speed upper bound",
                "frozen trajectory interpolation rule",
                "geometry/model/numerical error envelope",
                "interval-level verified/violated/unknown rule",
            ],
            "sampled_agreement_is_continuous_proof": False,
        },
    }
    package["identity_package_sha256"] = sha256_bytes(canonical_json_bytes(package))
    return package
