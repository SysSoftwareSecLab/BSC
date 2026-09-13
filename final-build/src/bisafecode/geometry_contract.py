"""Fail-closed contract between OpenArm geometry evidence and the verifier.

The continuous-collision kernel cannot infer which collision pairs exist, how
signed distance was computed, or whether entity-speed envelopes are sound.
This module makes those obligations explicit and content-addressed.  It does
not implement FCL or MuJoCo; an Ubuntu backend must satisfy this contract.
"""

from __future__ import annotations

import hashlib
import itertools
import json
import math
from dataclasses import dataclass
from enum import Enum
from typing import Callable, Iterable, Optional, Tuple

from .continuous_collision import (
    AdaptiveDistanceSpeedChecker,
    CollisionCertificatePolicy,
    EntityPair,
    PairDistanceSample,
    canonical_entity_pair,
)
from .explicit_state import (
    ActiveAction,
    SearchEnvironment,
    SearchState,
    search_environment_sha256,
    state_fingerprint,
)
from .motion_bounds import (
    EntitySpeedEnvelope,
    EnvelopeClosureSpeedBounder,
    FrozenTrajectoryRateProvider,
    ZERO_ARM_RATE,
)


SHA256_LENGTH = 64
SIGNED_DISTANCE_CONVENTION = "positive-separated-zero-touching-negative-penetration"


def _require_sha256(value: str, name: str) -> None:
    if len(value) != SHA256_LENGTH or any(character not in "0123456789abcdef" for character in value):
        raise ValueError(f"{name} must be a lowercase SHA-256 hex digest")


def _canonical_json(value: dict) -> bytes:
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


def _hash_record(prefix: str, value: dict) -> str:
    return prefix + hashlib.sha256(_canonical_json(value)).hexdigest()


def _validate_nonnegative_finite(value: float, name: str) -> None:
    if not math.isfinite(value) or value < 0.0:
        raise ValueError(f"{name} must be finite and non-negative")


class GeometryEntityKind(str, Enum):
    FIXED_ROBOT = "fixed-robot"
    LEFT_LINK = "left-link"
    RIGHT_LINK = "right-link"
    ATTACHED_OBJECT = "attached-object"
    WORLD = "world"


class MotionSource(str, Enum):
    LEFT_CHAIN = "left-chain"
    RIGHT_CHAIN = "right-chain"
    BIMANUAL = "bimanual"
    STATIC = "static"


class PairDisposition(str, Enum):
    CHECKED = "checked"
    EXCLUDED = "excluded"
    ALLOWED_CONTACT = "allowed-contact"


@dataclass(frozen=True)
class GeometryEntitySpec:
    entity_id: str
    kind: GeometryEntityKind
    geometry_ref: str
    geometry_sha256: str
    motion_source: MotionSource
    left_joint_m_per_rad: Tuple[float, ...] = ZERO_ARM_RATE
    right_joint_m_per_rad: Tuple[float, ...] = ZERO_ARM_RATE
    left_gripper_m_per_m: float = 0.0
    right_gripper_m_per_m: float = 0.0
    envelope_evidence_ref: str = ""

    def __post_init__(self) -> None:
        if not self.entity_id or not self.geometry_ref:
            raise ValueError("geometry entity requires identifiers")
        _require_sha256(self.geometry_sha256, "geometry_sha256")
        for name, vector in (
            ("left_joint_m_per_rad", self.left_joint_m_per_rad),
            ("right_joint_m_per_rad", self.right_joint_m_per_rad),
        ):
            if len(vector) != 7:
                raise ValueError(f"{name} must contain seven OpenArm coefficients")
            for value in vector:
                _validate_nonnegative_finite(value, name)
        _validate_nonnegative_finite(self.left_gripper_m_per_m, "left_gripper_m_per_m")
        _validate_nonnegative_finite(self.right_gripper_m_per_m, "right_gripper_m_per_m")
        if not self.envelope_evidence_ref:
            raise ValueError("geometry entity requires envelope evidence")
        left_moves = any(self.left_joint_m_per_rad) or self.left_gripper_m_per_m > 0.0
        right_moves = any(self.right_joint_m_per_rad) or self.right_gripper_m_per_m > 0.0
        if self.motion_source is MotionSource.STATIC and (left_moves or right_moves):
            raise ValueError("static entity cannot have a nonzero motion envelope")
        if self.motion_source is MotionSource.LEFT_CHAIN and right_moves:
            raise ValueError("left-chain entity cannot depend on right-arm rates")
        if self.motion_source is MotionSource.RIGHT_CHAIN and left_moves:
            raise ValueError("right-chain entity cannot depend on left-arm rates")
        if self.kind is GeometryEntityKind.WORLD and self.motion_source is not MotionSource.STATIC:
            raise ValueError("world geometry must be static in Paper 1")
        if self.kind is GeometryEntityKind.FIXED_ROBOT and self.motion_source is not MotionSource.STATIC:
            raise ValueError("fixed robot geometry must be static")
        if self.kind is GeometryEntityKind.LEFT_LINK and self.motion_source is not MotionSource.LEFT_CHAIN:
            raise ValueError("left-link entity must use the left chain")
        if self.kind is GeometryEntityKind.RIGHT_LINK and self.motion_source is not MotionSource.RIGHT_CHAIN:
            raise ValueError("right-link entity must use the right chain")

    def speed_envelope(self) -> EntitySpeedEnvelope:
        return EntitySpeedEnvelope(
            self.entity_id,
            self.left_joint_m_per_rad,
            self.right_joint_m_per_rad,
            self.left_gripper_m_per_m,
            self.right_gripper_m_per_m,
            self.envelope_evidence_ref,
        )

    def as_record(self) -> dict:
        return {
            "entity_id": self.entity_id,
            "kind": self.kind.value,
            "geometry_ref": self.geometry_ref,
            "geometry_sha256": self.geometry_sha256,
            "motion_source": self.motion_source.value,
            "left_joint_m_per_rad": list(self.left_joint_m_per_rad),
            "right_joint_m_per_rad": list(self.right_joint_m_per_rad),
            "left_gripper_m_per_m": self.left_gripper_m_per_m,
            "right_gripper_m_per_m": self.right_gripper_m_per_m,
            "envelope_evidence_ref": self.envelope_evidence_ref,
        }


