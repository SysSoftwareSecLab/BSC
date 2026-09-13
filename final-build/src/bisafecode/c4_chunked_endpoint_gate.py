"""Recoverable, content-addressed execution for the EXP-S2-031 C4 gate.

The predecessor EXP-S2-030 retained all 759,565 distance rows in memory and
wrote only after all 2,081 states had completed.  This module keeps the frozen
candidate and predicates unchanged, but partitions the state sequence before
execution.  Chunking is an execution-recovery mechanism, not candidate search,
an independent oracle, or a continuous collision certificate.
"""

from __future__ import annotations

from dataclasses import dataclass
import hashlib
import json
import math
from pathlib import Path, PurePosixPath
from typing import Any, Iterable, Mapping, Sequence, Tuple

from .c4_endpoint_contact_gate import (
    C4EndpointGateInputs,
    EXPECTED_CANDIDATE,
    load_c4_endpoint_gate_inputs,
    sha256_bytes,
    sha256_path,
)
from .collision_candidate_generation import canonical_json_bytes
from .collision_candidate_screening import (
    SampleDistances,
    expected_pair_universe,
    iter_distance_groups,
)


SCHEMA = "bisafecode.c4-chunked-endpoint-gate/v0.1"
STATUS = "FROZEN_BEFORE_EXECUTION"
MANIFEST_SCHEMA = "bisafecode.c4-state-chunk-manifest/v0.1"
RESULT_SCHEMA = "bisafecode.c4-chunked-endpoint-gate-result/v0.1"
CHUNK_RESULT_SCHEMA = "bisafecode.c4-chunk-result/v0.1"
CHUNK_SIZE = 128
EXPECTED_SAMPLE_COUNT = 2081
EXPECTED_CHUNK_COUNT = 17
EXPECTED_PAIRS_PER_SAMPLE = 365


def _require_exact_keys(record: Mapping[str, Any], expected: set[str], label: str) -> None:
    actual = set(record)
    if actual != expected:
        raise ValueError(
            f"{label} keys differ from the frozen schema: "
            f"missing={sorted(expected - actual)}, extra={sorted(actual - expected)}"
        )


def _require_sha256(value: Any, label: str) -> str:
    if not isinstance(value, str) or len(value) != 64 or any(character not in "0123456789abcdef" for character in value):
        raise ValueError(f"{label} must be a lowercase SHA-256 digest")
    return value


def _repository_file(root: Path, record: Mapping[str, Any], label: str) -> Path:
    _require_exact_keys(record, {"path", "sha256", "size_bytes"}, label)
    relative = PurePosixPath(record["path"])
    if relative.is_absolute() or "." in relative.parts or ".." in relative.parts:
        raise ValueError(f"{label} path must stay within the repository")
    path = root / Path(*relative.parts)
    try:
        path.resolve().relative_to(root.resolve())
    except ValueError as error:
        raise ValueError(f"{label} path escapes the repository") from error
    if path.is_symlink() or not path.is_file():
        raise ValueError(f"{label} must be a regular non-symlink file")
    if path.stat().st_size != record["size_bytes"] or sha256_path(path) != _require_sha256(record["sha256"], label):
        raise ValueError(f"{label} identity mismatch")
    return path


@dataclass(frozen=True)
class StateChunk:
    index: int
    start_index: int
    stop_index_exclusive: int
    scenarios: Tuple[str, ...]
    payload: bytes

    @property
    def chunk_id(self) -> str:
        return f"C4-CHUNK-{self.index:03d}"

    @property
    def record(self) -> dict:
        return {
            "chunk_id": self.chunk_id,
            "index": self.index,
            "start_index": self.start_index,
            "stop_index_exclusive": self.stop_index_exclusive,
            "sample_count": len(self.scenarios),
            "first_scenario": self.scenarios[0],
            "last_scenario": self.scenarios[-1],
            "state_tsv_size_bytes": len(self.payload),
            "state_tsv_sha256": sha256_bytes(self.payload),
            "expected_distance_rows": len(self.scenarios) * EXPECTED_PAIRS_PER_SAMPLE,
        }


