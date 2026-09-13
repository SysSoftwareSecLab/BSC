"""Fail-closed evaluation of sampled FCL candidate-screening outputs.

The screening backend is a construction filter, not the independent oracle
and not a continuous collision certificate.  This module deliberately streams
large distance tables and enforces the pre-registered pair universe at every
sample before applying the C1--C4 construction predicates.
"""

from __future__ import annotations

import csv
from dataclasses import dataclass
import hashlib
from itertools import zip_longest
import json
import math
from pathlib import Path, PurePosixPath
import re
import tarfile
from typing import Any, Callable, Dict, Iterator, Mapping, Optional, Sequence, Tuple

from .collision_candidate_generation import canonical_json_bytes, sha256_path
from .fcl_probe_evidence import EXPECTED_HEADER


PairKey = Tuple[str, str, str]
SCREENING_INPUT_SCHEMA = "bisafecode.collision-screening-inputs/v0.1"
SCREENING_INPUT_STATUS = "FROZEN_AFTER_EXP_S2_026_BEFORE_FCL"
SOURCE_ARCHIVE_MEMBER_KEYS = frozenset(
    {
        "active_srdf",
        "preflight",
        "probe_source",
        "reference_states",
        "resolved_urdf",
        "target",
        "transform",
    }
)


def _require_exact_keys(record: Mapping[str, Any], expected: frozenset[str], label: str) -> None:
    actual = frozenset(record)
    if actual != expected:
        raise ValueError(
            f"{label} keys differ from the frozen schema: "
            f"missing={sorted(expected - actual)}, extra={sorted(actual - expected)}"
        )


def _require_sha256(value: Any, label: str) -> str:
    if not isinstance(value, str) or len(value) != 64:
        raise ValueError(f"{label} must be a lowercase SHA-256 digest")
    if any(character not in "0123456789abcdef" for character in value):
        raise ValueError(f"{label} must be a lowercase SHA-256 digest")
    return value


def _require_git_commit(value: Any, label: str) -> str:
    if not isinstance(value, str) or len(value) != 40:
        raise ValueError(f"{label} must be a full lowercase Git commit")
    if any(character not in "0123456789abcdef" for character in value):
        raise ValueError(f"{label} must be a full lowercase Git commit")
    return value


def _require_positive_size(value: Any, label: str) -> int:
    if isinstance(value, bool) or not isinstance(value, int) or value <= 0:
        raise ValueError(f"{label} must be a positive byte count")
    return value


def _repository_file(repository_root: Path, relative_text: Any, label: str) -> Path:
    if not isinstance(relative_text, str) or not relative_text:
        raise ValueError(f"{label} path must be a non-empty repository-relative string")
    relative = PurePosixPath(relative_text)
    if relative.is_absolute() or ".." in relative.parts or "." in relative.parts:
        raise ValueError(f"{label} path must stay within the repository")
    path = repository_root / Path(*relative.parts)
    if path.is_symlink() or not path.is_file():
        raise ValueError(f"{label} must be an existing regular non-symlink file")
    root_resolved = repository_root.resolve()
    try:
        path.resolve().relative_to(root_resolved)
    except ValueError as error:
        raise ValueError(f"{label} resolves outside the repository") from error
    return path


def _validate_file_record(repository_root: Path, record: Mapping[str, Any], label: str) -> Path:
    _require_exact_keys(record, frozenset({"path", "sha256", "size_bytes"}), label)
    path = _repository_file(repository_root, record["path"], label)
    expected_sha256 = _require_sha256(record["sha256"], f"{label} SHA-256")
    expected_size = _require_positive_size(record["size_bytes"], f"{label} size")
    if path.stat().st_size != expected_size or sha256_path(path) != expected_sha256:
        raise ValueError(f"{label} identity mismatch")
    return path


