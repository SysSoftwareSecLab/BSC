#!/usr/bin/env python3
"""Recount and validate the compact generated-program cohort projections."""

from __future__ import annotations

import hashlib
import json
import re
from collections import Counter
from pathlib import Path
from typing import Any


ARTIFACT_ROOT = Path(__file__).resolve().parents[3]
RESULT_ROOT = ARTIFACT_ROOT / "results" / "final-revalidation" / "generated-cohorts"
LLM_ROOT = ARTIFACT_ROOT / "llm-generation"
SHA256_RE = re.compile(r"^[0-9a-f]{64}$")
RETAINED_PRIVATE_STATUS = "WITHHELD_NOT_PUBLICLY_BOUND"


NO_REPAIR_FIELDS = {
    "case_id",
    "source_sha256",
    "model_family",
    "generation_mode",
    "target_property",
    "task_card_id",
    "source_action_count",
    "challenge_realized",
    "method_status",
    "method_verdict",
    "release_allowed",
    "oracle_status",
    "oracle_verdict",
    "oracle_reason_codes",
    "oracle_scorable",
    "binary_label_match",
}

Q4_FIELDS = {
    "case_id",
    "source_sha256",
    "source_action_count",
    "action_count_target_met",
    "structural_target_met",
    "frozen_first_method_verdict",
    "frozen_first_method_reason_codes",
    "release_allowed",
    "method_run_unchanged",
    "method_rerun_or_replacement",
    "prior_oracle_verdict",
    "corrected_oracle_verdict",
    "corrected_oracle_process_status",
    "corrected_oracle_process_exit",
    "corrected_oracle_reason_codes",
    "corrected_oracle_finite_trace_count",
    "corrected_oracle_coverage_complete",
    "corrected_oracle_qualified",
    "corrected_oracle_scorable",
    "correction_disposition",
}


def fail(message: str) -> None:
    raise SystemExit(f"FAIL: {message}")


def require(actual: Any, expected: Any, label: str) -> None:
    if actual != expected:
        fail(f"{label}: expected {expected!r}, observed {actual!r}")


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def load_json(path: Path) -> Any:
    try:
        with path.open("r", encoding="utf-8") as handle:
            return json.load(handle)
    except (OSError, json.JSONDecodeError) as exc:
        fail(f"cannot read {path.relative_to(ARTIFACT_ROOT)}: {exc}")


def load_jsonl(path: Path) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    try:
        with path.open("r", encoding="utf-8") as handle:
            for line_number, line in enumerate(handle, start=1):
                if not line.strip():
                    fail(f"blank JSONL line in {path.relative_to(ARTIFACT_ROOT)}:{line_number}")
                row = json.loads(line)
                if not isinstance(row, dict):
                    fail(f"non-object JSONL row in {path.relative_to(ARTIFACT_ROOT)}:{line_number}")
                rows.append(row)
    except (OSError, json.JSONDecodeError) as exc:
        fail(f"cannot read {path.relative_to(ARTIFACT_ROOT)}: {exc}")
    return rows


def counts(rows: list[dict[str, Any]], key: str) -> dict[str, int]:
    return dict(sorted(Counter(str(row[key]) for row in rows).items()))


def check_hash(value: Any, label: str, *, nullable: bool = False) -> None:
    if value is None and nullable:
        return
    if not isinstance(value, str) or not SHA256_RE.fullmatch(value):
        fail(f"{label} is not a lowercase SHA-256")


def verify_checksum_manifest() -> int:
    manifest_path = RESULT_ROOT / "SHA256SUMS"
    if not manifest_path.is_file():
        fail("results/final-revalidation/generated-cohorts/SHA256SUMS is missing")

    listed: dict[str, str] = {}
    for line_number, line in enumerate(manifest_path.read_text(encoding="utf-8").splitlines(), start=1):
        match = re.fullmatch(r"([0-9a-f]{64})  (.+)", line)
        if not match:
            fail(f"malformed SHA256SUMS line {line_number}")
        digest, relative = match.groups()
        if relative in listed:
            fail(f"duplicate SHA256SUMS path: {relative}")
        if relative.startswith("/") or ".." in Path(relative).parts:
            fail(f"unsafe SHA256SUMS path: {relative}")
        listed[relative] = digest

    actual_files = {
        path.relative_to(RESULT_ROOT).as_posix()
        for path in RESULT_ROOT.rglob("*")
        if path.is_file() and path != manifest_path and not path.name.startswith("._")
    }
    require(set(listed), actual_files, "generated-cohorts checksum file set")
    for relative, expected_digest in sorted(listed.items()):
        require(sha256_file(RESULT_ROOT / relative), expected_digest, f"checksum {relative}")
    return len(listed)


