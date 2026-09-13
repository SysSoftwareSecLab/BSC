"""Label-blind generation pipeline shared by fixture and future locked runs.

PREP_ONLY_NOT_FORMAL_STAGE4_EVIDENCE
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any, Iterable, List, Mapping, Optional, Sequence, Tuple

from . import PREP_STATUS
from .generator import generate_candidate
from .leakage import check_candidate_leakage
from .schema import make_program_record, validate_manifest


def write_json(path: Path, value: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps(value, indent=2, sort_keys=True, ensure_ascii=False) + "\n",
        encoding="utf-8",
    )


def generate_manifest(
    *,
    root: Path,
    output_directory: Path,
    split: str,
    schedule: Sequence[Tuple[str, int]],
    references: Mapping[str, Any],
    family_assignments: Mapping[str, Sequence[str]],
    fixture_exclusions: Optional[Mapping[str, Any]] = None,
    accepted_target: Optional[int] = None,
    accepted_target_by_family: Optional[Mapping[str, int]] = None,
    replace_prep_only: bool = False,
) -> Mapping[str, Any]:
    """Generate every scheduled attempt, preserving both accepts and rejects."""

    output_directory.mkdir(parents=True, exist_ok=True)
    programs_directory = output_directory / "programs"
    records_directory = output_directory / "records"
    programs_directory.mkdir(parents=True, exist_ok=True)
    records_directory.mkdir(parents=True, exist_ok=True)
    records: List[Mapping[str, Any]] = []
    for ordinal, (family_id, seed) in enumerate(schedule, 1):
        if accepted_target_by_family is not None and sum(
            item["status"] == "accepted" and item["family_id"] == family_id
            for item in records
        ) >= accepted_target_by_family.get(family_id, 0):
            continue
        candidate = generate_candidate(split, family_id, seed)
        program_id = "{}-{:03d}-{}-{}".format(
            split.upper(), ordinal, family_id, seed
        )
        filename = "{:03d}_{}_{}.py".format(ordinal, family_id.lower(), seed)
        source_path = programs_directory / filename
        if source_path.exists():
            if not replace_prep_only or PREP_STATUS not in source_path.read_text(
                encoding="utf-8"
            ):
                raise FileExistsError("refusing to overwrite {}".format(source_path))
        source_path.write_text(candidate.source, encoding="utf-8")
        leakage = check_candidate_leakage(
            source=candidate.source,
            family_id=family_id,
            split=split,
            references=references,
            family_assignments=family_assignments,
            prior_records=records,
            fixture_exclusions=fixture_exclusions,
        )
        record = make_program_record(
            candidate=candidate,
            program_id=program_id,
            source_path=source_path.relative_to(root).as_posix(),
            leakage_report=leakage,
        )
        records.append(record)
        write_json(records_directory / (filename[:-3] + ".json"), record)
        if accepted_target is not None and sum(
            item["status"] == "accepted" for item in records
        ) >= accepted_target:
            break
        if accepted_target_by_family is not None and all(
            sum(
                item["status"] == "accepted" and item["family_id"] == family
                for item in records
            )
            >= target
            for family, target in accepted_target_by_family.items()
        ):
            break
    manifest = {
        "evidence_status": PREP_STATUS,
        "schema": "bisafecode.stage4.unseen.program-manifest/v2",
        "experiment_id": "EXP-S4-001_UNSEEN_PROGRAM_CORRECTNESS_PREP",
        "paper_result_eligible": False,
        "formal_dataset_eligible": False,
        "split": split,
        "generation_order": "schedule order; no label or verifier output exists",
        "records": records,
        "counts": {
            "attempted": len(records),
            "accepted": sum(record["status"] == "accepted" for record in records),
            "rejected": sum(record["status"] == "rejected" for record in records),
        },
    }
    errors = validate_manifest(
        manifest,
        root=root,
        family_assignments=family_assignments,
        fixture_exclusions=fixture_exclusions,
    )
    if errors:
        raise ValueError("generated manifest failed validation: {}".format("; ".join(errors)))
    write_json(output_directory / "manifest.json", manifest)
    return manifest