def load_attachment_screening_binding(
    binding_spec_path: Path,
    *,
    repository_root: Path,
    expected_generation_spec_content_sha256: str,
    batch_manifest_path: Path,
    batch_manifest: Mapping[str, Any],
    expected_source_member_hashes: Mapping[str, str],
) -> dict:
    """Validate the immutable EXP-S2-026 transform chain before FCL screening.

    This function never invokes FK or a collision backend.  It binds the
    precomputed transform to the exact candidate batch, generation semantics,
    raw evidence archive, model files, and transform inputs.  Any identity or
    provenance mismatch fails before the screening output directory is made.
    """

    if binding_spec_path.is_symlink() or not binding_spec_path.is_file():
        raise ValueError("screening-input specification must be a regular non-symlink file")
    try:
        record = json.loads(binding_spec_path.read_text(encoding="utf-8"))
    except json.JSONDecodeError as error:
        raise ValueError("screening-input specification is not valid JSON") from error
    if not isinstance(record, dict):
        raise ValueError("screening-input specification must be a JSON object")
    _require_exact_keys(
        record,
        frozenset(
            {
                "schema",
                "status",
                "paper_result_eligible",
                "independent_oracle_executed",
                "generation_spec_content_sha256",
                "candidate_batch_manifest_sha256",
                "attachment_transform",
                "source_archive",
                "provenance",
                "policy",
            }
        ),
        "screening-input specification",
    )
    if record["schema"] != SCREENING_INPUT_SCHEMA or record["status"] != SCREENING_INPUT_STATUS:
        raise ValueError("screening-input schema or status is not the frozen pre-FCL contract")
    if record["paper_result_eligible"] is not False or record["independent_oracle_executed"] is not False:
        raise ValueError("screening-input evidence-boundary flags are invalid")

    generation_hash = _require_sha256(
        record["generation_spec_content_sha256"], "generation specification content SHA-256"
    )
    if generation_hash != expected_generation_spec_content_sha256:
        raise ValueError("screening inputs target a different generation specification")
    batch_hash = _require_sha256(
        record["candidate_batch_manifest_sha256"], "candidate batch manifest SHA-256"
    )
    if sha256_path(batch_manifest_path) != batch_hash:
        raise ValueError("candidate batch manifest does not match the frozen screening inputs")

    policy = record["policy"]
    if not isinstance(policy, dict):
        raise ValueError("screening-input policy must be an object")
    _require_exact_keys(
        policy,
        frozenset(
            {
                "precomputed_transform_required",
                "attachment_fk_may_execute_during_screening",
                "screening_backend_role",
                "screening_is_continuous_certificate",
            }
        ),
        "screening-input policy",
    )
    if (
        policy["precomputed_transform_required"] is not True
        or policy["attachment_fk_may_execute_during_screening"] is not False
        or policy["screening_backend_role"] != "candidate-construction-only"
        or policy["screening_is_continuous_certificate"] is not False
    ):
        raise ValueError("screening-input policy would broaden the frozen evidence boundary")

    transform_record = record["attachment_transform"]
    if not isinstance(transform_record, dict):
        raise ValueError("attachment-transform record must be an object")
    transform_path = _validate_file_record(repository_root, transform_record, "attachment transform")
    transform_payload = transform_path.read_bytes()

    archive_record = record["source_archive"]
    if not isinstance(archive_record, dict):
        raise ValueError("source-archive record must be an object")
    _require_exact_keys(
        archive_record,
        frozenset({"path", "sha256", "size_bytes", "members"}),
        "source-archive record",
    )
    archive_path = _repository_file(repository_root, archive_record["path"], "source archive")
    archive_sha256 = _require_sha256(archive_record["sha256"], "source archive SHA-256")
    archive_size = _require_positive_size(archive_record["size_bytes"], "source archive size")
    if archive_path.stat().st_size != archive_size or sha256_path(archive_path) != archive_sha256:
        raise ValueError("source archive identity mismatch")
    member_records = archive_record["members"]
    if not isinstance(member_records, dict):
        raise ValueError("source-archive members must be an object")
    _require_exact_keys(member_records, SOURCE_ARCHIVE_MEMBER_KEYS, "source-archive members")

    member_payloads: Dict[str, bytes] = {}
    with tarfile.open(archive_path, "r:gz") as archive:
        members = archive.getmembers()
        member_names = [member.name for member in members]
        if len(member_names) != len(set(member_names)):
            raise ValueError("source archive contains duplicate member names")
        by_name = {member.name: member for member in members}
        for role in sorted(SOURCE_ARCHIVE_MEMBER_KEYS):
            member_record = member_records[role]
            if not isinstance(member_record, dict):
                raise ValueError(f"source-archive member record must be an object: {role}")
            _require_exact_keys(
                member_record,
                frozenset({"member_path", "sha256", "size_bytes"}),
                f"source-archive member {role}",
            )
            member_path = member_record["member_path"]
            if not isinstance(member_path, str):
                raise ValueError(f"source-archive member path must be a string: {role}")
            pure_member = PurePosixPath(member_path)
            if pure_member.is_absolute() or ".." in pure_member.parts or "." in pure_member.parts:
                raise ValueError(f"unsafe source-archive member path: {role}")
            member = by_name.get(member_path)
            if member is None or not member.isfile() or member.issym() or member.islnk():
                raise ValueError(f"source-archive member is missing or not a regular file: {role}")
            extracted = archive.extractfile(member)
            if extracted is None:
                raise ValueError(f"source-archive member cannot be read: {role}")
            payload = extracted.read()
            expected_member_sha256 = _require_sha256(
                member_record["sha256"], f"source-archive member SHA-256: {role}"
            )
            expected_member_size = _require_positive_size(
                member_record["size_bytes"], f"source-archive member size: {role}"
            )
            if len(payload) != expected_member_size or hashlib.sha256(payload).hexdigest() != expected_member_sha256:
                raise ValueError(f"source-archive member identity mismatch: {role}")
            member_payloads[role] = payload
    if member_payloads["transform"] != transform_payload:
        raise ValueError("precomputed transform differs from the accepted source-archive member")

    provenance = record["provenance"]
    if not isinstance(provenance, dict):
        raise ValueError("screening-input provenance must be an object")
    _require_exact_keys(
        provenance,
        frozenset(
            {
                "experiment_id",
                "repository_head",
                "probe_binary_sha256",
                "probe_source_sha256",
                "resolved_urdf_sha256",
                "active_srdf_sha256",
                "reference_states_sha256",
                "target_sha256",
            }
        ),
        "screening-input provenance",
    )
    if provenance["experiment_id"] != "EXP-S2-026":
        raise ValueError("attachment transform provenance must be EXP-S2-026")
    _require_git_commit(provenance["repository_head"], "provenance repository head")
    for key in provenance:
        if key.endswith("_sha256"):
            _require_sha256(provenance[key], f"provenance {key}")
    expected_provenance = {
        "probe_source_sha256": member_records["probe_source"]["sha256"],
        "resolved_urdf_sha256": member_records["resolved_urdf"]["sha256"],
        "active_srdf_sha256": member_records["active_srdf"]["sha256"],
        "reference_states_sha256": member_records["reference_states"]["sha256"],
        "target_sha256": member_records["target"]["sha256"],
    }
    for key, expected in expected_provenance.items():
        if provenance[key] != expected:
            raise ValueError(f"source-archive member and provenance identity differ: {key}")

    try:
        preflight = json.loads(member_payloads["preflight"].decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError) as error:
        raise ValueError("EXP-S2-026 preflight member is not valid UTF-8 JSON") from error
    if not isinstance(preflight, dict) or preflight.get("status") != "PASS":
        raise ValueError("EXP-S2-026 preflight did not report PASS")
    checks = preflight.get("checks")
    if not isinstance(checks, dict) or not checks or any(value is not True for value in checks.values()):
        raise ValueError("EXP-S2-026 preflight checks are incomplete or non-PASS")
    expected_preflight = {
        "binary": provenance["probe_binary_sha256"],
        "head": provenance["repository_head"],
        "manifest": batch_hash,
        "reference_states": provenance["reference_states_sha256"],
        "source": provenance["probe_source_sha256"],
        "srdf": provenance["active_srdf_sha256"],
        "target": provenance["target_sha256"],
        "urdf": provenance["resolved_urdf_sha256"],
    }
    if preflight.get("expected") != expected_preflight:
        raise ValueError("EXP-S2-026 preflight expected identities differ from screening provenance")
    actual_preflight = preflight.get("actual")
    if not isinstance(actual_preflight, dict):
        raise ValueError("EXP-S2-026 preflight actual identities are absent")
    if any(actual_preflight.get(key) != value for key, value in expected_preflight.items()):
        raise ValueError("EXP-S2-026 preflight actual identities differ from screening provenance")
    if actual_preflight.get("branch") != "main" or actual_preflight.get("status_porcelain") != "":
        raise ValueError("EXP-S2-026 preflight repository state was not main and clean")
    if provenance["resolved_urdf_sha256"] != expected_source_member_hashes.get(
        "gate_a_probe/outputs/openarm_v1_bimanual_resolved.urdf"
    ):
        raise ValueError("attachment provenance and generation URDF differ")
    if provenance["active_srdf_sha256"] != expected_source_member_hashes.get(
        "gate_a_probe/gate_a2_acm/active_srdf_copy.srdf"
    ):
        raise ValueError("attachment provenance and generation SRDF differ")

    transform_inputs = batch_manifest.get("attachment_transform_inputs")
    if not isinstance(transform_inputs, dict) or set(transform_inputs) != {"reference_states", "target"}:
        raise ValueError("candidate batch attachment-transform inputs are incomplete")
    for role, provenance_key in (
        ("reference_states", "reference_states_sha256"),
        ("target", "target_sha256"),
    ):
        input_record = transform_inputs[role]
        if not isinstance(input_record, dict):
            raise ValueError(f"candidate batch attachment input is invalid: {role}")
        if (
            input_record.get("sha256") != provenance[provenance_key]
            or input_record.get("size_bytes") != member_records[role]["size_bytes"]
        ):
            raise ValueError(f"candidate batch and attachment provenance differ: {role}")

    return {
        "binding_spec_path": str(binding_spec_path.resolve()),
        "binding_spec_sha256": sha256_path(binding_spec_path),
        "binding_spec_content_sha256": hashlib.sha256(canonical_json_bytes(record)).hexdigest(),
        "generation_spec_content_sha256": generation_hash,
        "candidate_batch_manifest_sha256": batch_hash,
        "precomputed_attachment": {
            "path": str(transform_path.resolve()),
            "sha256": transform_record["sha256"],
            "size_bytes": transform_record["size_bytes"],
        },
        "source_archive": {
            "path": str(archive_path.resolve()),
            "sha256": archive_sha256,
            "size_bytes": archive_size,
            "verified_member_count": len(member_payloads),
            "preflight_status": preflight["status"],
        },
        "provenance": dict(provenance),
        "policy": dict(policy),
    }


