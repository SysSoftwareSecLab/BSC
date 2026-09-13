"""Fail-closed EXP-S2-030 input and output validation.

The gate runs one frozen C4 state table only to validate the prospective,
sample-local terminal support-contact predicate.  It does not search for or
label a canonical candidate and is not a continuous collision certificate.
"""

from __future__ import annotations

import csv
from dataclasses import dataclass
import hashlib
import json
import math
from pathlib import Path, PurePosixPath
import re
import tarfile
from typing import Any, Mapping, Tuple

from .collision_candidate_screening import (
    expected_pair_universe,
    expected_scenarios,
    iter_distance_groups,
)
from .collision_case_protocol import load_collision_case_selection_amendment


SCHEMA = "bisafecode.c4-endpoint-contact-gate/v0.1"
STATUS = "FROZEN_BEFORE_EXECUTION"
EXPECTED_CANDIDATE = "C4-G00-L00-R00-S00"
STATE_HEADER = (
    "scenario",
    *(f"openarm_left_joint{index}" for index in range(1, 8)),
    "openarm_left_finger_joint1",
    "openarm_left_finger_joint2",
    *(f"openarm_right_joint{index}" for index in range(1, 8)),
    "openarm_right_finger_joint1",
    "openarm_right_finger_joint2",
)
SCENARIO_PATTERN = re.compile(
    rf"^{re.escape(EXPECTED_CANDIDATE)}__N(?P<index>[0-9]{{6}})__T(?P<time>[0-9]{{19}})$"
)


def sha256_bytes(payload: bytes) -> str:
    return hashlib.sha256(payload).hexdigest()