def validate_no_repair_generation_projection(
    result_rows: list[dict[str, Any]],
    result_summary: dict[str, Any],
) -> dict[str, int]:
    root = LLM_ROOT / "no-repair-36"
    generation_rows = load_jsonl(root / "generation-records.jsonl")
    cohort_summary = load_json(root / "cohort-summary.json")
    cards_document = load_json(root / "task-cards.json")
    card_rows = cards_document.get("cards") if isinstance(cards_document, dict) else None
    if not isinstance(card_rows, list):
        fail("no-repair public task cards are absent")
    cards: dict[str, dict[str, Any]] = {}
    for card in card_rows:
        if not isinstance(card, dict) or not isinstance(card.get("card_id"), str):
            fail("no-repair public task-card row is malformed")
        if card["card_id"] in cards:
            fail(f"no-repair public task-card id is duplicated: {card['card_id']}")
        cards[card["card_id"]] = card
    require(len(cards), 12, "no-repair public task-card count")
    system_prompt = (root / "generation-prompt.txt").read_text(encoding="utf-8")
    manual = (root / "public-authoring-manual.md").read_text(encoding="utf-8")
    require(len(generation_rows), 36, "no-repair public generation record count")

    expected_ordinals = set(range(1, 37))
    observed_ordinals: set[int] = set()
    expected_program_names: set[str] = set()
    expected_output_names: set[str] = set()
    eligible: dict[str, dict[str, Any]] = {}
    admitted_count = 0
    invalid_count = 0
    duplicate_count = 0

    for position, record in enumerate(generation_rows, start=1):
        ordinal = record.get("global_ordinal")
        if not isinstance(ordinal, int) or ordinal not in expected_ordinals:
            fail(f"no-repair generation row {position} has invalid ordinal")
        if ordinal in observed_ordinals:
            fail(f"no-repair generation repeats ordinal {ordinal}")
        observed_ordinals.add(ordinal)

        require(record.get("status"), "completed", f"no-repair generation {ordinal} status")
        require(record.get("dispatched"), True, f"no-repair generation {ordinal} dispatched")
        require(record.get("exit_code"), 0, f"no-repair generation {ordinal} exit code")
        require(record.get("repair_performed"), False, f"no-repair generation {ordinal} repair")
        require(record.get("replacement_performed"), False, f"no-repair generation {ordinal} replacement")

        family = record.get("family_id")
        task_card = record.get("task_card_id")
        if not isinstance(family, str) or not isinstance(task_card, str):
            fail(f"no-repair generation {ordinal} lacks family/card identity")
        card = cards.get(task_card)
        if card is None:
            fail(f"no-repair generation {ordinal} references absent task card {task_card!r}")
        require(record.get("property"), card.get("property"), f"no-repair generation {ordinal} card property")
        require(record.get("mode"), card.get("mode"), f"no-repair generation {ordinal} card mode")
        prompt_payload = {"public_authoring_manual": manual, "task_card": card}
        prompt = (
            system_prompt.rstrip()
            + "\n\nAUTHORING_INPUT_JSON\n"
            + json.dumps(prompt_payload, sort_keys=True, separators=(",", ":"), ensure_ascii=False)
            + "\n"
        )
        require(
            hashlib.sha256(prompt.encode("utf-8")).hexdigest(),
            record.get("prompt_sha256"),
            f"no-repair generation {ordinal} reconstructed prompt",
        )
        stem = f"{ordinal:02d}_{family.replace('.', '_')}_{task_card}"
        program_name = f"{stem}.py"
        output_name = f"{stem}.json"
        expected_program_names.add(program_name)
        expected_output_names.add(output_name)
        program_path = root / "programs" / program_name
        output_path = root / "outputs" / output_name
        source_digest = sha256_file(program_path)
        require(source_digest, record.get("program_source_sha256"), f"no-repair generation {ordinal} program bytes")
        output = load_json(output_path)
        output_source = output.get("program_source")
        if not isinstance(output_source, str):
            fail(f"no-repair generation {ordinal} public output lacks program_source")
        require(
            hashlib.sha256(output_source.encode("utf-8")).hexdigest(),
            source_digest,
            f"no-repair generation {ordinal} output/program join",
        )

        admission = record.get("admission")
        if not isinstance(admission, dict):
            fail(f"no-repair generation {ordinal} lacks admission record")
        require(admission.get("source_sha256"), source_digest, f"no-repair generation {ordinal} admission source")
        status = admission.get("status")
        correctness_eligible = admission.get("correctness_eligible")
        if type(correctness_eligible) is not bool:
            fail(f"no-repair generation {ordinal} correctness_eligible is not Boolean")
        duplicate_refs = admission.get("exact_prior_duplicate")
        if not isinstance(duplicate_refs, list):
            fail(f"no-repair generation {ordinal} duplicate record is malformed")

        if status == "admitted":
            admitted_count += 1
        elif status == "invalid_syntax_or_grammar":
            invalid_count += 1
        else:
            fail(f"no-repair generation {ordinal} has unsupported admission status {status!r}")

        if correctness_eligible:
            require(status, "admitted", f"no-repair generation {ordinal} eligible admission status")
            require(duplicate_refs, [], f"no-repair generation {ordinal} eligible duplicate list")
            case_id = admission.get("opaque_case_id")
            if not isinstance(case_id, str) or not re.fullmatch(r"CASE-[0-9A-F]{20}", case_id):
                fail(f"no-repair generation {ordinal} has invalid eligible case id")
            if case_id in eligible:
                fail(f"no-repair generation has duplicate eligible case id {case_id}")
            eligible[case_id] = record
        elif status == "admitted":
            if not duplicate_refs:
                fail(f"no-repair generation {ordinal} is admitted/ineligible without duplicate evidence")
            duplicate_count += 1

    require(observed_ordinals, expected_ordinals, "no-repair generation ordinal set")
    require(
        {path.name for path in (root / "programs").glob("*.py")},
        expected_program_names,
        "no-repair public program file set",
    )
    require(
        {path.name for path in (root / "outputs").glob("*.json")},
        expected_output_names,
        "no-repair public output file set",
    )
    require((admitted_count, invalid_count, duplicate_count, len(eligible)), (30, 6, 9, 21), "no-repair public funnel")
    require(counts(generation_rows, "task_card_id"), {card_id: 3 for card_id in sorted(cards)}, "no-repair task-card assignment counts")
    require(
        counts(generation_rows, "family_id"),
        {"gpt-5.4": 12, "gpt-5.5": 12, "gpt-5.6": 12},
        "no-repair model-family assignment counts",
    )

    require(cohort_summary.get("assigned_generation_attempts"), 36, "no-repair generation summary assigned")
    require(cohort_summary.get("completed_generation_attempts"), 36, "no-repair generation summary completed")
    require(cohort_summary.get("mechanically_admitted"), admitted_count, "no-repair generation summary admitted")
    require(cohort_summary.get("unique_after_deduplication"), len(eligible), "no-repair generation summary unique")
    require(cohort_summary.get("repairs"), 0, "no-repair generation summary repairs")
    require(cohort_summary.get("replacements"), 0, "no-repair generation summary replacements")
    require(cohort_summary.get("oracle_valid_scored"), 20, "no-repair generation summary scored")
    require(cohort_summary.get("original_contract_status"), "NOT_RECOVERED", "no-repair original contract boundary")
    require("missing_original_contract_sha256" in cohort_summary, False, "no-repair unrecovered contract digest absence")
    require(result_summary["funnel"]["generated"], len(generation_rows), "no-repair result/generation generated join")
    require(result_summary["funnel"]["mechanically_invalid"], invalid_count, "no-repair result/generation invalid join")
    require(result_summary["funnel"]["admitted_including_duplicates"], admitted_count, "no-repair result/generation admitted join")
    require(result_summary["funnel"]["unique_admitted_included_here"], len(eligible), "no-repair result/generation unique join")

    result_by_id = {row["case_id"]: row for row in result_rows}
    require(set(result_by_id), set(eligible), "no-repair eligible generation/result case set")
    for case_id, record in eligible.items():
        row = result_by_id[case_id]
        admission = record["admission"]
        require(row["source_sha256"], record["program_source_sha256"], f"{case_id} generation/result source")
        require(row["source_sha256"], admission["source_sha256"], f"{case_id} admission/result source")
        require(row["model_family"], record["family_id"], f"{case_id} generation/result family")
        require(row["generation_mode"], record["mode"], f"{case_id} generation/result mode")
        require(row["target_property"], record["property"], f"{case_id} generation/result property")
        require(row["task_card_id"], record["task_card_id"], f"{case_id} generation/result task card")

    return {
        "generated": len(generation_rows),
        "admitted_including_duplicates": admitted_count,
        "duplicate_excluded": duplicate_count,
        "unique_admitted": len(eligible),
        "task_cards_and_reconstructed_prompts": 36,
    }


