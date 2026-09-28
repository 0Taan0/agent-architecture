"""Subprocess introspection. Foreign code is NEVER imported into the server process.

We inject a small standalone script into the target interpreter (the project's venv if
found), run it with cwd = project root, and read one JSON object from stdout.
"""
from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

from pydantic import BaseModel, Field

from core.models import ProjectContext

SCRIPT = Path(__file__).parent / "adapters" / "langgraph" / "_introspect_script.py"
DEFAULT_TIMEOUT = 60


class IntrospectionError(RuntimeError):
    pass


class IntrospectionResult(BaseModel):
    graphs: list[dict] = Field(default_factory=list)
    errors: list[str] = Field(default_factory=list)
    framework_versions: dict[str, str] = Field(default_factory=dict)


def run_introspection(project: ProjectContext, modules: list[str],
                      timeout: int = DEFAULT_TIMEOUT) -> IntrospectionResult:
    if not modules:
        return IntrospectionResult()
    cmd = [project.interpreter, str(SCRIPT), str(project.root), *modules]
    try:
        proc = subprocess.run(cmd, cwd=project.root, capture_output=True, text=True,
                              timeout=timeout, check=False)
    except subprocess.TimeoutExpired as exc:
        raise IntrospectionError(f"introspection timed out after {timeout}s") from exc
    except OSError as exc:
        raise IntrospectionError(f"cannot start interpreter {project.interpreter}: {exc}") from exc
    payload = _last_json_line(proc.stdout)
    if payload is None:
        tail = (proc.stderr or proc.stdout or "").strip().splitlines()[-5:]
        raise IntrospectionError("introspection produced no result: " + " | ".join(tail))
    return IntrospectionResult.model_validate(payload)


def _last_json_line(stdout: str) -> dict | None:
    for line in reversed((stdout or "").splitlines()):
        line = line.strip()
        if line.startswith("{") and line.endswith("}"):
            try:
                return json.loads(line)
            except json.JSONDecodeError:
                continue
    return None


if __name__ == "__main__":  # manual smoke test
    p = ProjectContext.open(sys.argv[1])
    print(run_introspection(p, sys.argv[2:]).model_dump_json(indent=2))