@dataclass(frozen=True)
class DistanceValue:
    pair: PairKey
    distance_m: float
    nearest_first_m: Tuple[float, float, float]
    nearest_second_m: Tuple[float, float, float]
    first_body_type: str
    second_body_type: str

    def as_record(self) -> dict:
        return {
            "scope": self.pair[0],
            "entity_first": self.pair[1],
            "entity_second": self.pair[2],
            "signed_distance_m": self.distance_m,
            "nearest_first_m": list(self.nearest_first_m),
            "nearest_second_m": list(self.nearest_second_m),
            "first_body_type": self.first_body_type,
            "second_body_type": self.second_body_type,
        }


@dataclass(frozen=True)
class SampleDistances:
    scenario: str
    values: Mapping[PairKey, DistanceValue]

    @property
    def minimum(self) -> DistanceValue:
        return min(self.values.values(), key=lambda value: (value.distance_m, value.pair))


def expected_pair_universe(inventory: Mapping[str, Any], scope_id: str) -> frozenset[PairKey]:
    matching = [scope for scope in inventory["pair_scopes"] if scope["scope_id"] == scope_id]
    if len(matching) != 1:
        raise ValueError(f"geometry inventory must contain exactly one scope: {scope_id}")
    scope = matching[0]
    pairs = frozenset(
        (item["query_scope"], *sorted(item["entity_pair"]))
        for item in scope["pairs"]
        if item["disposition"] == "checked"
    )
    if len(pairs) != scope["pair_census"]["checked"]:
        raise ValueError(f"geometry inventory checked-pair census is inconsistent: {scope_id}")
    return pairs