@dataclass(frozen=True)
class CollisionPairSpec:
    entity_pair: EntityPair
    disposition: PairDisposition
    rationale: str
    evidence_ref: str
    condition_ref: Optional[str] = None

    def __post_init__(self) -> None:
        if self.entity_pair != canonical_entity_pair(*self.entity_pair):
            raise ValueError("collision pair must be canonical")
        if not self.rationale or not self.evidence_ref:
            raise ValueError("collision pair requires rationale and evidence")
        if self.disposition is PairDisposition.CHECKED and self.condition_ref is not None:
            raise ValueError("checked pair cannot be conditionally omitted")
        if self.disposition is PairDisposition.ALLOWED_CONTACT and not self.condition_ref:
            raise ValueError("allowed contact requires a predeclared condition")
        if self.disposition is PairDisposition.EXCLUDED and self.condition_ref is not None:
            raise ValueError("excluded pair cannot carry a runtime condition")

    def as_record(self) -> dict:
        return {
            "entity_pair": list(self.entity_pair),
            "disposition": self.disposition.value,
            "rationale": self.rationale,
            "evidence_ref": self.evidence_ref,
            "condition_ref": self.condition_ref,
        }


@dataclass(frozen=True)
class GeometryEvidenceManifest:
    environment_hash: str
    resolved_environment_sha256: str
    robot_model_sha256: str
    semantic_model_sha256: str
    scene_sha256: str
    geometry_representation: str
    entities: Tuple[GeometryEntitySpec, ...]
    pairs: Tuple[CollisionPairSpec, ...]
    required_clearance_m: float
    required_clearance_evidence_ref: str
    distance_error_bound_m: float
    distance_error_evidence_ref: str
    geometry_error_bound_m: float
    geometry_error_evidence_ref: str
    interpolation_error_bound_m: float
    interpolation_error_evidence_ref: str
    max_subdivisions: int
    min_interval_ns: int
    schema: str = "bisafecode.openarm-geometry-contract/v0.2"

    def __post_init__(self) -> None:
        for name, digest in (
            ("environment_hash", self.environment_hash),
            ("resolved_environment_sha256", self.resolved_environment_sha256),
            ("robot_model_sha256", self.robot_model_sha256),
            ("semantic_model_sha256", self.semantic_model_sha256),
            ("scene_sha256", self.scene_sha256),
        ):
            _require_sha256(digest, name)
        if not self.geometry_representation:
            raise ValueError("geometry representation must be named")
        for name, value in (
            ("required_clearance_m", self.required_clearance_m),
            ("distance_error_bound_m", self.distance_error_bound_m),
            ("geometry_error_bound_m", self.geometry_error_bound_m),
            ("interpolation_error_bound_m", self.interpolation_error_bound_m),
        ):
            _validate_nonnegative_finite(value, name)
        for name, reference in (
            ("required_clearance_evidence_ref", self.required_clearance_evidence_ref),
            ("distance_error_evidence_ref", self.distance_error_evidence_ref),
            ("geometry_error_evidence_ref", self.geometry_error_evidence_ref),
            ("interpolation_error_evidence_ref", self.interpolation_error_evidence_ref),
        ):
            if not reference:
                raise ValueError(f"{name} is required")
        if isinstance(self.max_subdivisions, bool) or not isinstance(self.max_subdivisions, int) or self.max_subdivisions < 0:
            raise ValueError("max_subdivisions must be a non-negative integer")
        if isinstance(self.min_interval_ns, bool) or not isinstance(self.min_interval_ns, int) or self.min_interval_ns <= 0:
            raise ValueError("min_interval_ns must be a positive integer")
        entity_ids = tuple(entity.entity_id for entity in self.entities)
        if not entity_ids or entity_ids != tuple(sorted(entity_ids)) or len(entity_ids) != len(set(entity_ids)):
            raise ValueError("geometry entities must be non-empty, unique, and sorted")
        pair_ids = tuple(pair.entity_pair for pair in self.pairs)
        if pair_ids != tuple(sorted(pair_ids)) or len(pair_ids) != len(set(pair_ids)):
            raise ValueError("collision pairs must be unique and sorted")
        by_id = {entity.entity_id: entity for entity in self.entities}
        expected = {
            canonical_entity_pair(first.entity_id, second.entity_id)
            for first, second in itertools.combinations(self.entities, 2)
            if not (
                first.kind is GeometryEntityKind.WORLD
                and second.kind is GeometryEntityKind.WORLD
            )
        }
        actual = set(pair_ids)
        if actual != expected:
            missing = sorted(expected - actual)
            extra = sorted(actual - expected)
            raise ValueError(f"collision-pair universe mismatch: missing={missing}, extra={extra}")
        if any(name not in by_id for pair in pair_ids for name in pair):
            raise ValueError("collision pair references an unknown entity")
        if not self.checked_pairs:
            raise ValueError("manifest must check at least one collision pair")

    @property
    def checked_pairs(self) -> Tuple[EntityPair, ...]:
        return tuple(
            pair.entity_pair
            for pair in self.pairs
            if pair.disposition is PairDisposition.CHECKED
        )

    @property
    def effective_required_clearance_m(self) -> float:
        total = self.required_clearance_m + self.total_error_bound_m
        return math.nextafter(total, math.inf)

    @property
    def total_error_bound_m(self) -> float:
        total = (
            self.distance_error_bound_m
            + self.geometry_error_bound_m
            + self.interpolation_error_bound_m
        )
        return math.nextafter(total, math.inf)

    def entity(self, entity_id: str) -> GeometryEntitySpec:
        for entity in self.entities:
            if entity.entity_id == entity_id:
                return entity
        raise KeyError(entity_id)

    def as_record(self) -> dict:
        return {
            "schema": self.schema,
            "environment_hash": self.environment_hash,
            "resolved_environment_sha256": self.resolved_environment_sha256,
            "robot_model_sha256": self.robot_model_sha256,
            "semantic_model_sha256": self.semantic_model_sha256,
            "scene_sha256": self.scene_sha256,
            "geometry_representation": self.geometry_representation,
            "entities": [entity.as_record() for entity in self.entities],
            "pairs": [pair.as_record() for pair in self.pairs],
            "certificate_policy": {
                "required_clearance_m": self.required_clearance_m,
                "required_clearance_evidence_ref": self.required_clearance_evidence_ref,
                "distance_error_bound_m": self.distance_error_bound_m,
                "distance_error_evidence_ref": self.distance_error_evidence_ref,
                "geometry_error_bound_m": self.geometry_error_bound_m,
                "geometry_error_evidence_ref": self.geometry_error_evidence_ref,
                "interpolation_error_bound_m": self.interpolation_error_bound_m,
                "interpolation_error_evidence_ref": self.interpolation_error_evidence_ref,
                "total_error_bound_m": self.total_error_bound_m,
                "effective_required_clearance_m": self.effective_required_clearance_m,
                "max_subdivisions": self.max_subdivisions,
                "min_interval_ns": self.min_interval_ns,
            },
        }

    def sha256(self) -> str:
        return hashlib.sha256(_canonical_json(self.as_record())).hexdigest()


