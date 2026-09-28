from __future__ import annotations

from core.base import BaseAgent
from core.schemas import AnalysisResult, ImprovementProposal


class ImprovementProposerAgent(BaseAgent):
    TOOLS = ["knowledge_base"]

    def run(self, task: AnalysisResult) -> ImprovementProposal:
        return ImprovementProposal(title="Automate approvals",
                                   rationale=f"{len(task.bottlenecks)} manual approvals")
