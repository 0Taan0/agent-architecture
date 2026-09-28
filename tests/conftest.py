import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

import pytest

from core.models import ProjectContext

EXAMPLES = ROOT / "examples"


@pytest.fixture
def prozess_mas() -> ProjectContext:
    return ProjectContext.open(EXAMPLES / "prozess-mas")


@pytest.fixture
def decorated() -> ProjectContext:
    return ProjectContext.open(EXAMPLES / "decorated")


@pytest.fixture
def langgraph_demo() -> ProjectContext:
    return ProjectContext.open(EXAMPLES / "langgraph-demo")
