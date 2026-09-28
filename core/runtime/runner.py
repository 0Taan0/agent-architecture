"""Run the user's project (main.py) in a subprocess with the project's interpreter.
Before: collect the run manifest. After: result, duration, error. Everything into
.architecture/runs.db, trace as JSONL in .architecture/traces/{run_id}.jsonl."""
from __future__ import annotations

import os
import subprocess
import time
from datetime import UTC, datetime

from pydantic import BaseModel

from core.models import ArchitectureModel, ProjectContext
from core.provenance import collect_run_manifest
from core.provenance.run_manifest import new_run_id

from .store import RunStore
from .trace import read_trace, summarize_trace, trace_path


class RunResult(BaseModel):
    run_id: str
    status: str
    duration_ms: float
    output: str | None = None
    error: str | None = None
    trace_path: str
    summary: dict


def run_project(project: ProjectContext, model: ArchitectureModel, input_json: str = "{}",
                *, entry: str = "main.py", timeout: int = 600,
                replay_of: str | None = None) -> RunResult:
    run_id = new_run_id(str(project.root))
    manifest = collect_run_manifest(project, model, run_id, input_json)
    tpath = trace_path(project.root, run_id)
    store = RunStore(project.root)
    started = datetime.now(UTC).replace(microsecond=0).isoformat().replace("+00:00", "Z")
    store.insert(run_id, started, manifest.model_dump(), project.rel(tpath))

    env = dict(os.environ, ARCHITECT_TRACE_PATH=str(tpath), ARCHITECT_RUN_ID=run_id,
               PYTHONPATH=str(project.root) + os.pathsep + os.environ.get("PYTHONPATH", ""))
    if replay_of:
        env["ARCHITECT_REPLAY_PATH"] = str(trace_path(project.root, replay_of))
    t0 = time.perf_counter()
    try:
        proc = subprocess.run([project.interpreter, entry, input_json], cwd=project.root,
                              capture_output=True, text=True, timeout=timeout, env=env,
                              check=False)
        status = "ok" if proc.returncode == 0 else "error"
        output, error = proc.stdout.strip() or None, (proc.stderr.strip() or None if proc.returncode else None)
    except subprocess.TimeoutExpired:
        status, output, error = "timeout", None, f"timed out after {timeout}s"
    duration = round((time.perf_counter() - t0) * 1000, 2)

    summary = summarize_trace(read_trace(tpath))
    manifest.model_served = summary.get("model_served", [])   # from the response, not request
    store.finish(run_id, status, duration, output, error, manifest.model_dump())
    return RunResult(run_id=run_id, status=status, duration_ms=duration, output=output,
                     error=error, trace_path=project.rel(tpath), summary=summary)