def sha256_path(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _require_sha256(value: Any, field: str) -> str:
    if not isinstance(value, str) or len(value) != 64 or any(c not in "0123456789abcdef" for c in value):
        raise ValueError(f"{field} must be a lowercase SHA-256 digest")
    return value


def _repo_file(root: Path, record: Mapping[str, Any], field: str) -> Path:
    if set(record) != {"path", "sha256", "size_bytes"}:
        raise ValueError(f"{field} identity keys differ from the frozen schema")
    relative = PurePosixPath(record["path"])
    if relative.is_absolute() or "." in relative.parts or ".." in relative.parts:
        raise ValueError(f"{field} path must stay within the repository")
    path = root / Path(*relative.parts)
    try:
        path.resolve().relative_to(root.resolve())
    except ValueError as error:
        raise ValueError(f"{field} path escapes the repository") from error
    if path.is_symlink() or not path.is_file():
        raise ValueError(f"{field} must be a regular non-symlink file")
    if path.stat().st_size != record["size_bytes"] or sha256_path(path) != _require_sha256(record["sha256"], field):
        raise ValueError(f"{field} identity mismatch")
    return path


def _archive_member(archive: tarfile.TarFile, record: Mapping[str, Any], field: str) -> bytes:
    if set(record) != {"path", "sha256", "size_bytes"}:
        raise ValueError(f"{field} member identity keys differ from the frozen schema")
    matches = [member for member in archive.getmembers() if member.name == record["path"]]
    if len(matches) != 1 or not matches[0].isfile() or matches[0].issym() or matches[0].islnk():
        raise ValueError(f"{field} must occur once as a regular archive member")
    stream = archive.extractfile(matches[0])
    if stream is None:
        raise ValueError(f"cannot read {field}")
    payload = stream.read()
    if len(payload) != record["size_bytes"] or sha256_bytes(payload) != _require_sha256(record["sha256"], field):
        raise ValueError(f"{field} archive-member identity mismatch")
    return payload


def _validate_state_table(payload: bytes, candidate: Mapping[str, Any]) -> Tuple[str, ...]:
    try:
        text = payload.decode("ascii")
    except UnicodeDecodeError as error:
        raise ValueError("C4 state table must be ASCII") from error
    reader = csv.DictReader(text.splitlines(), delimiter="\t")
    if tuple(reader.fieldnames or ()) != STATE_HEADER:
        raise ValueError("C4 state-table header differs from the frozen 18-variable schema")
    rows = list(reader)
    if len(rows) != candidate["sample_count"]:
        raise ValueError("C4 state-table sample count mismatch")
    scenarios = []
    prior_time = -1
    for index, row in enumerate(rows):
        scenario = row["scenario"]
        match = SCENARIO_PATTERN.fullmatch(scenario)
        if match is None or int(match.group("index")) != index:
            raise ValueError("C4 state-table scenario index is not contiguous")
        time_ns = int(match.group("time"))
        if time_ns <= prior_time:
            raise ValueError("C4 state-table time is not strictly increasing")
        prior_time = time_ns
        for field in STATE_HEADER[1:]:
            value = float(row[field])
            if not math.isfinite(value):
                raise ValueError("C4 state table contains a non-finite joint value")
        scenarios.append(scenario)
    if scenarios[0] != candidate["first_scenario"] or scenarios[-1] != candidate["last_scenario"]:
        raise ValueError("C4 state-table endpoint scenario identity mismatch")
    return tuple(scenarios)


@dataclass(frozen=True)
class C4EndpointGateInputs:
    specification: Mapping[str, Any]
    specification_file_sha256: str
    state_table_bytes: bytes
    state_scenarios: Tuple[str, ...]
    inventory: Mapping[str, Any]
    amendment: Mapping[str, Any]


def load_c4_endpoint_gate_inputs(repo_root: Path, spec_path: Path) -> C4EndpointGateInputs:
    repo_root = repo_root.resolve()
    spec_path = spec_path.resolve()
    record = json.loads(spec_path.read_text(encoding="utf-8"))
    required_root = {
        "schema", "status", "experiment_id", "paper_result_eligible", "purpose",
        "candidate_input", "selection_amendment", "repo_inputs", "runtime_inputs",
        "acceptance", "execution", "prohibited_operations",
    }
    if not isinstance(record, Mapping) or set(record) != required_root:
        raise ValueError("C4 endpoint gate root differs from the frozen schema")
    if record["schema"] != SCHEMA or record["status"] != STATUS or record["experiment_id"] != "EXP-S2-030":
        raise ValueError("unsupported or already-executed C4 endpoint gate")
    if record["paper_result_eligible"] is not False:
        raise ValueError("C4 endpoint gate is not a paper result")

    candidate = record["candidate_input"]
    if candidate["candidate_id"] != EXPECTED_CANDIDATE or candidate["sample_count"] != 2081:
        raise ValueError("C4 endpoint gate candidate identity drift")
    archive = _repo_file(repo_root, candidate["source_archive"], "candidate source archive")
    with tarfile.open(archive, "r:gz") as source:
        for member in source.getmembers():
            pure = PurePosixPath(member.name)
            if pure.is_absolute() or ".." in pure.parts or member.issym() or member.islnk() or member.isdev():
                raise ValueError("candidate source archive contains an unsafe member")
        manifest_bytes = _archive_member(source, candidate["manifest_member"], "candidate manifest")
        state_bytes = _archive_member(source, candidate["state_table_member"], "C4 state table")
    manifest = json.loads(manifest_bytes.decode("utf-8"))
    if manifest.get("schema") != "bisafecode.collision-candidate-batch/v0.2":
        raise ValueError("candidate manifest schema drift")
    if manifest.get("status") != "PREPARED_NOT_SCREENED":
        raise ValueError("candidate manifest is not the frozen unscreened batch")
    if manifest.get("generation_spec_file_sha256") != candidate["generation_spec_file_sha256"]:
        raise ValueError("candidate generation-spec file identity mismatch")
    if manifest.get("generation_spec_content_sha256") != candidate["generation_spec_content_sha256"]:
        raise ValueError("candidate generation-spec content identity mismatch")
    if manifest.get("selection_protocol_content_sha256") != "200e830b7633a446d572c54566512e40da4172836a2e0b474e80cc35ee0e80c2":
        raise ValueError("candidate batch is not bound to the frozen base selection protocol")
    manifest_candidate = next(
        (item for item in manifest.get("candidates", []) if item.get("candidate_id") == EXPECTED_CANDIDATE),
        None,
    )
    if manifest_candidate is None or manifest_candidate.get("case_id") != "C4" or manifest_candidate.get("rank") != 0:
        raise ValueError("candidate manifest does not bind the first lexicographic C4 candidate")
    if (
        manifest_candidate.get("scope_id") != "robot-fixture-carried-base"
        or manifest_candidate.get("structural_status") != "READY"
        or manifest_candidate.get("screening_status") != "NOT_EXECUTED"
        or manifest_candidate.get("attached_object_active") is not True
        or manifest_candidate.get("requires_attachment_transform") is not True
        or manifest_candidate.get("paper_result_eligible") is not False
    ):
        raise ValueError("candidate manifest readiness or evidence boundary drift")
    if manifest_candidate.get("sample_count") != candidate["sample_count"]:
        raise ValueError("candidate manifest sample count mismatch")
    state_record = manifest.get("state_tables", {}).get(EXPECTED_CANDIDATE)
    if (
        not isinstance(state_record, Mapping)
        or state_record.get("path") != "states/C4-G00-L00-R00-S00.tsv"
        or state_record.get("sha256") != candidate["state_table_member"]["sha256"]
        or state_record.get("size_bytes") != candidate["state_table_member"]["size_bytes"]
    ):
        raise ValueError("candidate manifest does not bind the frozen C4 state table")
    scenarios = _validate_state_table(state_bytes, candidate)

    repo_inputs = record["repo_inputs"]
    if set(repo_inputs) != {"world", "attachments", "inventory", "probe_source", "probe_cmake"}:
        raise ValueError("C4 endpoint gate repository-input set drift")
    for name, identity in repo_inputs.items():
        _repo_file(repo_root, identity, name)
    inventory = json.loads(_repo_file(repo_root, repo_inputs["inventory"], "inventory").read_text(encoding="utf-8"))
    if len(expected_pair_universe(inventory, "robot-fixture-carried-base")) != 365:
        raise ValueError("carried scope does not contain exactly 365 checked pairs")

    amendment_record = record["selection_amendment"]
    if set(amendment_record) != {"path", "file_sha256", "size_bytes", "canonical_content_sha256"}:
        raise ValueError("selection amendment identity keys differ from the frozen schema")
    amendment_path = _repo_file(
        repo_root,
        {
            "path": amendment_record["path"],
            "sha256": amendment_record["file_sha256"],
            "size_bytes": amendment_record["size_bytes"],
        },
        "selection amendment",
    )
    amendment = load_collision_case_selection_amendment(
        str(amendment_path),
        repository_root=repo_root,
        expected_base_protocol_content_sha256="200e830b7633a446d572c54566512e40da4172836a2e0b474e80cc35ee0e80c2",
    )
    if amendment.sha256 != amendment_record["canonical_content_sha256"]:
        raise ValueError("selection amendment canonical content mismatch")

    acceptance = record["acceptance"]
    if acceptance["scope_id"] != "robot-fixture-carried-base" or acceptance["checked_pairs_per_sample"] != 365:
        raise ValueError("C4 endpoint gate pair-coverage acceptance drift")
    if acceptance["expected_distance_rows"] != candidate["sample_count"] * 365:
        raise ValueError("C4 endpoint gate expected row count mismatch")
    if acceptance["support_pair"] != amendment.terminal_contact_contract["pair"]:
        raise ValueError("C4 endpoint gate support pair differs from amendment")
    if acceptance["support_pair_body_types"] != ["attached-object", "world"]:
        raise ValueError("C4 endpoint gate support body types drift")
    if acceptance["terminal_absolute_distance_tolerance_m"] != amendment.terminal_contact_contract["absolute_signed_distance_tolerance_m"]:
        raise ValueError("C4 endpoint gate support tolerance differs from amendment")
    execution = record["execution"]
    required_false = (
        "candidate_selection_executed", "complete_c1_c4_screening_executed",
        "independent_oracle_executed", "screening_is_continuous_certificate",
    )
    if execution["fcl_probe_executable_invocation_count"] != 1 or any(execution[field] is not False for field in required_false):
        raise ValueError("C4 endpoint gate execution boundary drift")
    return C4EndpointGateInputs(
        specification=record,
        specification_file_sha256=sha256_path(spec_path),
        state_table_bytes=state_bytes,
        state_scenarios=scenarios,
        inventory=inventory,
        amendment=amendment.record,
    )


def validate_c4_endpoint_distance_output(
    *,
    state_path: Path,
    distance_path: Path,
    inventory: Mapping[str, Any],
    acceptance: Mapping[str, Any],
) -> dict:
    scenarios = expected_scenarios(state_path, EXPECTED_CANDIDATE)
    universe = expected_pair_universe(inventory, acceptance["scope_id"])
    support_pair = tuple(acceptance["support_pair"])
    support_body_types = tuple(acceptance["support_pair_body_types"])
    if support_pair not in universe:
        raise ValueError("support pair is absent from the exact carried pair universe")
    endpoint_buffer = float(acceptance["endpoint_positive_buffer_m"])
    tolerance = float(acceptance["terminal_absolute_distance_tolerance_m"])
    first_group = None
    last_group = None
    nonterminal_support_minimum = math.inf
    group_count = 0
    for index, group in enumerate(iter_distance_groups(distance_path, scenarios, universe)):
        group_count += 1
        support_value = group.values[support_pair]
        if (support_value.first_body_type, support_value.second_body_type) != support_body_types:
            raise ValueError("support-pair body types differ from the frozen attached/world contract")
        if index == 0:
            first_group = group
        if 0 < index < len(scenarios) - 1:
            value = support_value.distance_m
            nonterminal_support_minimum = min(nonterminal_support_minimum, value)
            if value <= 0.0:
                raise ValueError("controlled support contact occurs before the terminal sample")
        last_group = group
    if group_count != len(scenarios) or first_group is None or last_group is None:
        raise ValueError("distance output sample count mismatch")
    first_support = first_group.values[support_pair].distance_m
    terminal_support = last_group.values[support_pair].distance_m
    if first_support <= endpoint_buffer:
        raise ValueError("first support-pair distance does not exceed the endpoint buffer")
    if abs(terminal_support) > tolerance:
        raise ValueError("terminal support-pair distance exceeds the controlled-contact tolerance")
    other_endpoint_values = [
        value.distance_m
        for group in (first_group, last_group)
        for pair, value in group.values.items()
        if pair != support_pair
    ]
    other_endpoint_minimum = min(other_endpoint_values)
    if other_endpoint_minimum <= endpoint_buffer:
        raise ValueError("an undeclared endpoint pair does not exceed the endpoint buffer")
    return {
        "status": "PASS_C4_ENDPOINT_CONTACT_CONTRACT",
        "candidate_id": EXPECTED_CANDIDATE,
        "sample_count": group_count,
        "checked_pairs_per_sample": len(universe),
        "distance_row_count": group_count * len(universe),
        "first_support_distance_m": first_support,
        "minimum_nonterminal_support_distance_m": nonterminal_support_minimum,
        "terminal_support_distance_m": terminal_support,
        "terminal_absolute_distance_tolerance_m": tolerance,
        "other_endpoint_minimum_distance_m": other_endpoint_minimum,
        "candidate_selected": False,
        "complete_c1_c4_screening_executed": False,
        "paper_result_eligible": False,
    }
