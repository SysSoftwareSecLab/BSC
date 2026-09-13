"""Hash-bound MoveIt/FCL process adapter for one explicit RH100 state.

The existing C++ probe accepts a TSV of complete OpenArm variable states and
returns every checked signed-distance pair.  This adapter binds that process
to an explicit search state and frozen trajectories.  Backend failure,
identity drift, time mismatch, or incomplete pair coverage raises an error;
the surrounding collision checker converts that error to ``unknown``.
"""

from __future__ import annotations

import hashlib
import json
import math
import os
from dataclasses import dataclass
from pathlib import Path
import subprocess
import tempfile
from typing import Any, Callable, Iterable, Mapping, Optional, Sequence

from .continuous_collision import EntityPair, canonical_entity_pair
from .explicit_state import (
    ActiveAction,
    SearchEnvironment,
    SearchState,
    state_fingerprint,
)
from .geometry_contract import (
    CollisionPairSpec,
    GeometryBackendIdentity,
    GeometryEntityKind,
    GeometryEntitySpec,
    MotionSource,
    PairDisposition,
    SignedDistanceObservation,
)
from .timed_ir import ActionKind, Arm


STATE_VARIABLES = tuple(
    [f"openarm_left_joint{index}" for index in range(1, 8)]
    + ["openarm_left_finger_joint1", "openarm_left_finger_joint2"]
    + [f"openarm_right_joint{index}" for index in range(1, 8)]
    + ["openarm_right_finger_joint1", "openarm_right_finger_joint2"]
)
OUTPUT_HEADER = (
    "scenario",
    "scope",
    "entity_first",
    "entity_second",
    "signed_distance_m",
    "nearest_first_x_m",
    "nearest_first_y_m",
    "nearest_first_z_m",
    "nearest_second_x_m",
    "nearest_second_y_m",
    "nearest_second_z_m",
    "first_body_type",
    "second_body_type",
)
GEOMETRY_REPRESENTATION = "MoveIt RobotModel/FCL mesh geometry with frozen box world"
GEOMETRY_INVENTORY_SHA256 = "f16d63b2d7e2688ecc8a15b9db55c252c223c419f7030ea56d1712cacbad03a9"
ENTITY_ENVELOPES_SHA256 = "e04de36f57081bf0f5b88c3ea7578e7ba87c3909e91cc51655f1cb7301cb20d6"


def _canonical_json(value: object) -> bytes:
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


