"""Git is the safety net (Spec 7.13). Twenty lines, no own versioning mechanism."""
from __future__ import annotations

import subprocess
from pathlib import Path

from pydantic import BaseModel


class GitState(BaseModel):
    is_repo: bool = False
    commit: str | None = None
    dirty: bool = False
    status: list[str] = []


def _git(root: Path, *args: str) -> str | None:
    try:
        out = subprocess.run(["git", *args], cwd=root, capture_output=True, text=True,
                             timeout=15, check=False)
    except (OSError, subprocess.TimeoutExpired):
        return None
    return out.stdout.strip() if out.returncode == 0 else None


def git_state(root: Path) -> GitState:
    if _git(root, "rev-parse", "--is-inside-work-tree") != "true":
        return GitState()
    status = _git(root, "status", "--porcelain") or ""
    lines = [ln for ln in status.splitlines() if ln.strip()]
    return GitState(is_repo=True, commit=_git(root, "rev-parse", "HEAD"), dirty=bool(lines),
                    status=lines)


def is_dirty(root: Path) -> bool:
    return git_state(root).dirty
