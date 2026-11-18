from pathlib import Path

import yaml

from core.inspector import InspectContext, inspect_project
from core.inspector.adapters import DeclarationsAdapter, ProjectLocalAdapter
from core.models import Provenance, from_yaml

EXPECTED = Path(__file__).resolve().parents[1] / "examples" / "expected"


def _expected(name):
    return from_yaml((EXPECTED / f"{name}.yaml").read_text())


def test_project_local_matches_expected_card(prozess_mas):
    rep = inspect_project(prozess_mas, [ProjectLocalAdapter()])
    assert rep.model == _expected("prozess-mas")
    names = {a.name for a in rep.model.agents}
    assert names == {"ProcessAnalyzerAgent", "ImprovementProposerAgent", "CriticAgent"}
    assert all(a.provenance == Provenance.DECLARED for a in rep.model.agents)
    assert {u.kind for u in rep.model.unresolved} == {"dynamic_tool_registration"}
    # inspect.py for the user's own MAS must stay small (target: ~50 lines)
    lines = [ln for ln in (prozess_mas.root / ".architecture/inspect.py").read_text().splitlines()
             if ln.strip() and not ln.strip().startswith("#")]
    assert len(lines) <= 50


def test_inspect_twice_gives_same_ids(prozess_mas):
    a = inspect_project(prozess_mas, [ProjectLocalAdapter()]).model
    b = inspect_project(prozess_mas, [ProjectLocalAdapter()]).model
    assert [x.id for x in a.agents] == [x.id for x in b.agents]


def test_declarations_reads_decorator_and_reports_declaration_drift(decorated):
    rep = inspect_project(decorated, [DeclarationsAdapter()])
    assert rep.model == _expected("decorated")
    researcher = rep.model.agent_by_name("researcher")
    assert researcher.tools == ["web_search"]
    assert researcher.model.model == "claude-sonnet-4-6"
    assert rep.model.schema(researcher.output_schema).name == "ResearchResult"
    assert len(rep.model.connections) == 1        # routes_to=["writer"] resolved by name
    assert any(u.kind == "declaration_drift" for u in rep.model.unresolved)


def test_ctx_resolves_dotted_bases(prozess_mas):
    ctx = InspectContext(prozess_mas)
    assert {c.name for c in ctx.classes_inheriting("core.base.BaseAgent")} == \
        {"ProcessAnalyzerAgent", "ImprovementProposerAgent", "CriticAgent"}
    assert {c.name for c in ctx.classes_inheriting("BaseAgent")} == \
        {"ProcessAnalyzerAgent", "ImprovementProposerAgent", "CriticAgent"}
    calls = ctx.calls_to("registry.register")
    assert calls and calls[0].file == "core/registry.py"


def test_expected_yaml_files_parse():
    for p in EXPECTED.glob("*.yaml"):
        assert yaml.safe_load(p.read_text())["agents"]
