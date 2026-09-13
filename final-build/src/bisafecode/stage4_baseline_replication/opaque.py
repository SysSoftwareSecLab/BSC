"""Metadata-blind case identifiers and presentation order."""

from __future__ import annotations

import hashlib


OPAQUE_DOMAIN = "EXP-S4-007:opaque-case-token:v1"


def opaque_case_id(source_sha256: str) -> str:
    """Return a token derived from source identity, never from schedule/label."""

    if len(source_sha256) != 64 or any(char not in "0123456789abcdef" for char in source_sha256):
        raise ValueError("source_sha256 must be lowercase SHA-256")
    digest = hashlib.sha256(f"{OPAQUE_DOMAIN}:{source_sha256}".encode("utf-8")).hexdigest()
    return f"CASE-{digest[:20]}"


def presentation_key(source_sha256: str) -> str:
    """Freeze an order unrelated to original program/family numbering."""

    return hashlib.sha256(
        f"EXP-S4-007:presentation-order:v1:{opaque_case_id(source_sha256)}".encode("utf-8")
    ).hexdigest()

