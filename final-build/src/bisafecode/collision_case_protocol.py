"""Validation for the preregistered C1--C4 candidate-selection protocol.

The protocol deliberately separates construction from label authority.  A
MoveIt/FCL screen may be used to find informative canonical trajectories, but
it cannot establish the independent experimental label later used to assess
the verifier.
"""

from __future__ import annotations

import hashlib
import json
import math
from pathlib import Path, PurePosixPath
from dataclasses import dataclass
from typing import Any, Mapping, Tuple


SCHEMA = "bisafecode.collision-case-selection-protocol/v0.1"
STATUS = "PREREGISTERED_NOT_EXECUTED"
EXPECTED_CASES = {
    "C1": ("safe", "robot-fixture-empty", False),
    "C2": ("safe", "robot-fixture-carried-base", True),
    "C3": ("unsafe", "robot-fixture-empty", False),
    "C4": ("unsafe", "robot-fixture-carried-base", True),
}
AMENDMENT_SCHEMA = "bisafecode.collision-case-selection-protocol-amendment/v0.2"
AMENDMENT_STATUS = "FROZEN_BEFORE_C1_C4_SCREENING"


def _require_sha256(value: Any, field: str) -> str:
    if not isinstance(value, str) or len(value) != 64:
        raise ValueError(f"{field} must be a lowercase SHA-256 digest")
    if any(character not in "0123456789abcdef" for character in value):
        raise ValueError(f"{field} must be a lowercase SHA-256 digest")
    return value


def _positive_finite(value: Any, field: str) -> float:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise ValueError(f"{field} must be numeric")
    result = float(value)
    if not math.isfinite(result) or result <= 0.0:
        raise ValueError(f"{field} must be finite and positive")
    return result


def _canonical_json(record: Mapping[str, Any]) -> bytes:
    return (
        json.dumps(
            record,
            sort_keys=True,
            separators=(",", ":"),
            ensure_ascii=True,
            allow_nan=False,
        )
        + "\n"
    ).encode("ascii")


