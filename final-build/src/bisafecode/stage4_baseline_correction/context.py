"""Hash-verified construction of the full GPT-5.5 judgment context."""

from __future__ import annotations

import hashlib
import json
from pathlib import Path
from typing import Any, Mapping

from . import CONTRACT_RELATIVE


def repository_root() -> Path:
    return Path(__file__).resolve().parents[3]


def sha256_file(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def load_context_manifest(root: Path | None = None) -> Mapping[str, Any]:
    root = (root or repository_root()).resolve()
    path = root / CONTRACT_RELATIVE / "CONTEXT_MANIFEST.json"
    value = json.loads(path.read_text(encoding="utf-8"))
    if value.get("schema") != "bisafecode.stage4.baseline-correction.context-manifest/v1":
        raise ValueError("context manifest schema mismatch")
    documents = value.get("documents")
    if not isinstance(documents, list) or not documents:
        raise ValueError("context manifest must list documents")
    paths = [item.get("path") for item in documents if isinstance(item, dict)]
    if len(paths) != len(documents) or len(paths) != len(set(paths)):
        raise ValueError("context document paths must be unique strings")
    for item in documents:
        path = root / str(item["path"])
        if not path.is_file():
            raise ValueError(f"context document missing: {item['path']}")
        if sha256_file(path) != item.get("sha256"):
            raise ValueError(f"context document hash mismatch: {item['path']}")
    return value


def build_full_context(root: Path | None = None) -> Mapping[str, Any]:
    """Embed every frozen context document by value; paths are not model inputs."""

    root = (root or repository_root()).resolve()
    manifest = load_context_manifest(root)

    def scientific_view(value: Any) -> Any:
        """Remove experiment-lifecycle markers that are not model semantics."""

        if isinstance(value, dict):
            return {
                key: scientific_view(item)
                for key, item in value.items()
                if key != "evidence_status"
            }
        if isinstance(value, list):
            return [scientific_view(item) for item in value]
        return value

    documents = {
        str(item["path"]): scientific_view(
            json.loads(
                (root / str(item["path"])).read_text(encoding="utf-8")
            )
        )
        for item in manifest["documents"]
    }
    return {
        "schema": "bisafecode.stage4.baseline-correction.embedded-context/v1",
        "manifest_sha256": sha256_file(root / CONTRACT_RELATIVE / "CONTEXT_MANIFEST.json"),
        "context_documents": documents,
    }
