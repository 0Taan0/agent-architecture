"""Slice 1 + 2 end to end: design -> generate -> run -> trace -> drift -> impact -> replay."""
import json
import subprocess

from core.drift import Severity, compare, detect_drift
from core.generator import OperationKind, apply_plan, plan_generation
from core.impact import impact_of_schema
from core.inspector import inspect_project
from core.inspector.adapters import DeclarationsAdapter
from core.models import (
    AgentNode,
    ArchitectureModel,
    Connection,
    ModelConfig,
    ProjectContext,
    Provenance,
    SchemaDef,
    SchemaField,
    connection_id,
    load_architecture,
    save_architecture,
)
from core.ownership import Ownership, load_manifest
from core.runtime import ReplayClient, RunStore, run_project
from core.runtime.trace import trace_path


def design() -> ArchitectureModel:
    q = SchemaDef(id="schema_q", name="Query", fields=[SchemaField(name="text", type="str")],
                  provenance=Provenance.DECLARED)
    r = SchemaDef(id="schema_r", name="ResearchResult", provenance=Provenance.DECLARED,
                  fields=[SchemaField(name="title", type="str"),
                          SchemaField(name="confidence", type="float")])
    a = AgentNode(id="agent_res", name="Researcher", input_schema="schema_q",
                  output_schema="schema_r", tools=["web_search"],
                  model=ModelConfig(provider="anthropic", model="claude-sonnet-4-6",
                                    temperature=0.2), provenance=Provenance.DECLARED)
    b = AgentNode(id="agent_wri", name="Writer", input_schema="schema_r",
                  provenance=Provenance.DECLARED)
    return ArchitectureModel(name="demo", agents=[a, b], schemas=[q, r], connections=[
        Connection(id=connection_id("agent_res", "agent_wri"), source="agent_res",
                   target="agent_wri", provenance=Provenance.DECLARED)])


def _git(root, *args):
    subprocess.run(["git", *args], cwd=root, check=True, capture_output=True)