def sha256_path(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _regular_file(path: Path, label: str) -> Path:
    if path.is_symlink() or not path.is_file():
        raise ValueError(f"{label} must be a regular non-symlink file")
    return path


@dataclass(frozen=True)
class FrozenGeometryScope:
    scope_id: str
    inventory_file_sha256: str
    inventory_content_sha256: str
    envelope_file_sha256: str
    envelope_identity_sha256: str
    entities: tuple[GeometryEntitySpec, ...]
    pairs: tuple[CollisionPairSpec, ...]

    @property
    def checked_pairs(self) -> tuple[EntityPair, ...]:
        return tuple(
            item.entity_pair
            for item in self.pairs
            if item.disposition is PairDisposition.CHECKED
        )

    @property
    def probe_body_types(self) -> dict[str, str]:
        """Return the C++ probe's body-type vocabulary for every entity."""

        mapping = {
            GeometryEntityKind.FIXED_ROBOT: "robot-link",
            GeometryEntityKind.LEFT_LINK: "robot-link",
            GeometryEntityKind.RIGHT_LINK: "robot-link",
            GeometryEntityKind.ATTACHED_OBJECT: "attached-object",
            GeometryEntityKind.WORLD: "world",
        }
        return {item.entity_id: mapping[item.kind] for item in self.entities}

    def sha256(self) -> str:
        return hashlib.sha256(
            _canonical_json(
                {
                    "scope_id": self.scope_id,
                    "inventory_file_sha256": self.inventory_file_sha256,
                    "inventory_content_sha256": self.inventory_content_sha256,
                    "envelope_file_sha256": self.envelope_file_sha256,
                    "envelope_identity_sha256": self.envelope_identity_sha256,
                    "entities": [item.as_record() for item in self.entities],
                    "pairs": [item.as_record() for item in self.pairs],
                }
            )
        ).hexdigest()


def load_frozen_geometry_scope(
    *,
    inventory_path: Path,
    envelope_path: Path,
    scope_id: str = "robot-fixture-empty",
) -> FrozenGeometryScope:
    """Bind the EXP-S2-015 pair scope to EXP-S2-037 speed coefficients."""

    if sha256_path(_regular_file(inventory_path, "geometry inventory")) != GEOMETRY_INVENTORY_SHA256:
        raise ValueError("geometry inventory identity changed")
    if sha256_path(_regular_file(envelope_path, "entity envelopes")) != ENTITY_ENVELOPES_SHA256:
        raise ValueError("entity envelope identity changed")
    inventory = json.loads(inventory_path.read_text(encoding="utf-8"))
    envelopes = json.loads(envelope_path.read_text(encoding="utf-8"))
    scopes = [item for item in inventory["pair_scopes"] if item["scope_id"] == scope_id]
    if len(scopes) != 1:
        raise ValueError("frozen geometry scope is missing or duplicated")
    scope = scopes[0]
    inventory_entities = {item["entity_id"]: item for item in inventory["entities"]}
    envelope_entities = {item["entity_id"]: item for item in envelopes["entities"]}
    entity_ids = tuple(scope["entity_ids"])
    if entity_ids != tuple(sorted(entity_ids)) or set(entity_ids) != set(envelope_entities):
        raise ValueError("inventory and envelope entity universes differ")
    envelope_ref = "openarm-entity-speed-envelopes:" + envelopes["artifact_identity_sha256"]
    entities = []
    for entity_id in entity_ids:
        source = inventory_entities[entity_id]
        envelope = envelope_entities[entity_id]
        if source["motion_source"] != envelope["motion_source"]:
            raise ValueError("inventory and envelope motion source differ")
        entities.append(
            GeometryEntitySpec(
                entity_id=entity_id,
                kind=GeometryEntityKind(source["kind"]),
                geometry_ref=(
                    "openarm-geometry-inventory:"
                    + inventory["inventory_sha256"]
                    + ":"
                    + entity_id
                ),
                geometry_sha256=source["geometry_sha256"],
                motion_source=MotionSource(source["motion_source"]),
                left_joint_m_per_rad=tuple(envelope["left_joint_m_per_rad"]),
                right_joint_m_per_rad=tuple(envelope["right_joint_m_per_rad"]),
                left_gripper_m_per_m=envelope["left_gripper_m_per_m"],
                right_gripper_m_per_m=envelope["right_gripper_m_per_m"],
                envelope_evidence_ref=envelope_ref,
            )
        )
    pairs = tuple(
        CollisionPairSpec(
            entity_pair=tuple(item["entity_pair"]),
            disposition=PairDisposition(item["disposition"]),
            rationale=item["rationale"],
            evidence_ref=(
                "openarm-geometry-inventory:"
                + inventory["inventory_sha256"]
                + ":"
                + item["evidence_ref"]
            ),
        )
        for item in scope["pairs"]
    )
    if tuple(item.entity_pair for item in pairs) != tuple(
        sorted(item.entity_pair for item in pairs)
    ):
        raise ValueError("frozen geometry pairs are not canonically sorted")
    result = FrozenGeometryScope(
        scope_id=scope_id,
        inventory_file_sha256=GEOMETRY_INVENTORY_SHA256,
        inventory_content_sha256=inventory["inventory_sha256"],
        envelope_file_sha256=ENTITY_ENVELOPES_SHA256,
        envelope_identity_sha256=envelopes["artifact_identity_sha256"],
        entities=tuple(entities),
        pairs=pairs,
    )
    if len(result.entities) != 28 or len(result.pairs) != 368 or len(result.checked_pairs) != 340:
        raise ValueError("frozen RH100 geometry census changed")
    return result


class HashBoundFCLInputs:
    """Frozen process inputs with identities checked before every query."""

    def __init__(
        self,
        *,
        probe_binary: Path,
        probe_binary_sha256: str,
        urdf: Path,
        urdf_sha256: str,
        srdf: Path,
        srdf_sha256: str,
        world: Path,
        world_sha256: str,
        attachments: Path,
        attachments_sha256: str,
    ) -> None:
        self.probe_binary = Path(probe_binary)
        self.probe_binary_sha256 = probe_binary_sha256
        self.urdf = Path(urdf)
        self.urdf_sha256 = urdf_sha256
        self.srdf = Path(srdf)
        self.srdf_sha256 = srdf_sha256
        self.world = Path(world)
        self.world_sha256 = world_sha256
        self.attachments = Path(attachments)
        self.attachments_sha256 = attachments_sha256
        self.validate()

    def validate(self) -> None:
        for path, expected, label in (
            (self.probe_binary, self.probe_binary_sha256, "FCL probe"),
            (self.urdf, self.urdf_sha256, "resolved URDF"),
            (self.srdf, self.srdf_sha256, "active SRDF"),
            (self.world, self.world_sha256, "frozen world"),
            (self.attachments, self.attachments_sha256, "frozen attachments"),
        ):
            _regular_file(path, label)
            if sha256_path(path) != expected:
                raise ValueError(f"{label} identity changed")
        if not os.access(self.probe_binary, os.X_OK):
            raise ValueError("FCL probe is not executable")
        if self.probe_binary.name != "openarm_fcl_pair_probe":
            raise ValueError("FCL probe basename changed")

    @property
    def scene_sha256(self) -> str:
        return hashlib.sha256(
            _canonical_json(
                {
                    "world_sha256": self.world_sha256,
                    "attachments_sha256": self.attachments_sha256,
                }
            )
        ).hexdigest()

    @property
    def backend_config_sha256(self) -> str:
        return hashlib.sha256(
            _canonical_json(
                {
                    "probe_binary_sha256": self.probe_binary_sha256,
                    "request_type": "SINGLE",
                    "max_contacts_per_body": 1,
                    "enable_nearest_points": True,
                    "enable_signed_distance": True,
                    "distance_threshold": "double-max",
                    "state_variables": list(STATE_VARIABLES),
                }
            )
        ).hexdigest()

    def backend_identity(
        self, *, backend_version: str, pair_manifest_sha256: str
    ) -> GeometryBackendIdentity:
        return GeometryBackendIdentity(
            backend_name="MoveIt/FCL",
            backend_version=backend_version,
            distance_api="CollisionEnvFCL::distanceSelf+distanceRobot/SINGLE",
            backend_config_sha256=self.backend_config_sha256,
            geometry_representation=GEOMETRY_REPRESENTATION,
            robot_model_sha256=self.urdf_sha256,
            semantic_model_sha256=self.srdf_sha256,
            scene_sha256=self.scene_sha256,
            pair_manifest_sha256=pair_manifest_sha256,
        )


def _trajectory_positions(trajectory: Any, elapsed_ns: int) -> tuple[float, ...]:
    if not 0 <= elapsed_ns <= trajectory.duration_ns:
        raise ValueError("query time lies outside the frozen trajectory")
    for point in trajectory.points:
        if point.time_ns == elapsed_ns:
            return tuple(point.positions)
    segment = next(
        (
            item
            for item in trajectory.segments()
            if item.start_ns < elapsed_ns < item.end_ns
        ),
        None,
    )
    if segment is None:
        raise ValueError("query time does not resolve to a trajectory segment")
    positions, _velocities, _accelerations = segment.evaluate(
        elapsed_ns - segment.start_ns
    )
    return tuple(positions)


def rh100_variable_positions(
    environment: SearchEnvironment,
    state: SearchState,
    time_ns: int,
    active: Sequence[ActiveAction],
) -> dict[str, float]:
    """Evaluate the frozen RH100 move/wait state at one integer-ns time."""

    active_tuple = tuple(active)
    if active_tuple != state.active:
        raise ValueError("query active actions differ from the explicit search state")
    if time_ns < state.time_ns:
        raise ValueError("query time precedes the explicit search state")
    if active_tuple and time_ns > min(action.end_ns for action in active_tuple):
        raise ValueError("query time exceeds the current event interval")
    if not active_tuple and time_ns != state.time_ns:
        raise ValueError("an event state can only be queried at its own time")

    left = state.world.left_q.positions()
    right = state.world.right_q.positions()
    trajectories = dict(environment.trajectories)
    for action in active_tuple:
        if action.kind is ActionKind.WAIT:
            continue
        if action.kind is not ActionKind.MOVE:
            raise ValueError("RH100 FCL adapter supports only frozen move/wait actions")
        if not action.trajectory_hash or action.trajectory_hash not in trajectories:
            raise ValueError("active move has no frozen trajectory")
        positions = _trajectory_positions(
            trajectories[action.trajectory_hash], time_ns - action.start_ns
        )
        if action.arm is Arm.LEFT:
            left = positions
        else:
            right = positions

    values = {
        **{f"openarm_left_joint{index + 1}": value for index, value in enumerate(left)},
        **{f"openarm_right_joint{index + 1}": value for index, value in enumerate(right)},
    }
    left_gripper = state.world.left_gripper.position()
    right_gripper = state.world.right_gripper.position()
    for index in (1, 2):
        values[f"openarm_left_finger_joint{index}"] = left_gripper
        values[f"openarm_right_finger_joint{index}"] = right_gripper
    if set(values) != set(STATE_VARIABLES):
        raise AssertionError("RH100 state variable construction is incomplete")
    if any(not math.isfinite(value) for value in values.values()):
        raise ValueError("RH100 query state contains a non-finite value")
    return values


def _state_tsv_bytes(
    scenario: str, positions: Mapping[str, float]
) -> bytes:
    if not scenario or any(character in scenario for character in "\t\r\n"):
        raise ValueError("query scenario must be one safe TSV field")
    header = ("scenario",) + STATE_VARIABLES
    row = (scenario,) + tuple(format(positions[name], ".17g") for name in STATE_VARIABLES)
    return ("\t".join(header) + "\n" + "\t".join(row) + "\n").encode("ascii")


def _write_exclusive(path: Path, value: bytes) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    descriptor = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o644)
    with os.fdopen(descriptor, "wb") as stream:
        stream.write(value)