def expected_scenarios(state_path: Path, candidate_id: str) -> Tuple[str, ...]:
    with state_path.open("r", encoding="utf-8", newline="") as stream:
        reader = csv.DictReader(stream, delimiter="\t")
        if reader.fieldnames is None or reader.fieldnames[0] != "scenario":
            raise ValueError("state table must begin with scenario")
        scenarios = tuple(row["scenario"] for row in reader)
    if not scenarios or len(scenarios) != len(set(scenarios)):
        raise ValueError("state scenarios must be non-empty and unique")
    pattern = re.compile(rf"^{re.escape(candidate_id)}__N(\d{{6}})__T(\d{{19}})$")
    last_time = -1
    for index, scenario in enumerate(scenarios):
        match = pattern.fullmatch(scenario)
        if match is None or int(match.group(1)) != index:
            raise ValueError("state scenario does not preserve candidate/sample identity")
        time_ns = int(match.group(2))
        if time_ns <= last_time:
            raise ValueError("state scenario times must be strictly increasing")
        last_time = time_ns
    return scenarios


def _finite(row: Mapping[str, str], names: Sequence[str]) -> Tuple[float, ...]:
    result = tuple(float(row[name]) for name in names)
    if any(not math.isfinite(value) for value in result):
        raise ValueError("distance output contains a non-finite number")
    return result


