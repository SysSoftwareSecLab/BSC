"""Unified RH100 entry from restricted source to source-level counterexample.

The geometry checker is injected.  Unit tests may use a scripted checker to
verify plumbing, but only a hash-bound OpenArm backend may produce scientific
geometry evidence.
"""

from __future__ import annotations

from typing import Any, Mapping, Optional

from .c3_v2_canonical import build_program, environment_hash
from .explicit_state import (
    ExplicitStateEngine,
    GripperConfiguration,
    IntervalChecker,
    IntervalStatus,
    JointConfiguration,
    PointChecker,
    SearchBounds,
    SearchEnvironment,
    SearchReport,
    Verdict,
    search_artifact_dict,
    unresolved_point_checker,
)
from .timed_ir import ActionNode, TimedProgram
from .trajectory import FrozenJointTrajectory


SCHEMA = "bisafecode.c3-v2-unified-result/v0.1"


def build_search_environment(
    oracle_input: Mapping[str, Any],
) -> tuple[TimedProgram, SearchEnvironment]:
    _source, program, trajectories = build_program(oracle_input)
    candidate = oracle_input["candidate_definition"]
    left_initial = tuple(
        float.fromhex(value)
        for value in candidate["left_trajectory"]["waypoints_float_hex"][0]
    )
    right_initial = tuple(
        float.fromhex(value)
        for value in candidate["right_trajectory"]["waypoints_float_hex"][0]
    )
    finger_position = float(
        oracle_input["initial_fingers_rad_or_m"]["openarm_left_finger_joint1"]
    )
    if any(
        float(value) != finger_position
        for value in oracle_input["initial_fingers_rad_or_m"].values()
    ):
        raise ValueError("RH100 frozen fingers are not symmetric")
    environment = SearchEnvironment(
        environment_hash=environment_hash(),
        initial_left_q=JointConfiguration.from_positions(left_initial),
        initial_right_q=JointConfiguration.from_positions(right_initial),
        initial_left_gripper=GripperConfiguration.from_position(finger_position),
        initial_right_gripper=GripperConfiguration.from_position(finger_position),
        gripper_position_limits=(0.0, 0.05),
        initial_objects=(),
        initial_resources=(),
        trajectories=tuple(
            (trajectory.content_hash, trajectory) for trajectory in trajectories
        ),
    )
    return program, environment


def _trajectory_map(
    environment: SearchEnvironment,
) -> dict[str, FrozenJointTrajectory]:
    return {key: value for key, value in environment.trajectories}


def _normalized_entity(entity_id: str) -> str:
    suffix = "_collision"
    return entity_id[: -len(suffix)] if entity_id.endswith(suffix) else entity_id


def _trajectory_segment(
    trajectory: FrozenJointTrajectory,
    *,
    action_start_ns: int,
    witness_time_ns: int,
) -> dict[str, Any]:
    local_ns = witness_time_ns - action_start_ns
    if not 0 <= local_ns <= trajectory.duration_ns:
        raise ValueError("witness time lies outside the active trajectory")
    if local_ns == trajectory.duration_ns:
        segment = trajectory.segments()[-1]
    else:
        segment = next(
            item
            for item in trajectory.segments()
            if item.start_ns <= local_ns < item.end_ns
        )
    return {
        "trajectory_sha256": trajectory.content_hash,
        "interpolation": trajectory.interpolation_order.value,
        "action_start_ns": action_start_ns,
        "local_witness_time_ns": local_ns,
        "local_segment_ns": [segment.start_ns, segment.end_ns],
        "global_segment_ns": [
            action_start_ns + segment.start_ns,
            action_start_ns + segment.end_ns,
        ],
        "source_point_indices": list(segment.source_point_indices),
    }