@dataclass(frozen=True)
class CollisionCaseSelectionProtocol:
    """A validated, content-addressed preregistration record."""

    record: Mapping[str, Any]

    @classmethod
    def from_record(cls, record: Mapping[str, Any]) -> "CollisionCaseSelectionProtocol":
        value = cls(record)
        value.validate()
        return value

    def validate(self) -> None:
        if self.record.get("schema") != SCHEMA:
            raise ValueError("unsupported collision selection protocol schema")
        if self.record.get("status") != STATUS:
            raise ValueError("selection protocol must remain preregistered and unexecuted")
        if self.record.get("paper_result_eligible") is not False:
            raise ValueError("selection protocol is not a paper performance result")

        identity = self.record.get("source_identity")
        if not isinstance(identity, Mapping):
            raise ValueError("source_identity is required")
        required_hashes = {
            "resolved_urdf_sha256",
            "active_srdf_sha256",
            "fixture_scene_sha256",
            "geometry_inventory_file_sha256",
            "geometry_inventory_content_sha256",
            "candidate_basis_archive_sha256",
            "fcl_backend_evidence_archive_sha256",
        }
        if set(identity) != required_hashes:
            raise ValueError("source_identity must contain the exact frozen hash set")
        for field in sorted(required_hashes):
            _require_sha256(identity[field], field)

        selection = self.record.get("selection_discipline")
        if not isinstance(selection, Mapping):
            raise ValueError("selection_discipline is required")
        if selection.get("selection_backend_role") != "candidate-screening-only":
            raise ValueError("FCL must be restricted to candidate screening")
        if selection.get("label_authority") != "independent-oracle-after-freeze":
            raise ValueError("an independent post-freeze oracle must own labels")
        if selection.get("first_lexicographic_match") is not True:
            raise ValueError("candidate choice must use the first lexicographic match")
        seed = selection.get("seed")
        if isinstance(seed, bool) or not isinstance(seed, int) or seed < 0:
            raise ValueError("selection seed must be a non-negative integer")
        if selection.get("failed_searches_retained") is not True:
            raise ValueError("failed candidate searches must be retained")
        if selection.get("thresholds_mutable_after_run") is not False:
            raise ValueError("screening thresholds cannot change after execution")

        trajectory = self.record.get("trajectory_family")
        if not isinstance(trajectory, Mapping):
            raise ValueError("trajectory_family is required")
        if trajectory.get("interpolation") != "quintic-minimum-jerk":
            raise ValueError("C1--C4 selection uses frozen minimum-jerk quintics")
        if trajectory.get("endpoint_velocity_rad_s") != 0.0:
            raise ValueError("candidate endpoint velocity must be zero")
        if trajectory.get("endpoint_acceleration_rad_s2") != 0.0:
            raise ValueError("candidate endpoint acceleration must be zero")
        _positive_finite(trajectory.get("time_quantum_ns"), "time_quantum_ns")
        _positive_finite(trajectory.get("minimum_duration_ns"), "minimum_duration_ns")
        _positive_finite(
            trajectory.get("minimum_nontrivial_arm_displacement_rad"),
            "minimum_nontrivial_arm_displacement_rad",
        )

        screening = self.record.get("screening_policy")
        if not isinstance(screening, Mapping):
            raise ValueError("screening_policy is required")
        if screening.get("screening_is_continuous_certificate") is not False:
            raise ValueError("sampled candidate screening is not a continuous certificate")
        _positive_finite(screening.get("sample_period_ns"), "sample_period_ns")
        _positive_finite(screening.get("max_joint_increment_rad"), "max_joint_increment_rad")
        _positive_finite(screening.get("safe_design_buffer_m"), "safe_design_buffer_m")
        _positive_finite(screening.get("endpoint_positive_buffer_m"), "endpoint_positive_buffer_m")
        _positive_finite(screening.get("penetration_witness_depth_m"), "penetration_witness_depth_m")

        cases = self.record.get("cases")
        if not isinstance(cases, list) or len(cases) != 4:
            raise ValueError("protocol must define exactly C1--C4")
        case_ids = [case.get("case_id") for case in cases if isinstance(case, Mapping)]
        if case_ids != sorted(EXPECTED_CASES):
            raise ValueError("cases must be uniquely ordered C1, C2, C3, C4")
        for case in cases:
            self._validate_case(case)

    @staticmethod
    def _validate_case(case: Mapping[str, Any]) -> None:
        case_id = case["case_id"]
        expected_label, expected_scope, expected_attachment = EXPECTED_CASES[case_id]
        if case.get("expected_label") != expected_label:
            raise ValueError(f"{case_id} label does not match the frozen matrix")
        if case.get("scope_id") != expected_scope:
            raise ValueError(f"{case_id} geometry scope does not match the frozen matrix")
        if case.get("attached_object_active") is not expected_attachment:
            raise ValueError(f"{case_id} attachment status does not match the frozen matrix")
        if case.get("candidate_label_only") is not True:
            raise ValueError(f"{case_id} screening cannot be treated as oracle truth")
        if not case.get("candidate_family"):
            raise ValueError(f"{case_id} candidate family is required")
        requirements = case.get("acceptance_requirements")
        if not isinstance(requirements, list) or not requirements:
            raise ValueError(f"{case_id} acceptance requirements are required")
        oracle = case.get("independent_oracle_requirements")
        if not isinstance(oracle, list) or len(oracle) < 2:
            raise ValueError(f"{case_id} requires independent numeric and human checks")
        if case_id == "C3":
            required = {
                "positive endpoints for every checked pair",
                "interior robot-robot penetration witness",
                "no simultaneous world or within-arm collision at the critical witness",
            }
            if not required.issubset(requirements):
                raise ValueError("C3 must isolate an interior robot-robot collision")
        if case_id == "C4":
            required = {
                "robot-links-only trajectory remains positive",
                "interior attached-object-to-other-arm penetration witness",
                "attached object is positive at both trajectory endpoints",
            }
            if not required.issubset(requirements):
                raise ValueError("C4 must isolate attached-object geometry")
            ablation = case.get("ablation")
            if not isinstance(ablation, Mapping):
                raise ValueError("C4 requires a matched no-attachment ablation")
            if ablation.get("same_joint_trajectory") is not True:
                raise ValueError("C4 ablation must preserve the joint trajectory")
            if ablation.get("only_remove_attached_geometry") is not True:
                raise ValueError("C4 ablation may only remove attached geometry")

    @property
    def sha256(self) -> str:
        return hashlib.sha256(_canonical_json(self.record)).hexdigest()