ProcessRunner = Callable[..., Any]


class RH100FCLProcessQuery:
    """Callable SignedDistanceQuery using one hash-bound probe process per point."""

    def __init__(
        self,
        *,
        environment: SearchEnvironment,
        inputs: HashBoundFCLInputs,
        backend_identity: GeometryBackendIdentity,
        expected_pairs: Iterable[EntityPair],
        expected_body_types: Mapping[str, str],
        timeout_s: float = 120.0,
        evidence_dir: Optional[Path] = None,
        process_runner: ProcessRunner = subprocess.run,
    ) -> None:
        pairs = tuple(sorted(canonical_entity_pair(*pair) for pair in expected_pairs))
        if not pairs or len(pairs) != len(set(pairs)):
            raise ValueError("FCL query requires a non-empty unique pair set")
        entity_ids = {entity for pair in pairs for entity in pair}
        body_types = dict(expected_body_types)
        if set(body_types) != entity_ids:
            raise ValueError("FCL body-type contract differs from the pair universe")
        if any(
            value not in {"robot-link", "attached-object", "world"}
            for value in body_types.values()
        ):
            raise ValueError("FCL body-type contract uses an unknown probe type")
        if timeout_s <= 0.0:
            raise ValueError("FCL query timeout must be positive")
        if backend_identity.robot_model_sha256 != inputs.urdf_sha256:
            raise ValueError("backend identity and URDF differ")
        if backend_identity.semantic_model_sha256 != inputs.srdf_sha256:
            raise ValueError("backend identity and SRDF differ")
        if backend_identity.scene_sha256 != inputs.scene_sha256:
            raise ValueError("backend identity and scene differ")
        if backend_identity.backend_config_sha256 != inputs.backend_config_sha256:
            raise ValueError("backend identity and probe configuration differ")
        self.environment = environment
        self.inputs = inputs
        self.backend_identity = backend_identity
        self.expected_pairs = pairs
        self.expected_body_types = body_types
        self.timeout_s = timeout_s
        self.evidence_dir = Path(evidence_dir) if evidence_dir is not None else None
        self.process_runner = process_runner
        self._query_count = 0
        self._audit_records: list[dict[str, object]] = []

    def audit_records(self) -> tuple[dict[str, object], ...]:
        return tuple(json.loads(json.dumps(item)) for item in self._audit_records)

    def __call__(
        self,
        state: SearchState,
        time_ns: int,
        active: tuple[ActiveAction, ...],
        requested_pairs: tuple[EntityPair, ...],
    ) -> tuple[SignedDistanceObservation, ...]:
        self.inputs.validate()
        requested = tuple(sorted(canonical_entity_pair(*pair) for pair in requested_pairs))
        if requested != self.expected_pairs:
            raise ValueError("query pair request differs from the frozen pair universe")
        positions = rh100_variable_positions(self.environment, state, time_ns, active)
        state_ref = state_fingerprint(state)
        scenario = f"rh100_q{self._query_count:04d}_{time_ns}_{state_ref[:12]}"
        state_bytes = _state_tsv_bytes(scenario, positions)

        temporary: Optional[tempfile.TemporaryDirectory[str]] = None
        if self.evidence_dir is None:
            temporary = tempfile.TemporaryDirectory(prefix="bisafecode-rh100-fcl-")
            query_root = Path(temporary.name)
        else:
            query_root = self.evidence_dir / f"query_{self._query_count:04d}"
            query_root.mkdir(parents=True, exist_ok=False)
        state_path = query_root / "state.tsv"
        output_path = query_root / "distances.tsv"
        _write_exclusive(state_path, state_bytes)
        command = [
            str(self.inputs.probe_binary),
            str(self.inputs.urdf),
            str(self.inputs.srdf),
            str(state_path),
            str(self.inputs.world),
            str(self.inputs.attachments),
            str(output_path),
        ]
        try:
            completed = self.process_runner(
                command,
                check=False,
                capture_output=True,
                timeout=self.timeout_s,
            )
            stdout = bytes(completed.stdout)
            stderr = bytes(completed.stderr)
            if self.evidence_dir is not None:
                _write_exclusive(query_root / "stdout.json", stdout)
                _write_exclusive(query_root / "stderr.txt", stderr)
                _write_exclusive(
                    query_root / "exit_code.txt",
                    f"{completed.returncode}\n".encode("ascii"),
                )
            if completed.returncode != 0:
                raise RuntimeError(f"FCL probe returned {completed.returncode}")
            try:
                reported = json.loads(stdout.decode("utf-8").strip())
            except (UnicodeDecodeError, json.JSONDecodeError) as error:
                raise RuntimeError("FCL probe stdout is not one JSON object") from error
            required = {
                "status": "PASS",
                "request_type": "SINGLE",
                "scenarios": 1,
                "distance_rows": len(self.expected_pairs),
                "signed_distance": True,
                "nearest_points": True,
            }
            if any(reported.get(key) != value for key, value in required.items()):
                raise RuntimeError("FCL probe capability/count report changed")
            observations = self._parse_output(
                output_path, scenario, time_ns, state_ref
            )
            audit = {
                "schema": "bisafecode.rh100-fcl-query-audit/v0.1",
                "query_index": self._query_count,
                "scenario": scenario,
                "time_ns": time_ns,
                "state_fingerprint": state_ref,
                "active_nodes": [item.node_id for item in active],
                "state_tsv_sha256": hashlib.sha256(state_bytes).hexdigest(),
                "distance_tsv_sha256": sha256_path(output_path),
                "stdout_sha256": hashlib.sha256(stdout).hexdigest(),
                "stderr_sha256": hashlib.sha256(stderr).hexdigest(),
                "observation_count": len(observations),
                "backend_identity_sha256": self.backend_identity.sha256(),
                "paper_result_eligible": False,
            }
            self._audit_records.append(audit)
            if self.evidence_dir is not None:
                _write_exclusive(query_root / "query_audit.json", _canonical_json(audit))
            self._query_count += 1
            return observations
        finally:
            if temporary is not None:
                temporary.cleanup()

    def _parse_output(
        self,
        output_path: Path,
        scenario: str,
        time_ns: int,
        state_ref: str,
    ) -> tuple[SignedDistanceObservation, ...]:
        return parse_fcl_distance_output(
            output_path=output_path,
            scenario=scenario,
            time_ns=time_ns,
            state_ref=state_ref,
            backend_ref=self.backend_identity.sha256(),
            expected_pairs=self.expected_pairs,
            expected_body_types=self.expected_body_types,
        )


