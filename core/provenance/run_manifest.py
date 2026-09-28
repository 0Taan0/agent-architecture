"""Stage 1 - Provenance (guaranteed). Every run records what influenced it (Spec 13.2).
Collected BEFORE the run starts; model_served is filled from the trace afterwards."""
from __future__ import annotations

import hashlib
from datetime import UTC, datetime
from pathlib import Path

from pydantic import BaseModel, Field

from core import PLATFORM_VERSION
from core.models import ArchitectureModel, ProjectContext
from core.ownership import architecture_hash, content_hash, file_hash
from core.vcs import git_state

LOCKFILES = ("uv.lock", "poetry.lock", "requirements.txt", "requirements.lock", "Pipfile.lock")
PROMPT_GLOBS = ("prompts/**/*.txt", "prompts/**/*.md", "prompts/**/*.jinja", "prompts/**/*.j2")


class RunManifest(BaseModel):
    run_id: str
    architecture_hash: str
    code_commit: str | None = None
    code_dirty: bool = False
    prompt_versions: dict[str, str] = Field(default_factory=dict)
    model_requested: list[str] = Field(default_factory=list)
    model_served: list[str] = Field(default_factory=list)   # from the API response, not request
    model_parameters: dict[str, dict] = Field(default_factory=dict)
    tool_versions: dict[str, str] = Field(default_factory=dict)
    dependency_lock: str | None = None
    input_hash: str
    platform_version: str = PLATFORM_VERSION
    timestamp: str


def collect_run_manifest(project: ProjectContext, model: ArchitectureModel, run_id: str,
                         input_text: str) -> RunManifest:
    git = git_state(project.root)
    lock = next((project.root / f for f in LOCKFILES if (project.root / f).exists()), None)
    prompts: dict[str, str] = {}
    for pattern in PROMPT_GLOBS:
        for p in sorted(project.root.glob(pattern)):
            prompts[project.rel(p)] = file_hash(p)
    tools: dict[str, str] = {}
    tools_dir = project.root / "tools"
    if tools_dir.is_dir():
        for p in sorted(tools_dir.rglob("*.py")):
            tools[project.rel(p)] = file_hash(p)
    return RunManifest(
        run_id=run_id,
        architecture_hash=architecture_hash(model),
        code_commit=git.commit, code_dirty=git.dirty,
        prompt_versions=prompts,
        model_requested=sorted({a.model.model for a in model.agents if a.model and a.model.model}),
        model_parameters={a.name: a.model.model_dump(exclude_none=True)
                          for a in model.agents if a.model},
        tool_versions=tools,
        dependency_lock=file_hash(lock) if lock else None,
        input_hash=content_hash(input_text),
        timestamp=datetime.now(UTC).replace(microsecond=0).isoformat()
        .replace("+00:00", "Z"),
    )


def new_run_id(seed: str | None = None) -> str:
    raw = (seed or "") + datetime.now(UTC).isoformat()
    return "run_" + hashlib.sha256(raw.encode()).hexdigest()[:10]


__all__ = ["Path", "RunManifest", "collect_run_manifest", "new_run_id"]