def iter_distance_groups(
    path: Path,
    scenarios: Sequence[str],
    expected_pairs: frozenset[PairKey],
) -> Iterator[SampleDistances]:
    with path.open("r", encoding="utf-8", newline="") as stream:
        reader = csv.DictReader(stream, delimiter="\t")
        if reader.fieldnames != list(EXPECTED_HEADER):
            raise ValueError("distance output header mismatch")
        expected_index = 0
        current_scenario: Optional[str] = None
        values: Dict[PairKey, DistanceValue] = {}

        def finish() -> SampleDistances:
            if current_scenario is None:
                raise AssertionError("cannot finish an absent scenario")
            actual = frozenset(values)
            if actual != expected_pairs:
                missing = len(expected_pairs - actual)
                extra = len(actual - expected_pairs)
                raise ValueError(
                    f"exact pair coverage mismatch for {current_scenario}: missing={missing}, extra={extra}"
                )
            return SampleDistances(current_scenario, dict(values))

        for row in reader:
            scenario = row["scenario"]
            if current_scenario != scenario:
                if current_scenario is not None:
                    yield finish()
                    expected_index += 1
                if expected_index >= len(scenarios) or scenario != scenarios[expected_index]:
                    raise ValueError("distance scenarios differ from the exact state-table order")
                current_scenario = scenario
                values = {}
            first, second = row["entity_first"], row["entity_second"]
            if not first or not second or second < first:
                raise ValueError("distance pair names must be non-empty and sorted")
            pair = (row["scope"], first, second)
            if pair in values:
                raise ValueError(f"duplicate distance pair in scenario {scenario}: {pair}")
            distance = _finite(row, ("signed_distance_m",))[0]
            nearest_first = _finite(
                row,
                ("nearest_first_x_m", "nearest_first_y_m", "nearest_first_z_m"),
            )
            nearest_second = _finite(
                row,
                ("nearest_second_x_m", "nearest_second_y_m", "nearest_second_z_m"),
            )
            body_types = (row["first_body_type"], row["second_body_type"])
            if any(value not in {"robot-link", "attached-object", "world"} for value in body_types):
                raise ValueError("distance output contains an unknown body type")
            values[pair] = DistanceValue(
                pair,
                distance,
                nearest_first,
                nearest_second,
                body_types[0],
                body_types[1],
            )
        if current_scenario is not None:
            yield finish()
            expected_index += 1
        if expected_index != len(scenarios):
            raise ValueError("distance output does not contain every expected scenario")


def is_cross_arm(pair: PairKey) -> bool:
    first, second = pair[1], pair[2]
    return (first.startswith("openarm_left_") and second.startswith("openarm_right_")) or (
        first.startswith("openarm_right_") and second.startswith("openarm_left_")
    )


def is_carried_base_right_arm(pair: PairKey) -> bool:
    return "carried_base" in pair[1:] and any(name.startswith("openarm_right_") for name in pair[1:])


def _minimum(values: Sequence[DistanceValue]) -> DistanceValue:
    if not values:
        raise ValueError("required distance category is absent")
    return min(values, key=lambda value: (value.distance_m, value.pair))