def test_full_circle(tmp_path):
    project = ProjectContext.open(tmp_path)
    model = design()
    save_architecture(project.root, model)
    _git(tmp_path, "init", "-q")
    _git(tmp_path, "-c", "user.email=t@t", "-c", "user.name=t", "add", ".")
    _git(tmp_path, "-c", "user.email=t@t", "-c", "user.name=t", "commit", "-qm", "design")

    # --- plan: everything is create, nothing written yet
    plan = plan_generation(project, model, load_manifest(project.root))
    assert all(op.kind == OperationKind.CREATE for op in plan.operations)
    assert not (tmp_path / "main.py").exists()

    # --- apply
    res = apply_plan(project, plan, load_manifest(project.root))
    assert res.refused is None and "main.py" in res.written
    manifest = load_manifest(project.root)
    assert manifest.files["agents/_generated/researcher_base.py"].ownership == Ownership.GENERATED
    assert manifest.files["agents/researcher.py"].ownership == Ownership.USER
    assert manifest.architecture_hash == plan.architecture_hash

    # --- second plan on unchanged architecture: nothing to do, user files untouched
    plan2 = plan_generation(project, model, manifest)
    assert {op.kind for op in plan2.operations} == {OperationKind.SKIP}

    # --- generated code round-trips through the inspector with the SAME ids
    inspected = inspect_project(project, [DeclarationsAdapter()]).model
    assert {a.id for a in inspected.agents} == {"agent_res", "agent_wri"}
    assert compare(model, inspected).breaking == []

    # --- user implements the agents (user-owned files, platform never touches them)
    (tmp_path / "agents/researcher.py").write_text(
        "from agents._generated.researcher_base import ResearcherAgentBase\n"
        "class ResearcherAgent(ResearcherAgentBase):\n"
        "    def run(self, task):\n"
        "        self.record_llm({'model': 'claude-sonnet-4-6', 'prompt': task},\n"
        "                        {'model': 'claude-sonnet-4-6-20260115', 'text': 'x',\n"
        "                         'usage': {'input_tokens': 5, 'output_tokens': 2}})\n"
        "        return {'title': task.get('text', ''), 'confidence': 0.9}\n")
    (tmp_path / "agents/writer.py").write_text(
        "from agents._generated.writer_base import WriterAgentBase\n"
        "class WriterAgent(WriterAgentBase):\n"
        "    def run(self, task):\n"
        "        return {'article': task['title'].upper(), 'score': task['confidence']}\n")

    # --- run: manifest + trace + runs.db
    run = run_project(project, model, json.dumps({"text": "hello"}))
    assert run.status == "ok", run.error
    assert json.loads(run.output) == {"article": "HELLO", "score": 0.9}
    assert run.summary["order"] == ["Researcher", "Writer"]
    assert run.summary["agents"]["Researcher"]["input_tokens"] == 5
    stored = RunStore(project.root).get(run.run_id)
    assert stored["manifest"]["architecture_hash"] == plan.architecture_hash
    assert stored["manifest"]["model_served"] == ["claude-sonnet-4-6-20260115"]  # from response
    assert stored["manifest"]["code_commit"] and stored["manifest"]["code_dirty"] is True

    # --- replay returns recorded responses in order, no API needed
    client = ReplayClient.from_trace(trace_path(project.root, run.run_id))
    assert client.remaining == 1
    assert client.complete()["text"] == "x"

    # --- drift: user edits a generated file + removes a field in the architecture
    gen = tmp_path / "agents/_generated/researcher_base.py"
    gen.write_text(gen.read_text() + "\n# hand edit\n")
    changed = model.model_copy(deep=True)
    changed.schema("schema_r").fields = [SchemaField(name="title", type="str")]
    current = inspect_project(project, [DeclarationsAdapter()]).model
    report = detect_drift(project, changed, current, load_manifest(project.root))
    kinds = {i.kind for i in report.items}
    assert "generated_file_modified" in kinds
    assert "schema_field_added" in kinds            # code still has confidence -> structural
    assert all(i.severity != Severity.COSMETIC for i in report.visible())

    # a generated file edited by hand becomes a conflict, never an overwrite
    plan3 = plan_generation(project, changed, load_manifest(project.root))
    conflict = next(op for op in plan3.operations if op.path.endswith("researcher_base.py"))
    assert conflict.kind == OperationKind.CONFLICT and "hand" not in (conflict.new_content or "")

    # --- impact: field granular + past runs under the old hash
    imp = impact_of_schema(project, model, "schema_r", fields=["confidence"],
                           previous_architecture_hash=plan.architecture_hash)
    assert imp.direct_agents == ["Researcher", "Writer"]
    assert any(u.kind == "subscript" and u.file == "agents/writer.py"
               for u in imp.field_usages["confidence"])
    assert imp.affected_runs == 1 and imp.affected_run_ids == [run.run_id]


def test_apply_refuses_dirty_tree(tmp_path):
    project = ProjectContext.open(tmp_path)
    _git(tmp_path, "init", "-q")
    (tmp_path / "x.txt").write_text("dirty")
    plan = plan_generation(project, design(), load_manifest(project.root))
    res = apply_plan(project, plan, load_manifest(project.root))
    assert res.refused and not res.written
    res = apply_plan(project, plan, load_manifest(project.root), allow_dirty=True)
    assert res.written


def test_existing_files_are_user_owned(tmp_path):
    project = ProjectContext.open(tmp_path)
    (tmp_path / "agents/_generated").mkdir(parents=True)
    (tmp_path / "agents/_generated/researcher_base.py").write_text("mine")
    plan = plan_generation(project, design(), load_manifest(project.root))
    op = next(o for o in plan.operations if o.path == "agents/_generated/researcher_base.py")
    assert op.kind == OperationKind.CONFLICT
    assert load_architecture(tmp_path) is None
