from __future__ import annotations

from core.base import BaseAgent
from core.schemas import AnalysisResult, ProcessDescription


class ProcessAnalyzerAgent(BaseAgent):
    TOOLS = ["process_mining", "bpmn_parser"]

    def run(self, task: ProcessDescription) -> AnalysisResult:
        return AnalysisResult(bottlenecks=[s for s in task.steps if "approval" in s],
                              confidence=0.8)