@dataclass(frozen=True)
class GeometryBackendIdentity:
    backend_name: str
    backend_version: str
    distance_api: str
    backend_config_sha256: str
    geometry_representation: str
    robot_model_sha256: str
    semantic_model_sha256: str
    scene_sha256: str
    pair_manifest_sha256: str
    signed_distance_convention: str = SIGNED_DISTANCE_CONVENTION

    def __post_init__(self) -> None:
        if not self.backend_name or not self.backend_version or not self.distance_api:
            raise ValueError("geometry backend identity is incomplete")
        if not self.geometry_representation:
            raise ValueError("geometry backend representation is required")
        for name, digest in (
            ("robot_model_sha256", self.robot_model_sha256),
            ("semantic_model_sha256", self.semantic_model_sha256),
            ("scene_sha256", self.scene_sha256),
            ("pair_manifest_sha256", self.pair_manifest_sha256),
            ("backend_config_sha256", self.backend_config_sha256),
        ):
            _require_sha256(digest, name)
        if self.signed_distance_convention != SIGNED_DISTANCE_CONVENTION:
            raise ValueError("unsupported signed-distance convention")

    def as_record(self) -> dict:
        return {
            "backend_name": self.backend_name,
            "backend_version": self.backend_version,
            "distance_api": self.distance_api,
            "backend_config_sha256": self.backend_config_sha256,
            "geometry_representation": self.geometry_representation,
            "robot_model_sha256": self.robot_model_sha256,
            "semantic_model_sha256": self.semantic_model_sha256,
            "scene_sha256": self.scene_sha256,
            "pair_manifest_sha256": self.pair_manifest_sha256,
            "signed_distance_convention": self.signed_distance_convention,
        }

    def sha256(self) -> str:
        return hashlib.sha256(_canonical_json(self.as_record())).hexdigest()


