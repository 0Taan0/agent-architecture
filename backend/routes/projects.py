from __future__ import annotations

from pathlib import Path

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel

from backend.registry import ProjectRegistry
from core.drift import DriftReport, detect_drift
from core.generator import ApplyResult, Plan, apply_plan, plan_generation
from core.impact import ImpactReport, impact_of_schema
from core.inspector import InspectionReport, inspect_project
from core.inspector.adapters import ALL_ADAPTERS
from core.inspector.adapters.project_local import INSPECT_FILE, SCAFFOLD
from core.models import (
    ArchitectureModel,
    LayoutModel,
    ProjectContext,
    load_architecture,
    load_layout,
    save_architecture,
    save_layout,
)
from core.ownership import Manifest, load_manifest
from core.runtime import RunResult, RunStore, read_trace, run_project, summarize_trace
from core.vcs import GitState, git_state

router = APIRouter(tags=["projects"])
_registry: ProjectRegistry | None = None


def registry() -> ProjectRegistry:
    global _registry
    if _registry is None:
        _registry = ProjectRegistry()
    return _registry


def project_ctx(project_id: str, reg: ProjectRegistry = Depends(registry)) -> ProjectContext:
    rec = reg.get(project_id)
    if rec is None:
        raise HTTPException(404, "unknown project")
    try:
        return ProjectContext.open(rec["path"], rec.get("python"))
    except FileNotFoundError as exc:
        raise HTTPException(410, str(exc)) from exc


class RegisterProject(BaseModel):
    path: str
    python: str | None = None


class ArchitectureResponse(BaseModel):
    stored: ArchitectureModel | None
    layout: LayoutModel
    manifest: Manifest
    git: GitState


class FileResponse(BaseModel):
    path: str
    content: str


class ApplyRequest(BaseModel):
    allow_dirty: bool = False


class RunRequest(BaseModel):
    input: str = "{}"
    replay_of: str | None = None


@router.post("/projects")
def register_project(body: RegisterProject, reg: ProjectRegistry = Depends(registry)) -> dict:
    p = Path(body.path).expanduser()
    if not p.is_dir():
        raise HTTPException(400, f"not a directory: {p}")
    return reg.register(p, body.python)


@router.get("/projects")
def list_projects(reg: ProjectRegistry = Depends(registry)) -> list[dict]:
    return reg.list()


@router.get("/projects/{project_id}/architecture", response_model=ArchitectureResponse)
def get_architecture(project: ProjectContext = Depends(project_ctx)) -> ArchitectureResponse:
    return ArchitectureResponse(stored=load_architecture(project.root),
                                layout=load_layout(project.root),
                                manifest=load_manifest(project.root), git=git_state(project.root))


@router.put("/projects/{project_id}/architecture")
def put_architecture(model: ArchitectureModel,
                     project: ProjectContext = Depends(project_ctx)) -> dict:
    return {"path": str(save_architecture(project.root, model))}


@router.put("/projects/{project_id}/layout")
def put_layout(layout: LayoutModel, project: ProjectContext = Depends(project_ctx)) -> dict:
    return {"path": str(save_layout(project.root, layout))}


@router.post("/projects/{project_id}/inspect", response_model=InspectionReport)
def inspect(project: ProjectContext = Depends(project_ctx)) -> InspectionReport:
    return inspect_project(project)


@router.get("/projects/{project_id}/adapters")
def adapters(project: ProjectContext = Depends(project_ctx)) -> list[dict]:
    return [{"name": a.name, "confidence": a.detect(project).value,
             "supported_versions": a.supported_versions} for a in (cls() for cls in ALL_ADAPTERS)]


@router.post("/projects/{project_id}/inspect-scaffold")
def inspect_scaffold(project: ProjectContext = Depends(project_ctx)) -> dict:
    """Proposes .architecture/inspect.py - never overwrites an existing one (user-owned)."""
    target = project.root / INSPECT_FILE
    if target.exists():
        raise HTTPException(409, f"{INSPECT_FILE} already exists and is user-owned")
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(SCAFFOLD, encoding="utf-8")
    return {"path": INSPECT_FILE}


@router.get("/projects/{project_id}/file", response_model=FileResponse)
def get_file(path: str = Query(...), project: ProjectContext = Depends(project_ctx)) -> FileResponse:
    target = (project.root / path).resolve()
    if not project.is_inside(target):             # path traversal guard
        raise HTTPException(403, "path outside project root")
    if not target.is_file():
        raise HTTPException(404, "file not found")
    return FileResponse(path=project.rel(target), content=target.read_text(encoding="utf-8",
                                                                            errors="replace"))


@router.get("/projects/{project_id}/drift", response_model=DriftReport)
def drift(project: ProjectContext = Depends(project_ctx)) -> DriftReport:
    current = inspect_project(project).model
    return detect_drift(project, load_architecture(project.root), current,
                        load_manifest(project.root))


@router.post("/projects/{project_id}/generate/plan", response_model=Plan)
def generate_plan(project: ProjectContext = Depends(project_ctx)) -> Plan:
    model = load_architecture(project.root)
    if model is None:
        raise HTTPException(400, "no architecture.yaml in project")
    return plan_generation(project, model, load_manifest(project.root))


@router.post("/projects/{project_id}/generate/apply", response_model=ApplyResult)
def generate_apply(body: ApplyRequest, project: ProjectContext = Depends(project_ctx)) -> ApplyResult:
    model = load_architecture(project.root)
    if model is None:
        raise HTTPException(400, "no architecture.yaml in project")
    plan = plan_generation(project, model, load_manifest(project.root))
    return apply_plan(project, plan, load_manifest(project.root), allow_dirty=body.allow_dirty)


@router.post("/projects/{project_id}/runs", response_model=RunResult)
def start_run(body: RunRequest, project: ProjectContext = Depends(project_ctx)) -> RunResult:
    model = load_architecture(project.root)
    if model is None:
        raise HTTPException(400, "no architecture.yaml in project")
    return run_project(project, model, body.input, replay_of=body.replay_of)


@router.get("/projects/{project_id}/runs")
def list_runs(project: ProjectContext = Depends(project_ctx)) -> list[dict]:
    return RunStore(project.root).list()


@router.get("/projects/{project_id}/runs/{run_id}")
def get_run(run_id: str, project: ProjectContext = Depends(project_ctx)) -> dict:
    run = RunStore(project.root).get(run_id)
    if run is None:
        raise HTTPException(404, "unknown run")
    events = read_trace(project.abs(run["trace_path"])) if run.get("trace_path") else []
    return {**run, "summary": summarize_trace(events), "events": [e.model_dump() for e in events]}


@router.get("/projects/{project_id}/impact", response_model=ImpactReport)
def impact(schema_id: str, fields: str | None = None, previous_hash: str | None = None,
           project: ProjectContext = Depends(project_ctx)) -> ImpactReport:
    model = load_architecture(project.root) or inspect_project(project).model
    try:
        return impact_of_schema(project, model, schema_id,
                                fields=[f for f in (fields or "").split(",") if f] or None,
                                previous_architecture_hash=previous_hash)
    except KeyError as exc:
        raise HTTPException(404, f"unknown schema {exc}") from exc