def parse_fcl_distance_output(
    *,
    output_path: Path,
    scenario: str,
    time_ns: int,
    state_ref: str,
    backend_ref: str,
    expected_pairs: Iterable[EntityPair],
    expected_body_types: Mapping[str, str],
) -> tuple[SignedDistanceObservation, ...]:
    """Parse one existing probe result without executing a backend process."""

    pairs = tuple(sorted(canonical_entity_pair(*pair) for pair in expected_pairs))
    if not pairs or len(pairs) != len(set(pairs)):
        raise ValueError("FCL output parser requires a unique pair universe")
    body_types = dict(expected_body_types)
    if set(body_types) != {entity for pair in pairs for entity in pair}:
        raise ValueError("FCL output body-type contract is incomplete")
    _regular_file(output_path, "FCL distance output")
    lines = output_path.read_text(encoding="utf-8").splitlines()
    if not lines or tuple(lines[0].split("\t")) != OUTPUT_HEADER:
        raise ValueError("FCL output header changed")
    result = []
    seen = set()
    for line in lines[1:]:
        fields = line.split("\t")
        if len(fields) != len(OUTPUT_HEADER):
            raise ValueError("FCL output row width changed")
        row = dict(zip(OUTPUT_HEADER, fields))
        if row["scenario"] != scenario or row["scope"] not in {"self", "world"}:
            raise ValueError("FCL output scenario or scope changed")
        pair = canonical_entity_pair(row["entity_first"], row["entity_second"])
        if pair != (row["entity_first"], row["entity_second"]):
            raise ValueError("FCL output pair is not canonical")
        if pair in seen:
            raise ValueError("FCL output contains a duplicate pair")
        seen.add(pair)
        reported_types = (row["first_body_type"], row["second_body_type"])
        required_types = (body_types[pair[0]], body_types[pair[1]])
        if reported_types != required_types:
            raise ValueError("FCL output body type differs from the frozen entity scope")
        required_scope = "world" if "world" in required_types else "self"
        if row["scope"] != required_scope:
            raise ValueError("FCL output scope and body types disagree")
        values = tuple(
            float(row[name])
            for name in (
                "signed_distance_m",
                "nearest_first_x_m",
                "nearest_first_y_m",
                "nearest_first_z_m",
                "nearest_second_x_m",
                "nearest_second_y_m",
                "nearest_second_z_m",
            )
        )
        if any(not math.isfinite(value) for value in values):
            raise ValueError("FCL output contains a non-finite value")
        result.append(
            SignedDistanceObservation(
                pair,
                time_ns,
                values[0],
                state_ref,
                backend_ref,
                values[1:4],
                values[4:7],
            )
        )
    if seen != set(pairs):
        raise ValueError("FCL output pair coverage differs from the frozen universe")
    return tuple(sorted(result, key=lambda item: item.entity_pair))
