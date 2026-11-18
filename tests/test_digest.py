from core.discovery import build_digest
from core.models import ProjectContext


def _project(tmp_path, files: dict[str, str]) -> ProjectContext:
    for rel, src in files.items():
        path = tmp_path / rel
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(src)
    return ProjectContext.open(tmp_path)


def _calls(digest, file: str) -> dict[str, list[int]]:
    module = next(m for m in digest.modules if m.file == file)
    return {c.callee: c.lines for c in module.calls}


def test_digest_of_prozess_mas(prozess_mas):
    digest = build_digest(prozess_mas)
    modules = {m.file: m for m in digest.modules}
    assert "agents/__init__.py" not in modules            # nothing to say about empty modules
    analyzer = modules["agents/analyzer.py"].classes[0]
    assert analyzer.name == "ProcessAnalyzerAgent"
    assert analyzer.bases == ["core.base.BaseAgent"]
    assert analyzer.attrs == ["TOOLS = ['process_mining', 'bpmn_parser']"]
    run = analyzer.methods[0]
    assert (run.name, run.params, run.returns) == ("run", ["task: ProcessDescription"], "AnalysisResult")
    assert modules["pipeline.py"].assigns == \
        ["PIPELINE = [ProcessAnalyzerAgent, ImprovementProposerAgent, CriticAgent]"]
    assert modules["core/schemas.py"].classes[0].attrs == \
        ["name: str", "steps: list[str]", "owner: str | None = None"]
    assert _calls(digest, "core/registry.py") == {"core.registry.register": [11]}
    # stdlib and __future__ are noise for the agent
    assert modules["core/base.py"].imports == []


def test_digest_is_deterministic(prozess_mas):
    a, b = build_digest(prozess_mas), build_digest(prozess_mas)
    assert a.hash == b.hash and a.hash.startswith("sha256:")
    assert a.render() == b.render()
    text = a.render()
    assert "## agents/analyzer.py" in text
    assert "imports: core.base.BaseAgent, core.schemas.{AnalysisResult,ProcessDescription}" in text
    assert "class ProcessAnalyzerAgent(core.base.BaseAgent) #L7" in text
    assert "  def run(task: ProcessDescription) -> AnalysisResult #L10" in text
    assert "calls: core.registry.register #L11" in text


def test_hash_ignores_line_shifts_but_not_structure(tmp_path):
    src = "class A:\n    def run(self, task: int) -> str: ...\n"
    before = build_digest(_project(tmp_path, {"a.py": src}))
    moved = build_digest(_project(tmp_path, {"a.py": "# a comment\n\n" + src}))
    assert moved.modules[0].classes[0].line == before.modules[0].classes[0].line + 2
    assert moved.hash == before.hash
    changed = build_digest(_project(tmp_path, {"a.py": src.replace("-> str", "-> bytes")}))
    assert changed.hash != before.hash


def test_calls_on_instances_are_named_by_their_constructor(tmp_path):
    digest = build_digest(_project(tmp_path, {
        "lib.py": "class Graph:\n    def add_edge(self, a, b): ...\n",
        "agents.py": "class Critic:\n    def run(self, x): ...\n",
        "app.py": (
            "import json\n"
            "from lib import Graph\n"
            "from agents import Critic\n"
            "g = Graph()\n"
            "g.add_edge('a', 'b')\n"
            "json.dumps({})\n"
            "class Orchestrator:\n"
            "    def __init__(self):\n"
            "        self.critic = Critic()\n"
            "    def step(self, x):\n"
            "        self.helper(x)\n"
            "        return self.critic.run(x)\n"
            "def build():\n"
            "    g.add_edge('b', 'c')\n"
            "    len([])\n"
        ),
    }))
    assert _calls(digest, "app.py") == {
        "agents.Critic": [9], "agents.Critic().run": [12],
        "lib.Graph": [4], "lib.Graph().add_edge": [5, 14],
    }


def test_uncertain_callees_are_left_out(tmp_path):
    digest = build_digest(_project(tmp_path, {
        "lib.py": "class A:\n    pass\nclass B:\n    pass\n",
        "app.py": (
            "from lib import A, B\n"
            "x = A()\n"
            "x = B()\n"                    # two origins
            "x.run()\n"
            "y = A()\n"
            "def f(y):\n"                  # parameter shadows the module variable
            "    y.run()\n"
            "def g(items):\n"
            "    z = A()\n"
            "    for z in items:\n"        # rebound by the loop
            "        z.run()\n"
            "class C:\n"
            "    def __init__(self, a):\n"
            "        self.a = A()\n"
            "        self.a = a\n"         # second, unknown origin
            "    def go(self):\n"
            "        self.a.run()\n"
        ),
    }))
    assert set(_calls(digest, "app.py")) == {"lib.A", "lib.B"}


def test_relative_imports_resolve_to_absolute_names(tmp_path):
    digest = build_digest(_project(tmp_path, {
        "pkg/__init__.py": "from .base import Base\n",
        "pkg/base.py": "class Base:\n    pass\n",
        "pkg/sub/__init__.py": "",
        "pkg/sub/worker.py": "from ..base import Base\nfrom . import helpers\nclass W(Base):\n    pass\n",
        "pkg/sub/helpers.py": "X = 1\n",
    }))
    modules = {m.file: m for m in digest.modules}
    assert modules["pkg/__init__.py"].imports == ["pkg.base.Base"]
    assert modules["pkg/sub/worker.py"].imports == ["pkg.base.Base", "pkg.sub.helpers"]
    assert modules["pkg/sub/worker.py"].classes[0].bases == ["pkg.base.Base"]


def test_tests_are_skipped_and_parse_errors_reported(tmp_path):
    project = _project(tmp_path, {
        "app.py": "X = 1\n",
        "broken.py": "def (:\n",
        "tests/test_app.py": "def test_x(): ...\n",
        "conftest.py": "Y = 2\n",
    })
    digest = build_digest(project)
    assert [m.file for m in digest.modules] == ["app.py"]
    assert digest.skipped == ["conftest.py", "tests/test_app.py"]
    assert digest.parse_errors == ["broken.py"]
    assert "tests/test_app.py" in [m.file for m in build_digest(project, include_tests=True).modules]