@dataclass(frozen=True)
class SignedDistanceObservation:
    entity_pair: EntityPair
    time_ns: int
    signed_distance_m: float
    state_fingerprint: str
    backend_identity_sha256: str
    nearest_point_first_m: Optional[Tuple[float, float, float]] = None
    nearest_point_second_m: Optional[Tuple[float, float, float]] = None

    def __post_init__(self) -> None:
        if self.entity_pair != canonical_entity_pair(*self.entity_pair):
            raise ValueError("distance observation pair must be canonical")
        if isinstance(self.time_ns, bool) or not isinstance(self.time_ns, int) or self.time_ns < 0:
            raise ValueError("distance observation requires non-negative integer time")
        if not math.isfinite(self.signed_distance_m):
            raise ValueError("distance observation must be finite")
        _require_sha256(self.state_fingerprint, "state_fingerprint")
        _require_sha256(self.backend_identity_sha256, "backend_identity_sha256")
        points = (self.nearest_point_first_m, self.nearest_point_second_m)
        if (points[0] is None) != (points[1] is None):
            raise ValueError("nearest points must be both present or both absent")
        for point in points:
            if point is not None and (len(point) != 3 or any(not math.isfinite(value) for value in point)):
                raise ValueError("nearest point must contain three finite coordinates")

    def as_record(self) -> dict:
        return {
            "schema": "bisafecode.signed-distance-observation/v0.1",
            "entity_pair": list(self.entity_pair),
            "time_ns": self.time_ns,
            "signed_distance_m": self.signed_distance_m,
            "state_fingerprint": self.state_fingerprint,
            "backend_identity_sha256": self.backend_identity_sha256,
            "nearest_point_first_m": list(self.nearest_point_first_m) if self.nearest_point_first_m is not None else None,
            "nearest_point_second_m": list(self.nearest_point_second_m) if self.nearest_point_second_m is not None else None,
        }

    def evidence_ref(self) -> str:
        return _hash_record("signed-distance:", self.as_record())


SignedDistanceQuery = Callable[
    [SearchState, int, Tuple[ActiveAction, ...], Tuple[EntityPair, ...]],
    Iterable[SignedDistanceObservation],
]