def partition_state_table(inputs: C4EndpointGateInputs, chunk_size: int = CHUNK_SIZE) -> Tuple[StateChunk, ...]:
    if chunk_size != CHUNK_SIZE:
        raise ValueError("C4 recovery partition size differs from the frozen 128-state contract")
    payload = inputs.state_table_bytes
    if b"\r" in payload or not payload.endswith(b"\n"):
        raise ValueError("C4 state table must use canonical LF line endings with a final newline")
    lines = payload.splitlines(keepends=True)
    if len(lines) != len(inputs.state_scenarios) + 1:
        raise ValueError("C4 state table physical-line count differs from the validated sample count")
    header = lines[0]
    rows = lines[1:]
    chunks = []
    for index, start in enumerate(range(0, len(rows), chunk_size)):
        stop = min(start + chunk_size, len(rows))
        chunks.append(
            StateChunk(
                index=index,
                start_index=start,
                stop_index_exclusive=stop,
                scenarios=inputs.state_scenarios[start:stop],
                payload=header + b"".join(rows[start:stop]),
            )
        )
    if len(chunks) != EXPECTED_CHUNK_COUNT or len(rows) != EXPECTED_SAMPLE_COUNT:
        raise ValueError("C4 partition cardinality differs from the frozen contract")
    if chunks[-1].record["sample_count"] != 33:
        raise ValueError("C4 final chunk must contain exactly 33 states")
    return tuple(chunks)


def build_chunk_manifest(inputs: C4EndpointGateInputs) -> dict:
    chunks = partition_state_table(inputs)
    return {
        "schema": MANIFEST_SCHEMA,
        "experiment_id": "EXP-S2-031",
        "candidate_id": EXPECTED_CANDIDATE,
        "source_state_table_sha256": sha256_bytes(inputs.state_table_bytes),
        "sample_count": EXPECTED_SAMPLE_COUNT,
        "checked_pairs_per_sample": EXPECTED_PAIRS_PER_SAMPLE,
        "expected_distance_rows": EXPECTED_SAMPLE_COUNT * EXPECTED_PAIRS_PER_SAMPLE,
        "partition": {
            "algorithm": "contiguous-input-order-fixed-count-v1",
            "state_rows_per_chunk": CHUNK_SIZE,
            "chunk_count": EXPECTED_CHUNK_COUNT,
            "final_chunk_state_rows": 33,
            "result_independent_of_distance_values": True,
        },
        "chunks": [chunk.record for chunk in chunks],
    }


@dataclass(frozen=True)
class C4ChunkedGateInputs:
    specification: Mapping[str, Any]
    specification_file_sha256: str
    base: C4EndpointGateInputs
    chunk_manifest: Mapping[str, Any]
    chunks: Tuple[StateChunk, ...]