def validate_q4_generation_projection(
    result_rows: list[dict[str, Any]],
    result_summary: dict[str, Any],
) -> dict[str, int]:
    root = LLM_ROOT / "controlled-extension-24"
    generation_rows = load_jsonl(root / "generation-records.jsonl")
    cohort_summary = load_json(root / "cohort-summary.json")
    cards_document = load_json(root / "task-cards.json")
    card_rows = cards_document.get("cards") if isinstance(cards_document, dict) else None
    if not isinstance(card_rows, list):
        fail("Q4 public task cards are absent")
    require(len(card_rows), 24, "Q4 public task-card count")
    card_ids = [card.get("card_id") if isinstance(card, dict) else None for card in card_rows]
    require(len(set(card_ids)), 24, "Q4 public task-card identity count")
    template = (root / "generation-prompt-template.md").read_text(encoding="utf-8")
    manual = (root / "public-authoring-manual.md").read_text(encoding="utf-8")
    require(len(generation_rows), 24, "Q4 public generation record count")

    expected_ids = {f"Q4-SLOT-{number:02d}" for number in range(1, 25)}
    observed_ids: set[str] = set()
    expected_program_names: set[str] = set()
    expected_output_names: set[str] = set()
    expected_prompt_names: set[str] = set()
    by_id: dict[str, dict[str, Any]] = {}
    for position, record in enumerate(generation_rows, start=1):
        case_id = record.get("slot_id")
        if case_id not in expected_ids or case_id in observed_ids:
            fail(f"Q4 generation row {position} has invalid or duplicate slot id")
        require(case_id, f"Q4-SLOT-{position:02d}", f"Q4 generation row {position} order")
        observed_ids.add(case_id)
        by_id[case_id] = record
        card = card_rows[position - 1]
        if not isinstance(card, dict) or not isinstance(card.get("card_id"), str):
            fail(f"Q4 public task-card row {position} is malformed")
        require(record.get("task_card_id"), card["card_id"], f"{case_id} generation/task-card join")
        canonical_card = (
            json.dumps(card, sort_keys=True, separators=(",", ":"), ensure_ascii=True, allow_nan=False) + "\n"
        ).encode("ascii")
        require(
            hashlib.sha256(canonical_card).hexdigest(),
            record.get("task_card_sha256"),
            f"{case_id} task-card bytes",
        )
        rendered_prompt = template.replace("{{PUBLIC_AUTHORING_MANUAL}}", manual).replace(
            "{{TASK_CARD_JSON}}",
            json.dumps(card, indent=2, sort_keys=True, ensure_ascii=True),
        )
        if "{{" in rendered_prompt or "}}" in rendered_prompt:
            fail(f"{case_id} reconstructed prompt retains a placeholder")
        prompt_name = f"{case_id}.txt"
        expected_prompt_names.add(prompt_name)
        prompt_path = root / "rendered-prompts" / prompt_name
        require(
            hashlib.sha256(rendered_prompt.encode("utf-8")).hexdigest(),
            sha256_file(prompt_path),
            f"{case_id} reconstructed/rendered prompt join",
        )
        require(sha256_file(prompt_path), record.get("prompt_sha256"), f"{case_id} rendered prompt/generation join")
        require(record.get("attempt"), 1, f"{case_id} generation attempt")
        require(record.get("maximum_attempts"), 1, f"{case_id} maximum generation attempts")
        require(record.get("duplicate"), False, f"{case_id} generation duplicate")
        require(record.get("replacement_allowed"), False, f"{case_id} replacement allowed")
        require(record.get("replacement_performed"), False, f"{case_id} replacement performed")
        require(record.get("eligible_for_primary_evaluation"), True, f"{case_id} primary eligibility")
        require(record.get("source_eligible_for_fixed_source"), True, f"{case_id} fixed-source eligibility")
        process = record.get("process")
        if not isinstance(process, dict):
            fail(f"{case_id} generation process record is absent")
        require(process.get("clean"), True, f"{case_id} generation process clean")
        require(process.get("exit_code"), 0, f"{case_id} generation process exit")
        require(process.get("stdin_fully_sent"), True, f"{case_id} generation stdin")

        program_name = f"{case_id}.py"
        output_name = f"{case_id}.json"
        expected_program_names.add(program_name)
        expected_output_names.add(output_name)
        program_path = root / "programs" / program_name
        output_path = root / "outputs" / output_name
        source_digest = sha256_file(program_path)
        require(source_digest, record.get("source_sha256"), f"{case_id} generation program bytes")
        require(sha256_file(output_path), record.get("raw_final_sha256"), f"{case_id} generation raw output bytes")
        output = load_json(output_path)
        output_source = output.get("program_source")
        if not isinstance(output_source, str):
            fail(f"{case_id} public output lacks program_source")
        require(
            hashlib.sha256(output_source.encode("utf-8")).hexdigest(),
            source_digest,
            f"{case_id} output/program join",
        )

    require(observed_ids, expected_ids, "Q4 generation slot set")
    require(
        {path.name for path in (root / "programs").glob("*.py")},
        expected_program_names,
        "Q4 public program file set",
    )
    require(
        {path.name for path in (root / "outputs").glob("*.json")},
        expected_output_names,
        "Q4 public output file set",
    )
    require(
        {path.name for path in (root / "rendered-prompts").glob("*.txt")},
        expected_prompt_names,
        "Q4 public rendered-prompt file set",
    )
    require(cohort_summary.get("assigned_generation_attempts"), 24, "Q4 generation summary assigned")
    require(cohort_summary.get("completed_generation_attempts"), 24, "Q4 generation summary completed")
    require(cohort_summary.get("fixed_source_eligible"), 24, "Q4 generation summary eligible")
    require(cohort_summary.get("one_attempt_each"), True, "Q4 generation summary one attempt")
    require(cohort_summary.get("replacements"), 0, "Q4 generation summary replacements")
    require(result_summary.get("fixed_programs"), len(generation_rows), "Q4 result/generation fixed rows")
    require(result_summary.get("generation_attempts_per_slot"), 1, "Q4 result/generation attempt count")

    result_by_id = {row["case_id"]: row for row in result_rows}
    require(set(result_by_id), set(by_id), "Q4 generation/result slot set")
    for case_id, record in by_id.items():
        require(result_by_id[case_id]["source_sha256"], record["source_sha256"], f"{case_id} generation/result source")

    return {
        "generated": len(generation_rows),
        "one_attempt_each": 1,
        "replacements": 0,
        "task_cards_and_rendered_prompts": len(expected_prompt_names),
    }


