"""Cross-file validation for the S4-008 freeze package."""

from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Any, Mapping

from . import CONTRACT_RELATIVE, EXPERIMENT_ID, FREEZE_STATUS


JSON_CONTRACTS = (
    "SCIENTIFIC_PROTOCOL.json",
    "ADMISSION_LEAKAGE_AND_STOP_POLICY.json",
    "LLM_GENERATION_BINDING.json",
    "METHOD_ORACLE_AND_ANALYSIS_PLAN.json",
    "APPROVAL_TEMPLATE.json",
    "generator_output.schema.json",
    "HUMAN_TASK_CARDS.json",
    "HUMAN_SUBMISSION_METADATA_TEMPLATE.json",
)
TEXT_CONTRACTS = (
    "PUBLIC_AUTHORING_MANUAL.md",
    "LLM_GENERATOR_PROMPT.txt",
    "HUMAN_PARTICIPANT_INSTRUCTIONS.md",
    "ANONYMOUS_HUMAN_COLLECTION_GUIDE.md",
)
FORBIDDEN_LABEL_KEYS = {
    "expected_verdict", "expected_label", "target_class", "target_verdict",
    "oracle_label", "verifier_verdict", "ground_truth", "intended_oracle_class",
}
CONTROLLED_ID_RE = re.compile(r"EXP-S4-002-P\d{3}")


def contract_root(root: Path) -> Path:
    return root / CONTRACT_RELATIVE


def load_json(root: Path, name: str) -> Mapping[str, Any]:
    value = json.loads((contract_root(root) / name).read_text(encoding="utf-8"))
    if not isinstance(value, dict):
        raise ValueError(f"{name} must contain a JSON object")
    return value


def _forbidden_paths(value: Any, prefix: str = "$") -> list[str]:
    paths: list[str] = []
    if isinstance(value, dict):
        for key, item in value.items():
            path = f"{prefix}.{key}"
            if str(key).lower() in FORBIDDEN_LABEL_KEYS:
                paths.append(path)
            paths.extend(_forbidden_paths(item, path))
    elif isinstance(value, list):
        for index, item in enumerate(value):
            paths.extend(_forbidden_paths(item, f"{prefix}[{index}]"))
    return paths


def validate_freeze_package(root: Path) -> list[str]:
    errors: list[str] = []
    directory = contract_root(root)
    for name in JSON_CONTRACTS + TEXT_CONTRACTS:
        if not (directory / name).is_file():
            errors.append(f"missing_contract:{name}")
    if errors:
        return errors

    documents: dict[str, Mapping[str, Any]] = {}
    for name in JSON_CONTRACTS:
        try:
            documents[name] = load_json(root, name)
        except (OSError, ValueError, json.JSONDecodeError) as error:
            errors.append(f"invalid_json:{name}:{error}")
    if errors:
        return errors

    protocol = documents["SCIENTIFIC_PROTOCOL.json"]
    if protocol.get("experiment_id") != EXPERIMENT_ID:
        errors.append("experiment_id_mismatch")
    if protocol.get("evidence_status") != FREEZE_STATUS:
        errors.append("protocol_not_freeze_only")
    population = protocol.get("population", {})
    if population.get("attempts_total") != 36:
        errors.append("attempt_total_not_36")
    if population.get("natural_llm_attempts") != 18:
        errors.append("llm_attempt_total_not_18")
    if population.get("independent_human_attempts") != 18:
        errors.append("human_attempt_total_not_18")

    cards_document = documents["HUMAN_TASK_CARDS.json"]
    cards = cards_document.get("cards", [])
    card_ids = [card.get("card_id") for card in cards if isinstance(card, dict)]
    expected = [f"TASK-{index:02d}" for index in range(1, 19)]
    if card_ids != expected:
        errors.append("task_cards_are_not_exact_ordered_18")
    assignments = cards_document.get("human_assignments", {})
    if sorted(assignments) != ["AUTHOR-A", "AUTHOR-B", "AUTHOR-C"]:
        errors.append("human_author_ids_mismatch")
    assigned = [item for values in assignments.values() for item in values]
    if sorted(assigned) != expected or any(len(values) != 6 for values in assignments.values()):
        errors.append("human_assignments_not_partitioned_3x6")
    if cards_document.get("llm_assignment") != expected:
        errors.append("llm_assignment_not_exact_18")

    binding = documents["LLM_GENERATION_BINDING.json"]
    expected_binding = {
        "model_selector": "gpt-5.6-sol",
        "reasoning_effort": "high",
        "codex_cli_version": "codex-cli 0.148.0-alpha.9",
        "attempts": 18,
        "fresh_process_per_attempt": True,
        "resume_allowed": False,
    }
    for key, value in expected_binding.items():
        if binding.get(key) != value:
            errors.append(f"llm_binding_mismatch:{key}")

    for name, document in documents.items():
        forbidden = _forbidden_paths(document)
        if forbidden:
            errors.append(f"forbidden_label_keys:{name}:{','.join(forbidden)}")
    for name in JSON_CONTRACTS + TEXT_CONTRACTS:
        text = (directory / name).read_text(encoding="utf-8")
        if CONTROLLED_ID_RE.search(text):
            errors.append(f"controlled_program_id_leak:{name}")
    return errors