def load_c4_chunked_gate_inputs(repo_root: Path, spec_path: Path) -> C4ChunkedGateInputs:
    repo_root = repo_root.resolve()
    spec_path = spec_path.resolve()
    if spec_path.is_symlink() or not spec_path.is_file():
        raise ValueError("C4 chunked-gate specification must be a regular non-symlink file")
    record = json.loads(spec_path.read_text(encoding="utf-8"))
    _require_exact_keys(
        record,
        {
            "schema", "status", "experiment_id", "purpose", "paper_result_eligible",
            "predecessor_failure", "base_gate", "chunk_manifest", "partition",
            "retry_policy", "execution", "acceptance", "prohibited_operations",
        },
        "C4 chunked-gate specification",
    )
    if record["schema"] != SCHEMA or record["status"] != STATUS or record["experiment_id"] != "EXP-S2-031":
        raise ValueError("unsupported or already-executed C4 chunked gate")
    if record["paper_result_eligible"] is not False:
        raise ValueError("C4 chunked gate cannot be a paper result")

    predecessor = _repository_file(repo_root, record["predecessor_failure"], "predecessor failure archive")
    if predecessor.name != "bisafecode_EXP-S2-030_C4_endpoint_contact_gate_FAIL_20260808.tar.gz":
        raise ValueError("C4 chunked gate is not bound to the immutable EXP-S2-030 failure")
    base_path = _repository_file(repo_root, record["base_gate"], "base endpoint-gate specification")
    base = load_c4_endpoint_gate_inputs(repo_root, base_path)
    chunks = partition_state_table(base)
    manifest_path = _repository_file(repo_root, record["chunk_manifest"], "C4 chunk manifest")
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    if manifest != build_chunk_manifest(base):
        raise ValueError("C4 chunk manifest differs from the deterministic frozen partition")

    partition = record["partition"]
    if partition != manifest["partition"]:
        raise ValueError("C4 chunked-gate partition differs from the content-addressed manifest")
    retry = record["retry_policy"]
    expected_retry = {
        "max_attempts_per_chunk": 2,
        "retry_requires_explicit_interruption_acknowledgement": True,
        "retryable_prior_outcomes": ["NO_RESULT_EXTERNAL_INTERRUPTION", "FAIL_INFRASTRUCTURE_TIMEOUT"],
        "completed_probe_nonzero_is_retryable": False,
        "completed_pair_or_geometry_failure_is_retryable": False,
        "previous_attempts_must_remain_immutable": True,
    }
    if retry != expected_retry:
        raise ValueError("C4 chunk retry policy differs from the frozen fail-closed contract")
    execution = record["execution"]
    expected_execution = {
        "successful_probe_invocation_count": EXPECTED_CHUNK_COUNT,
        "maximum_probe_attempt_count": EXPECTED_CHUNK_COUNT * retry["max_attempts_per_chunk"],
        "probe_timeout_seconds_per_chunk": 180,
        "exactly_one_chunk_per_runner_process": True,
        "all_chunks_required_before_endpoint_predicate": True,
        "candidate_selection_executed": False,
        "complete_c1_c4_screening_executed": False,
        "independent_oracle_executed": False,
        "screening_is_continuous_certificate": False,
    }
    if execution != expected_execution:
        raise ValueError("C4 chunked execution contract drift")
    if record["acceptance"] != base.specification["acceptance"]:
        raise ValueError("C4 chunked gate changed the predecessor acceptance predicate")
    return C4ChunkedGateInputs(record, sha256_path(spec_path), base, manifest, chunks)


def validate_attempt_transition(
    *,
    requested_attempt: int,
    prior_attempt_results: Sequence[Mapping[str, Any] | None],
    interruption_acknowledged: bool,
) -> None:
    if requested_attempt not in {1, 2}:
        raise ValueError("chunk attempt must be 1 or 2")
    if requested_attempt == 1:
        if prior_attempt_results:
            raise ValueError("attempt 1 cannot follow an existing attempt")
        if interruption_acknowledged:
            raise ValueError("attempt 1 cannot acknowledge a prior interruption")
        return
    if len(prior_attempt_results) != 1 or not interruption_acknowledged:
        raise ValueError("attempt 2 requires exactly one preserved prior attempt and explicit acknowledgement")
    prior = prior_attempt_results[0]
    if prior is None:
        return
    if prior.get("schema") != CHUNK_RESULT_SCHEMA:
        raise ValueError("prior attempt result schema is invalid")
    if prior.get("status") != "FAIL_INFRASTRUCTURE_TIMEOUT":
        raise ValueError("completed prior outcome is not infrastructure-retryable")