def load_collision_case_selection_protocol(path: str) -> CollisionCaseSelectionProtocol:
    with open(path, "r", encoding="utf-8") as stream:
        record = json.load(stream)
    if not isinstance(record, Mapping):
        raise ValueError("collision selection protocol root must be an object")
    return CollisionCaseSelectionProtocol.from_record(record)


def _repository_file(repository_root: Path, relative_text: Any, field: str) -> Path:
    if not isinstance(relative_text, str) or not relative_text:
        raise ValueError(f"{field} must be a repository-relative path")
    relative = PurePosixPath(relative_text)
    if relative.is_absolute() or "." in relative.parts or ".." in relative.parts:
        raise ValueError(f"{field} must stay within the repository")
    path = repository_root / Path(*relative.parts)
    if path.is_symlink() or not path.is_file():
        raise ValueError(f"{field} must be a regular non-symlink file")
    try:
        path.resolve().relative_to(repository_root.resolve())
    except ValueError as error:
        raise ValueError(f"{field} resolves outside the repository") from error
    return path


@dataclass(frozen=True)
class CollisionCaseSelectionAmendment:
    """Prospective, content-addressed correction to the frozen C4 filter.

    The correction is intentionally narrower than a runtime allowed-contact
    policy: it applies only to the last sampled state of the purpose-built C4
    candidate screen.  It does not change the verifier's continuous-interval
    geometry contract or the independent label authority.
    """

    record: Mapping[str, Any]

    @classmethod
    def from_record(cls, record: Mapping[str, Any]) -> "CollisionCaseSelectionAmendment":
        value = cls(record)
        value.validate()
        return value

    def validate(self) -> None:
        required_root = {
            "schema",
            "status",
            "paper_result_eligible",
            "base_protocol",
            "trigger_evidence",
            "amendment_reason",
            "candidate_definitions_and_order_unchanged",
            "screening_thresholds_other_than_the_declared_contact_contract_unchanged",
            "c4_terminal_support_contact",
            "selection_bias_disclosure",
        }
        if set(self.record) != required_root:
            raise ValueError("selection amendment root keys differ from the frozen schema")
        if self.record.get("schema") != AMENDMENT_SCHEMA or self.record.get("status") != AMENDMENT_STATUS:
            raise ValueError("unsupported or already-executed selection amendment")
        if self.record.get("paper_result_eligible") is not False:
            raise ValueError("selection amendment is not a paper result")
        if self.record.get("candidate_definitions_and_order_unchanged") is not True:
            raise ValueError("selection amendment may not change candidate definitions or order")
        if self.record.get("screening_thresholds_other_than_the_declared_contact_contract_unchanged") is not True:
            raise ValueError("selection amendment may not alter other screening thresholds")
        if not self.record.get("amendment_reason") or not self.record.get("selection_bias_disclosure"):
            raise ValueError("selection amendment must disclose its reason and selection-bias boundary")

        base = self.record.get("base_protocol")
        if not isinstance(base, Mapping) or set(base) != {"path", "file_sha256", "canonical_content_sha256"}:
            raise ValueError("base protocol identity is incomplete")
        _require_sha256(base["file_sha256"], "base protocol file hash")
        _require_sha256(base["canonical_content_sha256"], "base protocol content hash")

        trigger = self.record.get("trigger_evidence")
        trigger_keys = {
            "raw_archive_path",
            "raw_archive_sha256",
            "raw_archive_size_bytes",
            "observed_pair",
            "observed_signed_distance_m",
            "candidate_screening_executed_before_amendment",
        }
        if not isinstance(trigger, Mapping) or set(trigger) != trigger_keys:
            raise ValueError("selection amendment trigger evidence is incomplete")
        _require_sha256(trigger["raw_archive_sha256"], "trigger archive hash")
        size = trigger["raw_archive_size_bytes"]
        if isinstance(size, bool) or not isinstance(size, int) or size <= 0:
            raise ValueError("trigger archive size must be positive")
        if trigger["observed_pair"] != ["world", "carried_base", "fixture_base"]:
            raise ValueError("trigger pair differs from the frozen support pair")
        distance = trigger["observed_signed_distance_m"]
        if isinstance(distance, bool) or not isinstance(distance, (int, float)) or not math.isfinite(distance):
            raise ValueError("trigger distance must be finite")
        if trigger["candidate_screening_executed_before_amendment"] is not False:
            raise ValueError("amendment must be frozen before candidate screening")

        contract = self.record.get("c4_terminal_support_contact")
        contract_keys = {
            "case_id",
            "scope_id",
            "allowed_sample_position",
            "required_terminal_reference_state",
            "pair",
            "absolute_signed_distance_tolerance_m",
            "first_endpoint_pair_must_exceed_endpoint_buffer",
            "nonterminal_pair_must_be_strictly_positive",
            "all_other_endpoint_pairs_must_exceed_endpoint_buffer",
            "interior_target_witness_and_non_target_rules_unchanged",
            "global_acm_modified",
            "screening_is_continuous_certificate",
            "threshold_role",
        }
        if not isinstance(contract, Mapping) or set(contract) != contract_keys:
            raise ValueError("C4 terminal support contract keys differ from the frozen schema")
        expected_scalars = {
            "case_id": "C4",
            "scope_id": "robot-fixture-carried-base",
            "allowed_sample_position": "last-only",
            "required_terminal_reference_state": "left_operating",
            "pair": ["world", "carried_base", "fixture_base"],
            "first_endpoint_pair_must_exceed_endpoint_buffer": True,
            "nonterminal_pair_must_be_strictly_positive": True,
            "all_other_endpoint_pairs_must_exceed_endpoint_buffer": True,
            "interior_target_witness_and_non_target_rules_unchanged": True,
            "global_acm_modified": False,
            "screening_is_continuous_certificate": False,
        }
        for field, expected in expected_scalars.items():
            if contract.get(field) != expected:
                raise ValueError(f"C4 terminal support contract drift: {field}")
        tolerance = _positive_finite(
            contract.get("absolute_signed_distance_tolerance_m"),
            "controlled support contact tolerance",
        )
        if tolerance != 1e-9:
            raise ValueError("controlled support contact tolerance must remain 1e-9 m")
        if not contract.get("threshold_role"):
            raise ValueError("controlled support contact tolerance role is required")

    @property
    def sha256(self) -> str:
        return hashlib.sha256(_canonical_json(self.record)).hexdigest()

    @property
    def terminal_contact_contract(self) -> Mapping[str, Any]:
        return self.record["c4_terminal_support_contact"]


