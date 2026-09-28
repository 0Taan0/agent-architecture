from typing import Any

from architecture_decl import agent
from models.schemas import Query, ResearchResult


@agent("researcher", tools=["web_search"], model="claude-sonnet-4-6", routes_to=["writer"],
       output="ResearchResult")
class ResearcherAgent:
    def run(self, task: Query) -> dict[str, Any]:   # declared ResearchResult, returns dict
        return {"title": task.text}


@agent("writer", input="ResearchResult")
class WriterAgent:
    def run(self, task: ResearchResult) -> str:
        return task.summary