def source_level_counterexample(
    *,
    report: SearchReport,
    program: TimedProgram,
    environment: SearchEnvironment,
    identity_package: Mapping[str, Any],
) -> Optional[dict[str, Any]]:
    if report.verdict is not Verdict.VIOLATED or not report.counterexample:
        return None
    terminal = report.counterexample[-1]
    assessment = terminal.interval or terminal.point
    if assessment is None or assessment.entity_pair is None:
        raise ValueError("RH100 collision report lacks a geometric witness")
    if terminal.interval is not None:
        witness_interval = list(terminal.interval.interval_ns)
        witness_time_ns = terminal.interval.interval_ns[0]
    else:
        witness_time_ns = terminal.point.time_ns
        witness_interval = [witness_time_ns, witness_time_ns]

    by_node = {
        node.node_id: node
        for node in program.nodes
        if isinstance(node, ActionNode)
    }
    trajectory_by_hash = _trajectory_map(environment)
    relevant_actions = []
    trajectory_segments = []
    for node_id in terminal.node_ids:
        node = by_node.get(node_id)
        if node is None:
            continue
        action_start_ns = 0
        if node.action.arm and node.action.arm.value == "right" and node.action.kind.value == "move":
            action_start_ns = 7_000_000_000
        record = {
            "node_id": node.node_id,
            "arm": node.action.arm.value if node.action.arm else None,
            "kind": node.action.kind.value,
            "duration_ns": node.action.duration_ns,
            "source": {
                "file": node.source.file,
                "line": node.source.line,
                "column": node.source.column,
                "end_line": node.source.end_line,
                "end_column": node.source.end_column,
            },
            "trajectory_sha256": node.action.trajectory_hash,
        }
        relevant_actions.append(record)
        if node.action.trajectory_hash:
            trajectory_segments.append(
                {
                    "node_id": node.node_id,
                    **_trajectory_segment(
                        trajectory_by_hash[node.action.trajectory_hash],
                        action_start_ns=action_start_ns,
                        witness_time_ns=witness_time_ns,
                    ),
                }
            )

    checker_pair = tuple(_normalized_entity(item) for item in assessment.entity_pair)
    oracle_contacts = identity_package["backends"]["mujoco_comparison"][
        "fcl_reference_contacts"
    ]
    oracle_pairs = {
        tuple(_normalized_entity(item) for item in contact["pair"])
        for contact in oracle_contacts
    }
    sampled_intervals = identity_package["backends"]["mujoco_comparison"][
        "sampled_contact_intervals_ns"
    ]
    time_agreement = any(
        item["start_ns"] <= witness_time_ns <= item["end_ns"]
        for item in sampled_intervals
    )
    pair_agreement = checker_pair in oracle_pairs
    return {
        "schema": "bisafecode.source-level-counterexample/v0.1",
        "property_id": "cross-arm-collision",
        "conflicting_entities": list(assessment.entity_pair),
        "violation_time_ns": witness_time_ns,
        "violation_interval_ns": witness_interval,
        "relevant_actions": sorted(
            relevant_actions,
            key=lambda item: (item["source"]["line"], item["node_id"]),
        ),
        "trajectory_segments": trajectory_segments,
        "checker_witness_ref": assessment.witness_ref,
        "checker_oracle_comparison": {
            "checker_backend": "injected interval checker",
            "oracle_backend": "MuJoCo 3.11.0 sampled comparison",
            "property_agreement": time_agreement,
            "time_within_oracle_sampled_contact_interval": time_agreement,
            "normalized_pair_agreement_at_fcl_reference": pair_agreement,
            "agreement_scope": "sampled model-level witness only; not a continuous certificate or fully independent geometry ground truth",
        },
    }


def run_unified_rh100(
    *,
    oracle_input: Mapping[str, Any],
    identity_package: Mapping[str, Any],
    interval_checker: IntervalChecker,
    point_checker: PointChecker = unresolved_point_checker,
    bounds: SearchBounds = SearchBounds(
        max_states=256,
        max_transitions=1024,
        max_time_ns=12_000_000_000,
    ),
    checker_qualification: str,
) -> dict[str, Any]:
    """Run the real parser/IR/search pipeline with an explicitly qualified checker."""

    if checker_qualification not in {
        "ENGINEERING_TEST_DOUBLE",
        "HASH_BOUND_OPENARM_BACKEND",
    }:
        raise ValueError("checker qualification must be explicit")
    program, environment = build_search_environment(oracle_input)
    engine = ExplicitStateEngine(
        program,
        environment,
        interval_checker=interval_checker,
        point_checker=point_checker,
    )
    report = engine.run(bounds)
    counterexample = source_level_counterexample(
        report=report,
        program=program,
        environment=environment,
        identity_package=identity_package,
    )
    scientific_result_eligible = (
        checker_qualification == "HASH_BOUND_OPENARM_BACKEND"
        and identity_package["continuous_certificate_gate"]["status"] == "COMPLETED"
    )
    return {
        "schema": SCHEMA,
        "candidate_id": "C3V2-RH100",
        "pipeline": [
            "restricted-python-parser",
            "timed-ir",
            "explicit-state-search",
            "point-or-interval-checker",
            report.verdict.value,
            "source-level-counterexample" if counterexample else "no-counterexample",
            "independent-oracle-comparison" if counterexample else "oracle-not-compared",
        ],
        "checker_qualification": checker_qualification,
        "search": search_artifact_dict(program, environment, bounds, report),
        "counterexample": counterexample,
        "scientific_result_eligible": scientific_result_eligible,
        "paper_result_eligible": False,
    }