def load_collision_case_selection_amendment(
    path: str,
    *,
    repository_root: Path,
    expected_base_protocol_content_sha256: str,
) -> CollisionCaseSelectionAmendment:
    amendment_path = Path(path)
    if amendment_path.is_symlink() or not amendment_path.is_file():
        raise ValueError("selection amendment must be a regular non-symlink file")
    record = json.loads(amendment_path.read_text(encoding="utf-8"))
    if not isinstance(record, Mapping):
        raise ValueError("selection amendment root must be an object")
    amendment = CollisionCaseSelectionAmendment.from_record(record)

    base_identity = amendment.record["base_protocol"]
    base_path = _repository_file(repository_root, base_identity["path"], "base protocol")
    if hashlib.sha256(base_path.read_bytes()).hexdigest() != base_identity["file_sha256"]:
        raise ValueError("base protocol file identity mismatch")
    base = load_collision_case_selection_protocol(str(base_path))
    if base.sha256 != base_identity["canonical_content_sha256"]:
        raise ValueError("base protocol content identity mismatch")
    if base.sha256 != expected_base_protocol_content_sha256:
        raise ValueError("selection amendment targets a different candidate-generation protocol")

    trigger = amendment.record["trigger_evidence"]
    archive = _repository_file(repository_root, trigger["raw_archive_path"], "trigger archive")
    if archive.stat().st_size != trigger["raw_archive_size_bytes"]:
        raise ValueError("trigger archive size mismatch")
    if hashlib.sha256(archive.read_bytes()).hexdigest() != trigger["raw_archive_sha256"]:
        raise ValueError("trigger archive hash mismatch")
    return amendment
