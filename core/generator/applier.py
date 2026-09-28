"""Apply phase. Writes ONLY what the plan marked create/update, after confirmation.
Checks git status first (Spec 7.13) and refuses on a dirty tree unless told otherwise."""
from __future__ import annotations

from pydantic import BaseModel, Field

from core import PLATFORM_VERSION
from core.models import ProjectContext
from core.ownership import (
    FileEntry,
    Manifest,
    Ownership,
    content_hash,
    save_manifest,
)
from core.ownership.manifest import now_iso
from core.vcs import git_state

from .planner import OperationKind, Plan


class ApplyResult(BaseModel):
    written: list[str] = Field(default_factory=list)
    skipped: list[str] = Field(default_factory=list)
    conflicts: list[str] = Field(default_factory=list)
    refused: str | None = None


def apply_plan(project: ProjectContext, plan: Plan, manifest: Manifest, *,
               allow_dirty: bool = False) -> ApplyResult:
    result = ApplyResult()
    state = git_state(project.root)
    if state.is_repo and state.dirty and not allow_dirty:
        result.refused = ("git working tree is not clean - commit or stash first "
                          "(or apply with allow_dirty=True)")
        return result
    for op in plan.operations:
        if op.kind == OperationKind.CONFLICT:
            result.conflicts.append(op.path)
            continue
        if op.kind == OperationKind.SKIP:
            result.skipped.append(op.path)
            continue
        path = project.abs(op.path)
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(op.new_content or "", encoding="utf-8")
        result.written.append(op.path)
        h = content_hash(op.new_content or "")
        if op.ownership == Ownership.GENERATED:
            manifest.files[op.path] = FileEntry(
                ownership=Ownership.GENERATED, node_id=op.node_id, symbol=op.symbol,
                content_hash=h, generator_version=PLATFORM_VERSION, template=op.template,
                generated_at=now_iso())
        else:
            manifest.files[op.path] = FileEntry(
                ownership=Ownership.USER, node_id=op.node_id, symbol=op.symbol,
                created_by="platform", last_seen_hash=h)
    manifest.architecture_hash = plan.architecture_hash
    save_manifest(project.root, manifest)
    return result