def summarize_candidate(
    *,
    case_id: str,
    candidate_id: str,
    state_path: Path,
    distance_path: Path,
    inventory: Mapping[str, Any],
    scope_id: str,
    safe_buffer_m: float,
    endpoint_buffer_m: float,
    witness_depth_m: float,
    terminal_contact_contract: Optional[Mapping[str, Any]] = None,
) -> dict:
    scenarios = expected_scenarios(state_path, candidate_id)
    universe = expected_pair_universe(inventory, scope_id)
    groups = tuple(iter_distance_groups(distance_path, scenarios, universe))
    all_values = [value for group in groups for value in group.values.values()]
    overall = _minimum(all_values)
    report = {
        "case_id": case_id,
        "candidate_id": candidate_id,
        "scope_id": scope_id,
        "sample_count": len(groups),
        "checked_pair_count_per_sample": len(universe),
        "minimum": overall.as_record(),
        "accepted": False,
        "reasons": [],
    }
    reasons = report["reasons"]
    if case_id in {"C1", "C2"}:
        if overall.distance_m <= safe_buffer_m:
            reasons.append("minimum-not-strictly-greater-than-safe-buffer")
    elif case_id in {"C3", "C4"}:
        endpoint_values = list(groups[0].values.values()) + list(groups[-1].values.values())
        if terminal_contact_contract is not None:
            if case_id != "C4":
                raise ValueError("terminal support contact contract is only valid for C4")
            pair_record = terminal_contact_contract.get("pair")
            if pair_record != ["world", "carried_base", "fixture_base"]:
                raise ValueError("terminal support contact pair differs from the frozen C4 contract")
            support_pair: PairKey = tuple(pair_record)  # type: ignore[assignment]
            if support_pair not in groups[0].values or support_pair not in groups[-1].values:
                raise ValueError("terminal support contact pair is absent from C4 coverage")
            tolerance = float(terminal_contact_contract["absolute_signed_distance_tolerance_m"])
            if not math.isfinite(tolerance) or tolerance <= 0.0:
                raise ValueError("terminal support contact tolerance must be finite and positive")
            first_support = groups[0].values[support_pair]
            terminal_support = groups[-1].values[support_pair]
            report["controlled_terminal_contact"] = {
                "sample_index": len(groups) - 1,
                "scenario": groups[-1].scenario,
                "pair": list(support_pair),
                "signed_distance_m": terminal_support.distance_m,
                "absolute_signed_distance_tolerance_m": tolerance,
                "global_acm_modified": False,
            }
            if first_support.distance_m <= endpoint_buffer_m:
                reasons.append("controlled-pair-first-endpoint-not-strictly-greater-than-buffer")
            if any(group.values[support_pair].distance_m <= 0.0 for group in groups[1:-1]):
                reasons.append("controlled-support-contact-occurs-before-terminal-sample")
            if abs(terminal_support.distance_m) > tolerance:
                reasons.append("controlled-terminal-contact-outside-tolerance")
            endpoint_values = list(groups[0].values.values()) + [
                value for pair, value in groups[-1].values.items() if pair != support_pair
            ]
        endpoint_minimum = _minimum(endpoint_values)
        report["endpoint_minimum"] = endpoint_minimum.as_record()
        if endpoint_minimum.distance_m <= endpoint_buffer_m:
            reasons.append("endpoint-minimum-not-strictly-greater-than-buffer")
        predicate: Callable[[PairKey], bool] = is_cross_arm if case_id == "C3" else is_carried_base_right_arm
        witness: Optional[Tuple[int, DistanceValue]] = None
        for index, group in enumerate(groups[1:-1], 1):
            target = _minimum([value for pair, value in group.values.items() if predicate(pair)])
            if target.distance_m <= -witness_depth_m:
                witness = (index, target)
                break
        if witness is None:
            reasons.append("required-interior-target-witness-absent")
        else:
            witness_index, target = witness
            non_target_before_or_at = [
                value
                for group in groups[: witness_index + 1]
                for pair, value in group.values.items()
                if not predicate(pair)
            ]
            other = _minimum(non_target_before_or_at)
            report["target_witness"] = {
                "sample_index": witness_index,
                "scenario": groups[witness_index].scenario,
                **target.as_record(),
            }
            report["non_target_minimum_before_or_at_witness"] = other.as_record()
            if other.distance_m <= 0.0:
                reasons.append("non-target-pair-not-strictly-positive-before-or-at-witness")
    else:
        raise ValueError(f"unknown collision case: {case_id}")
    report["accepted"] = not reasons
    return report