def _apply_endpoint_predicate(
    *,
    groups: Iterable[SampleDistances],
    expected_sample_count: int,
    universe: frozenset[tuple[str, str, str]],
    acceptance: Mapping[str, Any],
) -> dict:
    support_pair = tuple(acceptance["support_pair"])
    support_body_types = tuple(acceptance["support_pair_body_types"])
    if support_pair not in universe:
        raise ValueError("support pair is absent from the exact carried pair universe")
    endpoint_buffer = float(acceptance["endpoint_positive_buffer_m"])
    tolerance = float(acceptance["terminal_absolute_distance_tolerance_m"])
    first_group = None
    last_group = None
    nonterminal_support_minimum = math.inf
    count = 0
    for index, group in enumerate(groups):
        count += 1
        support = group.values[support_pair]
        if (support.first_body_type, support.second_body_type) != support_body_types:
            raise ValueError("support-pair body types differ from the frozen attached/world contract")
        if index == 0:
            first_group = group
        if 0 < index < expected_sample_count - 1:
            nonterminal_support_minimum = min(nonterminal_support_minimum, support.distance_m)
            if support.distance_m <= 0.0:
                raise ValueError("controlled support contact occurs before the terminal sample")
        last_group = group
    if count != expected_sample_count or first_group is None or last_group is None:
        raise ValueError("chunk union does not contain the exact frozen sample count")
    first_support = first_group.values[support_pair].distance_m
    terminal_support = last_group.values[support_pair].distance_m
    if first_support <= endpoint_buffer:
        raise ValueError("first support-pair distance does not exceed the endpoint buffer")
    if abs(terminal_support) > tolerance:
        raise ValueError("terminal support-pair distance exceeds the controlled-contact tolerance")
    other_endpoint_minimum = min(
        value.distance_m
        for group in (first_group, last_group)
        for pair, value in group.values.items()
        if pair != support_pair
    )
    if other_endpoint_minimum <= endpoint_buffer:
        raise ValueError("an undeclared endpoint pair does not exceed the endpoint buffer")
    return {
        "status": "PASS_C4_ENDPOINT_CONTACT_CONTRACT",
        "candidate_id": EXPECTED_CANDIDATE,
        "sample_count": count,
        "checked_pairs_per_sample": len(universe),
        "distance_row_count": count * len(universe),
        "first_support_distance_m": first_support,
        "minimum_nonterminal_support_distance_m": nonterminal_support_minimum,
        "terminal_support_distance_m": terminal_support,
        "terminal_absolute_distance_tolerance_m": tolerance,
        "other_endpoint_minimum_distance_m": other_endpoint_minimum,
        "candidate_selected": False,
        "complete_c1_c4_screening_executed": False,
        "paper_result_eligible": False,
    }