class ManifestSignedDistanceSampler:
    """Validate a robot backend response before it reaches the certificate kernel."""

    def __init__(
        self,
        *,
        manifest: GeometryEvidenceManifest,
        backend_identity: GeometryBackendIdentity,
        query: SignedDistanceQuery,
    ) -> None:
        if backend_identity.robot_model_sha256 != manifest.robot_model_sha256:
            raise ValueError("backend robot model does not match geometry manifest")
        if backend_identity.semantic_model_sha256 != manifest.semantic_model_sha256:
            raise ValueError("backend semantic model does not match geometry manifest")
        if backend_identity.scene_sha256 != manifest.scene_sha256:
            raise ValueError("backend scene does not match geometry manifest")
        if backend_identity.geometry_representation != manifest.geometry_representation:
            raise ValueError("backend geometry representation does not match manifest")
        if backend_identity.pair_manifest_sha256 != manifest.sha256():
            raise ValueError("backend pair-manifest hash does not match manifest content")
        self.manifest = manifest
        self.backend_identity = backend_identity
        self.query = query

    def __call__(
        self,
        state: SearchState,
        time_ns: int,
        active: Tuple[ActiveAction, ...],
    ) -> Tuple[PairDistanceSample, ...]:
        if state.environment_hash != self.manifest.environment_hash:
            raise ValueError("search state and geometry manifest environment hashes differ")
        expected_state = state_fingerprint(state)
        expected_backend = self.backend_identity.sha256()
        observations = tuple(self.query(state, time_ns, active, self.manifest.checked_pairs))
        by_pair = {observation.entity_pair: observation for observation in observations}
        if len(by_pair) != len(observations):
            raise ValueError("geometry backend returned a duplicate collision pair")
        if set(by_pair) != set(self.manifest.checked_pairs):
            raise ValueError("geometry backend pair coverage does not match the manifest")
        samples = []
        for pair in self.manifest.checked_pairs:
            observation = by_pair[pair]
            if observation.time_ns != time_ns:
                raise ValueError("geometry backend returned the wrong query time")
            if observation.state_fingerprint != expected_state:
                raise ValueError("geometry backend returned the wrong state fingerprint")
            if observation.backend_identity_sha256 != expected_backend:
                raise ValueError("geometry backend identity changed during the query")
            samples.append(
                PairDistanceSample(
                    pair,
                    time_ns,
                    observation.signed_distance_m,
                    observation.evidence_ref(),
                )
            )
        return tuple(samples)


def build_manifest_collision_checker(
    *,
    environment: SearchEnvironment,
    manifest: GeometryEvidenceManifest,
    backend_identity: GeometryBackendIdentity,
    query: SignedDistanceQuery,
) -> AdaptiveDistanceSpeedChecker:
    """Build the continuous checker only after all geometry obligations bind."""

    if environment.environment_hash != manifest.environment_hash:
        raise ValueError("search environment and geometry manifest hashes differ")
    if search_environment_sha256(environment) != manifest.resolved_environment_sha256:
        raise ValueError("resolved search environment does not match geometry manifest")
    if any(
        pair.disposition is PairDisposition.ALLOWED_CONTACT
        for pair in manifest.pairs
    ):
        raise ValueError(
            "runtime allowed-contact conditions are not implemented; "
            "use a fixed case-scoped exclusion with audited evidence or return unknown"
        )
    distance_sampler = ManifestSignedDistanceSampler(
        manifest=manifest,
        backend_identity=backend_identity,
        query=query,
    )
    rate_provider = FrozenTrajectoryRateProvider(environment)
    closure_speed_bounder = EnvelopeClosureSpeedBounder(
        configured_pairs=manifest.checked_pairs,
        rate_provider=rate_provider,
        envelope_resolver=lambda _state, entity_id: manifest.entity(entity_id).speed_envelope(),
    )
    policy = CollisionCertificatePolicy(
        manifest.required_clearance_m,
        manifest.max_subdivisions,
        manifest.min_interval_ns,
        collision_error_bound_m=manifest.total_error_bound_m,
        collision_error_evidence_ref=(
            "geometry-manifest-error-envelope:"
            + hashlib.sha256(
                _canonical_json(
                    {
                        "distance_error_bound_m": manifest.distance_error_bound_m,
                        "distance_error_evidence_ref": manifest.distance_error_evidence_ref,
                        "geometry_error_bound_m": manifest.geometry_error_bound_m,
                        "geometry_error_evidence_ref": manifest.geometry_error_evidence_ref,
                        "interpolation_error_bound_m": manifest.interpolation_error_bound_m,
                        "interpolation_error_evidence_ref": manifest.interpolation_error_evidence_ref,
                    }
                )
            ).hexdigest()
        ),
    )
    return AdaptiveDistanceSpeedChecker(
        configured_pairs=manifest.checked_pairs,
        distance_sampler=distance_sampler,
        closure_speed_bounder=closure_speed_bounder,
        policy=policy,
    )
