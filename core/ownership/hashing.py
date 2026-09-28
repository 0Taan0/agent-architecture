"""Canonical serialisation + SHA256.

Canonical = sorted keys, fixed formatting, no timestamps, no layout. An unstable hash
makes drift detection look broken and you search in the wrong place."""
from __future__ import annotations

import hashlib
import json
from pathlib import Path

from core.models import ArchitectureModel

# fields that are not part of the architectural meaning
_EXCLUDE = {"unresolved"}


def canonical_json(data) -> str:
    return json.dumps(data, sort_keys=True, separators=(",", ":"), ensure_ascii=True)


def content_hash(text: str | bytes) -> str:
    raw = text.encode("utf-8") if isinstance(text, str) else text
    return "sha256:" + hashlib.sha256(raw).hexdigest()


def file_hash(path: Path) -> str:
    return content_hash(Path(path).read_bytes())


def architecture_hash(model: ArchitectureModel) -> str:
    """Covers agents, schemas, connections, model config. Not canvas positions,
    not unresolved items, not source_adapter (who found it does not change what it is)."""
    data = model.sorted().model_dump(mode="json", exclude=_EXCLUDE)
    for coll in ("agents", "schemas", "connections"):
        for item in data.get(coll, []):
            item.pop("source_adapter", None)
            item.pop("description", None)
    return content_hash(canonical_json(data))
