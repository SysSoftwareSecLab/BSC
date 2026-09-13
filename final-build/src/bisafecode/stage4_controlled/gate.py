"""Hard authorization and content-validating phase gate for EXP-S4-002.

FREEZE_ONLY_NOT_FORMAL_STAGE4_EVIDENCE
"""

from __future__ import annotations

from pathlib import Path
from typing import Any, Mapping, Sequence

from .identity import CONTRACT_DIR, compute_asset_identity, compute_contract_identity, compute_toolchain_identity, sha256_file, verify_frozen_core
from .methods import method_ids
from .records import validate_approval_record
from .seals import InvalidPhaseSeal, load_phase_seal, require_valid_phase_seal


class FormalGateLocked(RuntimeError):
    pass


def verify_mac_approval(approval: Mapping[str, Any], *, root: Path, expected_commit: str) -> None:
    errors = validate_approval_record(approval)
    if errors:
        raise FormalGateLocked("; ".join(errors))
    if approval["freeze_commit"] != expected_commit:
        raise FormalGateLocked("approval does not bind the checked-out freeze commit")
    identities = {
        "asset_root_sha256": compute_asset_identity(root)["root_sha256"],
        "contract_root_sha256": compute_contract_identity(root)["root_sha256"],
        "toolchain_root_sha256": compute_toolchain_identity(root)["root_sha256"],
    }
    for key, expected in identities.items():
        if approval.get(key) != expected:
            raise FormalGateLocked(f"approval/live {key} mismatch")
    llm = approval["llm_baseline"]
    llm_files = {
        "prompt_sha256": f"{CONTRACT_DIR}/llm/LLM_JUDGE_PROMPT.txt",
        "cli_invocation_schema_sha256": f"{CONTRACT_DIR}/llm/cli_invocation.schema.json",
        "judge_output_schema_sha256": f"{CONTRACT_DIR}/llm/judge_output.schema.json",
        "aggregation_policy_sha256": f"{CONTRACT_DIR}/llm/AGGREGATION_POLICY.json",
    }
    for key, path in llm_files.items():
        if llm.get(key) != sha256_file(root / path):
            raise FormalGateLocked(f"approval/live LLM {key} mismatch")
    if not verify_frozen_core(root)["passed"]:
        raise FormalGateLocked("S3-002 frozen identity mismatch")


def verify_post_derived_paper_approval(
    approval: Mapping[str, Any], *, derived_root_sha256: str
) -> None:
    del approval, derived_root_sha256
    raise FormalGateLocked(
        "PAPER_EXPORT_NOT_IMPLEMENTED_ATOMIC_AUTHORITY_FINALIZATION_REQUIRED"
    )


def _seal(
    raw_root: Path,
    name: str,
    *,
    repository_root: Path,
    program_ids: Sequence[str],
    predecessors: Mapping[str, str],
) -> Mapping[str, Any]:
    path = raw_root / "seals" / f"{name}.json"
    try:
        seal = load_phase_seal(path)
        if name == "generation":
            expected_files = ["locked_manifest.json"] + [
                value
                for program_id in program_ids
                for value in (f"attempts/{program_id}.json", f"locked_programs/{program_id}.py")
            ]
        elif name in set(method_ids()):
            expected_files = [
                value
                for program_id in program_ids
                for value in (
                    f"methods/{name}/{program_id}.artifact.json",
                    f"methods/{name}/{program_id}.record.json",
                    f"methods/{name}/{program_id}.stdout.log",
                    f"methods/{name}/{program_id}.stderr.log",
                )
            ]
        elif name == "oracle":
            expected_files = [
                value
                for program_id in program_ids
                for value in (
                    f"oracle/{program_id}.evidence.json",
                    f"oracle/{program_id}.record.json",
                    f"oracle/{program_id}.traces.json",
                    f"oracle/{program_id}.stdout.log",
                    f"oracle/{program_id}.stderr.log",
                )
            ]
        else:
            expected_files = None
        require_valid_phase_seal(
            seal=seal,
            expected_phase=name,
            data_root=raw_root,
            expected_record_count=len(program_ids),
            expected_program_ids=program_ids,
            expected_predecessor_roots=predecessors,
            repository_root=repository_root,
            expected_file_paths=expected_files,
        )
        record_paths = []
        if name == "generation":
            record_paths = [(program_id, raw_root / f"attempts/{program_id}.json") for program_id in program_ids]
        elif name in set(method_ids()):
            record_paths = [(program_id, raw_root / f"methods/{name}/{program_id}.record.json") for program_id in program_ids]
        elif name == "oracle":
            record_paths = [(program_id, raw_root / f"oracle/{program_id}.record.json") for program_id in program_ids]
        for program_id, record_path in record_paths:
            try:
                import json

                record = json.loads(record_path.read_text(encoding="utf-8"))
            except (OSError, json.JSONDecodeError) as error:
                raise InvalidPhaseSeal("sealed record is missing or malformed") from error
            if record.get("program_id") != program_id:
                raise InvalidPhaseSeal("sealed record/program ID mismatch")
    except InvalidPhaseSeal as error:
        raise FormalGateLocked(f"{name} seal invalid: {error}") from error
    return seal


def assert_phase_preconditions(
    phase: str,
    *,
    raw_root: Path,
    approval_verified: bool,
    repository_root: Path,
    program_ids: Sequence[str],
    derived_root: Path | None = None,
) -> Mapping[str, Mapping[str, Any]]:
    if not approval_verified:
        raise FormalGateLocked("Mac approval must be verified before any formal phase")
    if len(program_ids) != 60 or len(set(program_ids)) != 60:
        raise FormalGateLocked("formal phase requires the exact 60 program IDs")
    if phase == "locked_generation":
        if raw_root.exists():
            raise FormalGateLocked("Raw root already exists; generation is create-once")
        return {}
    generation = _seal(
        raw_root,
        "generation",
        repository_root=repository_root,
        program_ids=program_ids,
        predecessors={},
    )
    observed = {"generation": generation}
    generation_root = generation["root_sha256"]
    if phase == "methods":
        if (raw_root / "seals" / "oracle.json").exists():
            raise FormalGateLocked("method phase is closed after oracle sealing")
        return observed
    method_seals = {}
    for name in method_ids():
        method_seals[name] = _seal(
            raw_root,
            name,
            repository_root=repository_root,
            program_ids=program_ids,
            predecessors={"generation": generation_root},
        )
    observed.update(method_seals)
    if phase == "oracle":
        if (raw_root / "seals" / "oracle.json").exists():
            raise FormalGateLocked("oracle phase is create-once")
        return observed
    oracle_predecessors = {"generation": generation_root}
    oracle_predecessors.update({name: seal["root_sha256"] for name, seal in method_seals.items()})
    oracle = _seal(
        raw_root,
        "oracle",
        repository_root=repository_root,
        program_ids=program_ids,
        predecessors=oracle_predecessors,
    )
    observed["oracle"] = oracle
    if phase == "derived":
        return observed
    if phase == "paper":
        raise FormalGateLocked(
            "PAPER_EXPORT_NOT_IMPLEMENTED_ATOMIC_AUTHORITY_FINALIZATION_REQUIRED"
        )
    raise ValueError("unknown formal phase")
