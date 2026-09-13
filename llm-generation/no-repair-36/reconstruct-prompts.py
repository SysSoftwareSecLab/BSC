#!/usr/bin/env python3
"""Reconstruct the 12 prompt byte strings shared across the three model families."""
import json
from pathlib import Path

root = Path(__file__).resolve().parent
system_prompt = (root / "generation-prompt.txt").read_text(encoding="utf-8")
manual = (root / "public-authoring-manual.md").read_text(encoding="utf-8")
cards_doc = json.loads((root / "task-cards.json").read_text(encoding="utf-8"))
cards = cards_doc.get("cards", cards_doc)
for card in cards:
    card_id = card.get("task_card_id", card.get("card_id"))
    payload = {"public_authoring_manual": manual, "task_card": card}
    prompt = system_prompt.rstrip() + "\n\nAUTHORING_INPUT_JSON\n" + json.dumps(payload, sort_keys=True, separators=(",", ":"), ensure_ascii=False) + "\n"
    target = root / "reconstructed-prompts" / f"{card_id}.txt"
    target.parent.mkdir(exist_ok=True)
    target.write_text(prompt, encoding="utf-8")