def validate_no_repair() -> dict[str, Any]:
    data_path = RESULT_ROOT / "no-repair" / "per-instance-results.jsonl"
    summary_path = RESULT_ROOT / "no-repair" / "summary.json"
    rows = load_jsonl(data_path)
    summary = load_json(summary_path)

    require(len(rows), 21, "no-repair unique row count")
    expected_ids: set[str] = set()
    for index, row in enumerate(rows, start=1):
        require(set(row), NO_REPAIR_FIELDS, f"no-repair row {index} field set")
        case_id = row["case_id"]
        if not isinstance(case_id, str) or not re.fullmatch(r"CASE-[0-9A-F]{20}", case_id):
            fail(f"no-repair row {index} has invalid anonymous case id")
        if case_id in expected_ids:
            fail(f"duplicate no-repair case id: {case_id}")
        expected_ids.add(case_id)
        check_hash(row["source_sha256"], f"{case_id} source_sha256")
        require(row["release_allowed"], row["method_verdict"] == "verified-within-bounds", f"{case_id} release rule")
        scorable = row["oracle_verdict"] in {"safe", "unsafe"}
        require(row["oracle_scorable"], scorable, f"{case_id} oracle_scorable")
        expected_method = {"safe": "verified-within-bounds", "unsafe": "violated"}.get(row["oracle_verdict"])
        require(row["binary_label_match"], scorable and row["method_verdict"] == expected_method, f"{case_id} binary_label_match")

    scorable = [row for row in rows if row["oracle_scorable"]]
    unsafe = [row for row in rows if row["oracle_verdict"] == "unsafe"]
    all_method_counts = counts(rows, "method_verdict")
    scored_method_counts = counts(scorable, "method_verdict")
    oracle_counts = counts(rows, "oracle_verdict")
    binary_matches = sum(bool(row["binary_label_match"]) for row in scorable)

    require(all_method_counts, {"unknown": 6, "verified-within-bounds": 4, "violated": 11}, "no-repair all-21 method counts")
    require(scored_method_counts, {"unknown": 6, "verified-within-bounds": 4, "violated": 10}, "no-repair scored-20 method counts")
    require(oracle_counts, {"invalid": 1, "safe": 4, "unsafe": 16}, "no-repair oracle counts")
    require(len(scorable), 20, "no-repair scored denominator")
    require(sum(bool(row["release_allowed"]) for row in unsafe), 0, "no-repair unsafe releases")
    require(summary["funnel"], {
        "admitted_including_duplicates": 30,
        "generated": 36,
        "mechanically_invalid": 6,
        "oracle_invalid_retained_unscored": 1,
        "oracle_valid_scored": 20,
        "repair_count": 0,
        "replacement_count": 0,
        "unique_admitted_included_here": 21,
    }, "no-repair summary funnel")
    require(
        summary.get("retained_private_evidence_status"),
        RETAINED_PRIVATE_STATUS,
        "no-repair retained private evidence boundary",
    )
    require(summary["recomputed_from_included_rows"]["method_counts_all_21"], all_method_counts, "no-repair summary all method counts")
    require(summary["recomputed_from_included_rows"]["method_counts_scored_20"], scored_method_counts, "no-repair summary scored method counts")
    require(summary["recomputed_from_included_rows"]["oracle_counts_all_21"], oracle_counts, "no-repair summary oracle counts")
    require(summary["recomputed_from_included_rows"]["unsafe_releases"], {"oracle_unsafe": 16, "released": 0}, "no-repair summary unsafe releases")
    require(summary["recomputed_from_included_rows"]["binary_label_matches"], binary_matches, "no-repair summary binary matches")
    require(summary["recomputed_from_included_rows"]["binary_label_denominator"], 20, "no-repair summary binary denominator")
    generation = validate_no_repair_generation_projection(rows, summary)

    return {
        "rows": 21,
        "scored": 20,
        "method_scored": scored_method_counts,
        "oracle": oracle_counts,
        "unsafe_release": "0/16",
        "binary_label_match": f"{binary_matches}/20",
        "public_generation_join": generation,
        "retained_private_evidence": RETAINED_PRIVATE_STATUS,
    }


