"""Approval-gated authoring collection and label-free source sealing."""

from __future__ import annotations

import hashlib
import json
import shutil
import subprocess
from pathlib import Path
from typing import Any, Iterable, Mapping

from bisafecode.stage4_unseen.canonicalize import CanonicalizationError, canonicalize_source

from . import EXPERIMENT_ID
from .admission import compare_with_prior_catalog, mechanical_admission
from .contracts import validate_freeze_package
from .llm_generator import build_requests, invoke_once, prompt_bytes


class AuthoringLocked(RuntimeError):
    pass


def write_json(path: Path, value: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps(value, indent=2, sort_keys=True, ensure_ascii=False) + "\n",
        encoding="utf-8",
    )


def _head(root: Path) -> str:
    process = subprocess.run(
        ["git", "rev-parse", "HEAD"], cwd=root, text=True,
        capture_output=True, check=False,
    )
    if process.returncode != 0:
        raise AuthoringLocked("cannot resolve freeze commit")
    return process.stdout.strip()


def verify_authoring_approval(root: Path, approval_path: Path) -> Mapping[str, Any]:
    if validate_freeze_package(root):
        raise AuthoringLocked("freeze package validation failed")
    try:
        approval = json.loads(approval_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as error:
        raise AuthoringLocked(f"formal approval is unavailable:{error}") from error
    if approval.get("experiment_id") != EXPERIMENT_ID:
        raise AuthoringLocked("approval experiment mismatch")
    if approval.get("status") != "APPROVED_FOR_CREATE_ONCE_AUTHORING":
        raise AuthoringLocked("create-once authoring is not approved")
    if approval.get("freeze_commit") != _head(root):
        raise AuthoringLocked("approval is not bound to exact HEAD")
    authorizations = approval.get("authorizations", {})
    required = (
        "collect_18_natural_llm_attempts",
        "collect_18_independent_human_attempts",
        "run_mechanical_admission_and_seal",
    )
    if not all(authorizations.get(item) is True for item in required):
        raise AuthoringLocked("authoring/admission authorization incomplete")
    if authorizations.get("paper_acceptance") is not False:
        raise AuthoringLocked("authoring approval cannot accept Paper evidence")
    return approval


def collect_llm_attempts(
    *, root: Path, approval_path: Path, output_directory: Path
) -> Mapping[str, Any]:
    """Dispatch exactly 18 create-once sessions; never retry or overwrite."""

    verify_authoring_approval(root, approval_path)
    if output_directory.exists() and any(output_directory.iterdir()):
        raise FileExistsError(f"refusing to overwrite {output_directory}")
    output_directory.mkdir(parents=True, exist_ok=True)
    artifacts = output_directory / "artifacts"
    artifacts.mkdir()
    attempts: list[Mapping[str, Any]] = []
    requests = build_requests(root)
    if len(requests) != 18:
        raise AuthoringLocked("request schedule is not exactly 18")
    for request in requests:
        result = dict(invoke_once(request, root=root))
        result["prompt_sha256"] = hashlib.sha256(prompt_bytes(request)).hexdigest()
        attempts.append(result)
        basename = f"{request['attempt_ordinal']:02d}_{request['task_card_id']}"
        write_json(artifacts / f"{basename}.json", result)
        if result.get("status") == "completed":
            (artifacts / f"{basename}.py").write_text(
                str(result["program_source"]), encoding="utf-8"
            )
    summary = {
        "schema": "bisafecode.stage4.external-blind.llm-authoring-manifest/v1",
        "experiment_id": EXPERIMENT_ID,
        "freeze_commit": _head(root),
        "attempts": attempts,
        "counts": {
            "assigned": 18,
            "completed": sum(item.get("status") == "completed" for item in attempts),
            "noncompleted": sum(item.get("status") != "completed" for item in attempts),
        },
        "methods_or_oracle_run": False,
        "paper_eligible": False,
    }
    write_json(output_directory / "manifest.json", summary)
    return summary


def expected_human_assignments(root: Path) -> Mapping[str, list[str]]:
    path = root / (
        "03_experiments/contracts/"
        "EXP-S4-008_RQ1_BLIND_EXTERNAL_VALIDITY_FREEZE_ONLY/HUMAN_TASK_CARDS.json"
    )
    document = json.loads(path.read_text(encoding="utf-8"))
    return {key: list(value) for key, value in document["human_assignments"].items()}


def ingest_human_submissions(
    *, root: Path, approval_path: Path, submissions_directory: Path,
    output_directory: Path,
) -> Mapping[str, Any]:
    """Retain the exact assigned submission or a missing/provenance outcome."""

    verify_authoring_approval(root, approval_path)
    if output_directory.exists() and any(output_directory.iterdir()):
        raise FileExistsError(f"refusing to overwrite {output_directory}")
    output_directory.mkdir(parents=True, exist_ok=True)
    artifacts = output_directory / "artifacts"
    artifacts.mkdir()
    attempts: list[Mapping[str, Any]] = []
    for author_id, cards in expected_human_assignments(root).items():
        metadata_path = submissions_directory / f"{author_id}_metadata.json"
        try:
            metadata = json.loads(metadata_path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            metadata = {}
        if metadata_path.is_file():
            metadata_bytes = metadata_path.read_bytes()
            shutil.copyfile(metadata_path, artifacts / metadata_path.name)
            metadata_sha256 = hashlib.sha256(metadata_bytes).hexdigest()
        else:
            metadata_sha256 = None
        attestation = metadata.get("eligibility_attestation", {})
        provenance_ok = all(attestation.get(key) is True for key in (
            "did_not_develop_or_inspect_bisafecode",
            "did_not_view_existing_programs_or_results",
            "used_no_llm_assistance",
            "received_no_safety_or_verifier_feedback",
        ))
        for card_id in cards:
            source_path = submissions_directory / f"{author_id}_{card_id}.py"
            record: dict[str, Any] = {
                "anonymous_author_id": author_id,
                "task_card_id": card_id,
                "source_filename": source_path.name,
                "metadata_sha256": metadata_sha256,
            }
            if not source_path.is_file():
                record["status"] = "missing_submission"
            else:
                source_bytes = source_path.read_bytes()
                record["source_sha256"] = hashlib.sha256(source_bytes).hexdigest()
                try:
                    source = source_bytes.decode("utf-8")
                except UnicodeDecodeError:
                    record["status"] = "invalid_syntax_or_grammar"
                    record["reasons"] = ["SOURCE_NOT_UTF8"]
                else:
                    record["program_source"] = source
                    record["status"] = "submitted" if provenance_ok else "provenance_ineligible"
                    record["provenance_eligible"] = provenance_ok
            attempts.append(record)
            write_json(artifacts / f"{author_id}_{card_id}.json", record)
            if source_path.is_file():
                shutil.copyfile(source_path, artifacts / source_path.name)
    manifest = {
        "schema": "bisafecode.stage4.external-blind.human-authoring-manifest/v1",
        "experiment_id": EXPERIMENT_ID,
        "freeze_commit": _head(root),
        "attempts": attempts,
        "counts": {
            "assigned": 18,
            "submitted": sum(item.get("status") == "submitted" for item in attempts),
            "missing_or_ineligible": sum(item.get("status") != "submitted" for item in attempts),
        },
        "methods_or_oracle_run": False,
        "paper_eligible": False,
    }
    write_json(output_directory / "manifest.json", manifest)
    return manifest


def build_prior_catalog(root: Path) -> list[Mapping[str, Any]]:
    patterns = (
        "03_experiments/canonical_v*/**/*.py",
        "03_experiments/results/EXP-S4-001_UNSEEN_PROGRAM_CORRECTNESS_PREP/**/*.py",
        "03_experiments/raw/EXP-S4-002_CONTROLLED_UNSEEN_PROGRAM_CORRECTNESS/locked_programs/*.py",
    )
    paths: set[Path] = set()
    for pattern in patterns:
        paths.update(root.glob(pattern))
    catalog: list[Mapping[str, Any]] = []
    for path in sorted(paths):
        try:
            source = path.read_text(encoding="utf-8")
            canonical = canonicalize_source(source)
        except (OSError, UnicodeDecodeError, CanonicalizationError, ValueError):
            continue
        catalog.append({
            "reference_id": path.relative_to(root).as_posix(),
            "source_sha256": canonical.source_sha256,
            "canonical_ast_hash": canonical.canonical_ast_hash,
            "structural_family_signature_hash": canonical.structural_family_signature_hash,
            "action_tokens": list(canonical.action_tokens),
        })
    return catalog


def seal_authoring_attempts(
    *, root: Path, attempt_records: Iterable[Mapping[str, Any]],
    output_directory: Path,
) -> Mapping[str, Any]:
    """Admit without labels and produce a method-facing provenance-free view."""

    if output_directory.exists() and any(output_directory.iterdir()):
        raise FileExistsError(f"refusing to overwrite {output_directory}")
    output_directory.mkdir(parents=True, exist_ok=True)
    source_directory = output_directory / "sealed_sources"
    source_directory.mkdir()
    prior = build_prior_catalog(root)
    full_records: list[Mapping[str, Any]] = []
    method_records: list[Mapping[str, Any]] = []
    for ordinal, record_input in enumerate(attempt_records, 1):
        record = dict(record_input)
        source = record.get("program_source")
        eligible_status = record.get("status") in {"completed", "submitted"}
        if not eligible_status or not isinstance(source, str):
            record["correctness_eligible"] = False
            full_records.append(record)
            continue
        admission = dict(mechanical_admission(source, root=root))
        comparison = compare_with_prior_catalog(admission, prior)
        token_material = (
            f"external-attempt-v1:{admission['source_sha256']}:{ordinal}:"
            f"{record.get('task_card_id', '')}"
        )
        opaque = "CASE-" + hashlib.sha256(token_material.encode("utf-8")).hexdigest()[:20].upper()
        record["mechanical_admission"] = admission
        record["prior_comparison"] = comparison
        record["opaque_case_id"] = opaque
        record["correctness_eligible"] = bool(comparison["correctness_eligible"])
        full_records.append(record)
        if record["correctness_eligible"]:
            path = source_directory / f"{opaque}.py"
            path.write_text(source, encoding="utf-8")
            method_records.append({
                "opaque_case_id": opaque,
                "source_path": path.relative_to(output_directory).as_posix(),
                "source_sha256": admission["source_sha256"],
                "canonical_ast_hash": admission["canonical_ast_hash"],
            })
    method_records.sort(key=lambda item: item["opaque_case_id"])
    full_manifest = {
        "schema": "bisafecode.stage4.external-blind.authoring-seal/v1",
        "records": full_records,
        "counts": {
            "assigned": len(full_records),
            "correctness_eligible": sum(bool(item.get("correctness_eligible")) for item in full_records),
        },
        "methods_or_oracle_run": False,
        "paper_eligible": False,
    }
    method_manifest = {
        "schema": "bisafecode.stage4.external-blind.method-input-manifest/v1",
        "records": method_records,
        "provenance_fields_present": False,
        "oracle_fields_present": False,
    }
    write_json(output_directory / "authoring_manifest.json", full_manifest)
    write_json(output_directory / "method_input_manifest.json", method_manifest)
    return full_manifest
