""".architecture/manifest.yaml - what the platform generated last (Spec 7.4).
Ownership is stored here, never derived from file names alone."""
from __future__ import annotations

from datetime import UTC, datetime
from enum import Enum
from pathlib import Path

import yaml
from pydantic import BaseModel, Field

MANIFEST_FILE = ".architecture/manifest.yaml"


class Ownership(str, Enum):
    GENERATED = "generated"
    USER = "user"
    MIXED = "mixed"
    ORPHANED = "orphaned"


class FileEntry(BaseModel):
    ownership: Ownership
    node_id: str | None = None
    symbol: str | None = None
    content_hash: str | None = None       # generated: hash at generation time
    last_seen_hash: str | None = None     # user: hash last observed by the platform
    generator_version: str | None = None
    template: str | None = None
    generated_at: str | None = None
    created_by: str | None = None


class Manifest(BaseModel):
    version: str = "1"
    architecture_hash: str | None = None
    files: dict[str, FileEntry] = Field(default_factory=dict)

    def generated_files(self) -> dict[str, FileEntry]:
        return {p: e for p, e in self.files.items() if e.ownership == Ownership.GENERATED}


def now_iso() -> str:
    return datetime.now(UTC).replace(microsecond=0).isoformat().replace("+00:00", "Z")


def load_manifest(root: Path) -> Manifest:
    path = Path(root) / MANIFEST_FILE
    if not path.exists():
        return Manifest()
    return Manifest.model_validate(yaml.safe_load(path.read_text(encoding="utf-8")) or {})


def save_manifest(root: Path, manifest: Manifest) -> Path:
    path = Path(root) / MANIFEST_FILE
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(yaml.safe_dump(manifest.model_dump(mode="json", exclude_none=True),
                                   sort_keys=True), encoding="utf-8")
    return path