def validate_q4() -> dict[str, Any]:
    data_path = RESULT_ROOT / "controlled-extension-q4" / "per-instance-results.jsonl"
    summary_path = RESULT_ROOT / "controlled-extension-q4" / "summary.json"
    corrections_path = RESULT_ROOT / "controlled-extension-q4" / "oracle-corrections.json"
    rows = load_jsonl(data_path)
    summary = load_json(summary_path)
    corrections = load_json(corrections_path)

    require(len(rows), 24, "Q4 fixed row count")
    expected_ids = {f"Q4-SLOT-{number:02d}" for number in range(1, 25)}
    observed_ids: set[str] = set()
    for index, row in enumerate(rows, start=1):
        require(set(row), Q4_FIELDS, f"Q4 row {index} field set")
        case_id = row["case_id"]
        if case_id in observed_ids:
            fail(f"duplicate Q4 slot: {case_id}")
        observed_ids.add(case_id)
        check_hash(row["source_sha256"], f"{case_id} source_sha256")
        if type(row["action_count_target_met"]) is not bool:
            fail(f"{case_id} action_count_target_met is not Boolean")
        if type(row["structural_target_met"]) is not bool:
            fail(f"{case_id} structural_target_met is not Boolean")
        require(row["release_allowed"], row["frozen_first_method_verdict"] == "verified-within-bounds", f"{case_id} release rule")
        require(row["method_run_unchanged"], True, f"{case_id} method unchanged")
        require(row["method_rerun_or_replacement"], False, f"{case_id} method rerun/replacement")
        require(row["corrected_oracle_process_exit"], 0, f"{case_id} corrected oracle process exit")
        qualified = row["corrected_oracle_process_status"] == "completed" and row["corrected_oracle_verdict"] in {"safe", "unsafe", "invalid"}
        scorable = qualified and row["corrected_oracle_verdict"] in {"safe", "unsafe"}
        require(row["corrected_oracle_qualified"], qualified, f"{case_id} corrected oracle qualified")
        require(row["corrected_oracle_scorable"], scorable, f"{case_id} corrected oracle scorable")
        require(row["corrected_oracle_coverage_complete"], qualified, f"{case_id} corrected oracle coverage")
        require(row["corrected_oracle_finite_trace_count"], 4 if qualified else 0, f"{case_id} corrected oracle trace count")

    require(observed_ids, expected_ids, "Q4 slot identity set")
    scored = [row for row in rows if row["corrected_oracle_scorable"]]
    qualified = [row for row in rows if row["corrected_oracle_qualified"]]
    unsafe = [row for row in rows if row["corrected_oracle_verdict"] == "unsafe"]
    all_method_counts = counts(rows, "frozen_first_method_verdict")
    scored_method_counts = counts(scored, "frozen_first_method_verdict")
    corrected_counts = Counter(row["corrected_oracle_verdict"] for row in rows)
    structural_target_count = sum(row["structural_target_met"] for row in rows)
    action_target_count = sum(row["action_count_target_met"] for row in rows)

    require(all_method_counts, {"invalid": 2, "unknown": 4, "verified-within-bounds": 13, "violated": 5}, "Q4 all-24 method counts")
    require(scored_method_counts, {"unknown": 4, "verified-within-bounds": 13, "violated": 4}, "Q4 scored-21 method counts")
    require(corrected_counts, Counter({"safe": 13, "unsafe": 8, "invalid": 2, None: 1}), "Q4 corrected oracle counts")
    require(len(qualified), 23, "Q4 qualified corrected-oracle processes")
    require(len(scored), 21, "Q4 safe/unsafe scored denominator")
    require(sum(bool(row["release_allowed"]) for row in unsafe), 0, "Q4 unsafe releases")
    require(structural_target_count, 24, "Q4 recomputed structural target count")
    require(action_target_count, 23, "Q4 recomputed action target count")

    slot17 = next(row for row in rows if row["case_id"] == "Q4-SLOT-17")
    slot18 = next(row for row in rows if row["case_id"] == "Q4-SLOT-18")
    require(
        (
            slot17["prior_oracle_verdict"],
            slot17["corrected_oracle_verdict"],
            slot17["corrected_oracle_process_status"],
            slot17["corrected_oracle_process_exit"],
            slot17["corrected_oracle_scorable"],
            slot17["correction_disposition"],
        ),
        ("safe", None, "rss_monitor_unavailable", 0, False, "retained_monitor_failure_unscored"),
        "Q4 slot 17 retained failure",
    )
    require(
        (slot18["prior_oracle_verdict"], slot18["corrected_oracle_verdict"], slot18["frozen_first_method_verdict"]),
        ("invalid", "unsafe", "violated"),
        "Q4 slot 18 correction",
    )

    reported = summary["recomputed_from_included_rows"]
    require(reported["method_counts_all_24"], all_method_counts, "Q4 summary all method counts")
    require(reported["method_counts_scored_21"], scored_method_counts, "Q4 summary scored method counts")
    require(reported["corrected_oracle_counts_all_24"], {"invalid": 2, "safe": 13, "unsafe": 8, "worker_failure_null": 1}, "Q4 summary corrected oracle counts")
    require(reported["corrected_oracle_qualified_processes"], 23, "Q4 summary qualified processes")
    require(reported["safe_or_unsafe_scored_rows"], 21, "Q4 summary scored rows")
    require(reported["unsafe_releases"], {"corrected_oracle_unsafe": 8, "released": 0}, "Q4 summary unsafe releases")
    require(reported["structural_target_met"], structural_target_count, "Q4 structural target")
    require(reported["source_action_target_met"], action_target_count, "Q4 action target")
    require(summary["first_method_reruns"], 0, "Q4 method reruns")
    require(summary["first_method_replacements"], 0, "Q4 method replacements")
    require(
        summary.get("retained_private_evidence_status"),
        RETAINED_PRIVATE_STATUS,
        "Q4 retained private evidence boundary",
    )
    require(
        summary["retained_failure"]["forensic_raw_worker_output_retained_in_private_source"],
        True,
        "Q4 slot 17 private forensic source retention",
    )
    require(
        summary["retained_failure"]["forensic_raw_worker_file_in_public_projection"],
        False,
        "Q4 slot 17 public forensic file boundary",
    )
    require(corrections["method_outputs"], {
        "fixed_first_method_rows": 24,
        "replacement": False,
        "rerun": False,
        "verdicts_unchanged": True,
    }, "Q4 correction method boundary")
    require(
        corrections.get("retained_private_evidence_status"),
        RETAINED_PRIVATE_STATUS,
        "Q4 correction retained private evidence boundary",
    )
    records = {record["case_id"]: record for record in corrections["records"]}
    require(set(records), {"Q4-SLOT-17", "Q4-SLOT-18"}, "Q4 correction record ids")
    require(records["Q4-SLOT-17"]["accepted_corrected_oracle_verdict"], None, "Q4 correction slot 17 accepted verdict")
    require(records["Q4-SLOT-17"]["forensic_raw_worker_verdict"], "unsafe", "Q4 correction slot 17 forensic verdict")
    require(records["Q4-SLOT-17"]["forensic_output_promoted_to_scored_result"], False, "Q4 correction slot 17 forensic promotion")
    require(records["Q4-SLOT-18"]["accepted_corrected_oracle_verdict"], "unsafe", "Q4 correction slot 18 accepted verdict")

    retained_failure = summary["retained_failure"]
    record17 = records["Q4-SLOT-17"]
    record18 = records["Q4-SLOT-18"]
    require(retained_failure["case_id"], slot17["case_id"], "Q4 slot 17 summary/result id")
    require(retained_failure["process_status"], slot17["corrected_oracle_process_status"], "Q4 slot 17 summary/result process")
    require(retained_failure["accepted_corrected_oracle_verdict"], slot17["corrected_oracle_verdict"], "Q4 slot 17 summary/result verdict")
    require(record17["prior_oracle_verdict"], slot17["prior_oracle_verdict"], "Q4 slot 17 correction/result prior verdict")
    require(record17["process_status"], slot17["corrected_oracle_process_status"], "Q4 slot 17 correction/result process")
    require(record17["frozen_first_method_verdict"], slot17["frozen_first_method_verdict"], "Q4 slot 17 correction/result method verdict")
    require(record18["prior_oracle_verdict"], slot18["prior_oracle_verdict"], "Q4 slot 18 correction/result prior verdict")
    require(record18["frozen_first_method_verdict"], slot18["frozen_first_method_verdict"], "Q4 slot 18 correction/result method verdict")
    require(record18["corrected_oracle_reason_codes"], slot18["corrected_oracle_reason_codes"], "Q4 slot 18 correction/result reasons")

    generation = validate_q4_generation_projection(rows, summary)

    return {
        "rows": 24,
        "qualified_corrected_oracle": 23,
        "scored": 21,
        "method_scored": scored_method_counts,
        "corrected_oracle": {"safe": 13, "unsafe": 8, "invalid": 2, "worker_failure_null": 1},
        "unsafe_release": "0/8",
        "retained_failure": "Q4-SLOT-17",
        "corrected_label": "Q4-SLOT-18 invalid->unsafe",
        "public_generation_join": generation,
        "retained_private_evidence": RETAINED_PRIVATE_STATUS,
    }


def main() -> None:
    checksum_files = verify_checksum_manifest()
    report = {
        "status": "PASS",
        "checksum_files": checksum_files,
        "no_repair": validate_no_repair(),
        "controlled_extension_q4": validate_q4(),
    }
    print(json.dumps(report, indent=2, sort_keys=True))


if __name__ == "__main__":
    main()