def compare_common_non_attached_distances(
    *,
    candidate_id: str,
    state_path: Path,
    empty_path: Path,
    carried_path: Path,
    inventory: Mapping[str, Any],
    tolerance_m: float,
) -> dict:
    scenarios = expected_scenarios(state_path, candidate_id)
    empty_pairs = expected_pair_universe(inventory, "robot-fixture-empty")
    carried_pairs = expected_pair_universe(inventory, "robot-fixture-carried-base")
    maximum_error = -1.0
    maximum_record = None
    comparisons = 0
    empty_groups = iter_distance_groups(empty_path, scenarios, empty_pairs)
    carried_groups = iter_distance_groups(carried_path, scenarios, carried_pairs)
    for index, values in enumerate(zip_longest(empty_groups, carried_groups)):
        empty, carried = values
        if empty is None or carried is None or empty.scenario != carried.scenario:
            raise ValueError("matched C4 runs have different scenario sequences")
        for pair, empty_value in empty.values.items():
            carried_value = carried.values.get(pair)
            if carried_value is None:
                raise ValueError(f"carried run omits common pair: {pair}")
            error = abs(empty_value.distance_m - carried_value.distance_m)
            comparisons += 1
            if error > maximum_error:
                maximum_error = error
                maximum_record = {
                    "sample_index": index,
                    "scenario": empty.scenario,
                    "scope": pair[0],
                    "entity_first": pair[1],
                    "entity_second": pair[2],
                    "empty_distance_m": empty_value.distance_m,
                    "carried_distance_m": carried_value.distance_m,
                    "absolute_error_m": error,
                }
    if maximum_record is None:
        raise ValueError("matched C4 comparison is empty")
    return {
        "comparison_count": comparisons,
        "tolerance_m": tolerance_m,
        "maximum_absolute_error": maximum_record,
        "accepted": maximum_error <= tolerance_m,
    }


def evaluate_candidate(
    *,
    case_id: str,
    candidate_id: str,
    state_path: Path,
    primary_distance_path: Path,
    inventory: Mapping[str, Any],
    thresholds: Mapping[str, float],
    empty_ablation_path: Optional[Path] = None,
    terminal_contact_contract: Optional[Mapping[str, Any]] = None,
) -> dict:
    scope_id = "robot-fixture-carried-base" if case_id in {"C2", "C4"} else "robot-fixture-empty"
    report = summarize_candidate(
        case_id=case_id,
        candidate_id=candidate_id,
        state_path=state_path,
        distance_path=primary_distance_path,
        inventory=inventory,
        scope_id=scope_id,
        safe_buffer_m=float(thresholds["safe_design_buffer_m"]),
        endpoint_buffer_m=float(thresholds["endpoint_positive_buffer_m"]),
        witness_depth_m=float(thresholds["penetration_witness_depth_m"]),
        terminal_contact_contract=terminal_contact_contract,
    )
    if case_id == "C4":
        if empty_ablation_path is None:
            raise ValueError("C4 requires the matched empty-attachment output")
        empty = summarize_candidate(
            case_id="C1",
            candidate_id=candidate_id,
            state_path=state_path,
            distance_path=empty_ablation_path,
            inventory=inventory,
            scope_id="robot-fixture-empty",
            safe_buffer_m=0.0,
            endpoint_buffer_m=float(thresholds["endpoint_positive_buffer_m"]),
            witness_depth_m=float(thresholds["penetration_witness_depth_m"]),
        )
        comparison = compare_common_non_attached_distances(
            candidate_id=candidate_id,
            state_path=state_path,
            empty_path=empty_ablation_path,
            carried_path=primary_distance_path,
            inventory=inventory,
            tolerance_m=float(thresholds["ablation_distance_match_tolerance_m"]),
        )
        report["empty_ablation"] = empty
        report["common_non_attached_distance_comparison"] = comparison
        if not empty["accepted"]:
            report["reasons"].append("matched-empty-run-not-strictly-positive")
        if not comparison["accepted"]:
            report["reasons"].append("common-pair-distance-mismatch")
        report["accepted"] = not report["reasons"]
    return report
