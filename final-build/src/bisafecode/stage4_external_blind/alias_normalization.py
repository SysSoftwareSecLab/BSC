"""Deterministic R2 normalization for public trajectory documentation aliases.

This module performs no parsing, verification, baseline, oracle, or label work.
It changes only an exact string literal used as the second positional argument
of a direct ``move`` call.  Every other source byte is retained.
"""

from __future__ import annotations

import ast
import hashlib
import json
from typing import Any, Mapping


TRAJECTORY_ALIAS_TO_SHA256: Mapping[str, str] = {
    "left_approach": "d2061067f4b66316e861e216e433941870034c1ef0d57baad7f533a3b9d5ca26",
    "left_retreat": "952b79dbc0b450154d6910502682bcb1bb62373808550d35da80c313aba41db6",
    "right_approach": "3e6910a45a223961ab95f4f5e8a63ac43f95c8270bb698234abaf687963ab609",
    "right_retreat": "1d58d45f58c932f071ca9641505e7a0a5abb03f7ed0d96b9aad5a93383fd32ea",
}


def _sha256(value: bytes) -> str:
    return hashlib.sha256(value).hexdigest()


def _line_starts(source_bytes: bytes) -> list[int]:
    starts = [0]
    for index, value in enumerate(source_bytes):
        if value == 10:
            starts.append(index + 1)
    return starts


def normalize_trajectory_aliases(source: str) -> Mapping[str, Any]:
    """Return a byte-preserving normalization record for one UTF-8 source."""

    source_bytes = source.encode("utf-8")
    module = ast.parse(source, filename="external_blind_source.py", mode="exec")
    starts = _line_starts(source_bytes)
    replacements: list[dict[str, Any]] = []
    spans: list[tuple[int, int, bytes, str, str, int, int]] = []
    for node in ast.walk(module):
        if not isinstance(node, ast.Call):
            continue
        if not isinstance(node.func, ast.Name) or node.func.id != "move":
            continue
        if len(node.args) < 2:
            continue
        literal = node.args[1]
        if not isinstance(literal, ast.Constant) or not isinstance(literal.value, str):
            continue
        alias = literal.value
        if alias not in TRAJECTORY_ALIAS_TO_SHA256:
            continue
        if None in (literal.end_lineno, literal.end_col_offset):
            raise ValueError("Python AST source spans are unavailable")
        start = starts[literal.lineno - 1] + literal.col_offset
        end = starts[int(literal.end_lineno) - 1] + int(literal.end_col_offset)
        trajectory_sha = TRAJECTORY_ALIAS_TO_SHA256[alias]
        replacement = json.dumps(trajectory_sha).encode("ascii")
        spans.append(
            (
                start,
                end,
                replacement,
                alias,
                trajectory_sha,
                literal.lineno,
                literal.col_offset,
            )
        )
    normalized = source_bytes
    previous_start = len(source_bytes) + 1
    for start, end, replacement, alias, trajectory_sha, line, column in sorted(
        spans, reverse=True
    ):
        if end > previous_start or not (0 <= start < end <= len(source_bytes)):
            raise ValueError("overlapping or invalid alias replacement span")
        original_literal = normalized[start:end]
        normalized = normalized[:start] + replacement + normalized[end:]
        previous_start = start
        replacements.append(
            {
                "alias": alias,
                "trajectory_sha256": trajectory_sha,
                "line": line,
                "column": column,
                "original_literal_utf8": original_literal.decode("utf-8"),
                "replacement_literal_utf8": replacement.decode("ascii"),
            }
        )
    replacements.sort(key=lambda item: (int(item["line"]), int(item["column"])))
    normalized_source = normalized.decode("utf-8")
    return {
        "schema": "bisafecode.stage4.external-blind.trajectory-alias-normalization/v1",
        "source_sha256": _sha256(source_bytes),
        "normalized_source_sha256": _sha256(normalized),
        "changed": bool(replacements),
        "replacement_count": len(replacements),
        "replacements": replacements,
        "normalized_source": normalized_source,
    }