def validate_complete_chunk_set(inputs: C4ChunkedGateInputs, output_root: Path) -> dict:
    identity_path = output_root / "run_identity.json"
    if identity_path.is_symlink() or not identity_path.is_file():
        raise ValueError("chunked run identity is missing")
    identity = json.loads(identity_path.read_text(encoding="utf-8"))
    _require_exact_keys(
        identity,
        {
            "schema", "experiment_id", "specification_sha256", "chunk_manifest_file_sha256",
            "candidate_state_table_sha256", "probe_binary", "runtime_geometry_identity",
            "paper_result_eligible",
        },
        "chunked run identity",
    )
    if identity["schema"] != "bisafecode.c4-chunked-run-identity/v0.1" or identity["experiment_id"] != "EXP-S2-031":
        raise ValueError("chunked run identity schema or experiment differs")
    if identity.get("specification_sha256") != inputs.specification_file_sha256:
        raise ValueError("chunked run identity targets a different specification")
    if identity["chunk_manifest_file_sha256"] != inputs.specification["chunk_manifest"]["sha256"]:
        raise ValueError("chunked run identity targets a different chunk manifest")
    if identity["candidate_state_table_sha256"] != inputs.chunk_manifest["source_state_table_sha256"]:
        raise ValueError("chunked run identity targets a different candidate state table")
    if identity["paper_result_eligible"] is not False:
        raise ValueError("chunked run identity broadens the evidence boundary")
    binary = identity["probe_binary"]
    if not isinstance(binary, Mapping):
        raise ValueError("chunked run probe-binary identity is missing")
    _require_exact_keys(binary, {"path", "sha256", "size_bytes"}, "chunked run probe binary")
    binary_path = Path(binary["path"])
    if (
        binary_path.is_symlink()
        or not binary_path.is_file()
        or binary_path.stat().st_size != binary["size_bytes"]
        or sha256_path(binary_path) != _require_sha256(binary["sha256"], "chunked run probe binary")
    ):
        raise ValueError("chunked run probe-binary identity no longer matches the executed binary")
    universe = expected_pair_universe(inputs.base.inventory, inputs.specification["acceptance"]["scope_id"])
    selected = []
    group_iterators = []
    total_attempts = 0
    infrastructure_attempts = 0
    for chunk in inputs.chunks:
        chunk_root = output_root / "chunks" / f"chunk_{chunk.index:03d}"
        attempts = sorted(path for path in chunk_root.glob("attempt_*")) if chunk_root.is_dir() else []
        if any(path.is_symlink() or not path.is_dir() for path in attempts):
            raise ValueError(f"chunk {chunk.index} contains a non-regular attempt directory")
        if [path.name for path in attempts] not in (["attempt_001"], ["attempt_001", "attempt_002"]):
            raise ValueError(f"chunk {chunk.index} attempt sequence differs from the frozen maximum-two policy")
        total_attempts += len(attempts)
        if len(attempts) == 2:
            first_result_path = attempts[0] / "chunk_result.json"
            first_result = json.loads(first_result_path.read_text(encoding="utf-8")) if first_result_path.is_file() else None
            acknowledgement = attempts[0] / "infrastructure_interruption_acknowledgement.json"
            if not acknowledgement.is_file():
                raise ValueError(f"chunk {chunk.index} attempt 2 lacks explicit interruption acknowledgement")
            if first_result is not None and first_result.get("status") != "FAIL_INFRASTRUCTURE_TIMEOUT":
                raise ValueError(f"chunk {chunk.index} retried a completed non-infrastructure outcome")
            infrastructure_attempts += 1
        pass_records = []
        for attempt_path in attempts:
            result_path = attempt_path / "chunk_result.json"
            if result_path.is_file():
                result = json.loads(result_path.read_text(encoding="utf-8"))
                if result.get("status") == "PASS_CHUNK":
                    pass_records.append((attempt_path, result))
        if len(pass_records) != 1:
            raise ValueError(f"chunk {chunk.index} must contain exactly one successful preserved attempt")
        attempt_path, result = pass_records[0]
        if (
            result.get("schema") != CHUNK_RESULT_SCHEMA
            or result.get("chunk") != chunk.record
            or result.get("attempt") != int(attempt_path.name.removeprefix("attempt_"))
            or result.get("retryable_under_frozen_policy") is not False
            or result.get("paper_result_eligible") is not False
        ):
            raise ValueError(f"chunk {chunk.index} result identity differs from the frozen manifest")
        distance_path = attempt_path / "distance_output.tsv"
        distance_record = result.get("distance_output")
        if not isinstance(distance_record, Mapping):
            raise ValueError(f"chunk {chunk.index} distance record is missing")
        if (
            distance_path.is_symlink()
            or not distance_path.is_file()
            or distance_path.stat().st_size != distance_record.get("size_bytes")
            or sha256_path(distance_path) != distance_record.get("sha256")
        ):
            raise ValueError(f"chunk {chunk.index} distance output identity mismatch")
        selected.append({"chunk_index": chunk.index, "attempt": attempt_path.name, **distance_record})
        group_iterators.append(iter_distance_groups(distance_path, chunk.scenarios, universe))

    def groups() -> Iterable[SampleDistances]:
        for iterator in group_iterators:
            yield from iterator

    validation = _apply_endpoint_predicate(
        groups=groups(),
        expected_sample_count=EXPECTED_SAMPLE_COUNT,
        universe=universe,
        acceptance=inputs.specification["acceptance"],
    )
    return {
        "schema": RESULT_SCHEMA,
        "status": validation["status"],
        "paper_result_eligible": False,
        "specification_sha256": inputs.specification_file_sha256,
        "chunk_manifest_file_sha256": inputs.specification["chunk_manifest"]["sha256"],
        "chunk_manifest_canonical_sha256": sha256_bytes(canonical_json_bytes(inputs.chunk_manifest)),
        "successful_probe_invocations": EXPECTED_CHUNK_COUNT,
        "total_probe_attempts": total_attempts,
        "infrastructure_retry_count": infrastructure_attempts,
        "selected_chunk_attempts": selected,
        "validation": validation,
        "candidate_selection_executed": False,
        "complete_c1_c4_screening_executed": False,
        "independent_oracle_executed": False,
        "screening_is_continuous_certificate": False,
        "prohibited_operations_executed": False,
    }
