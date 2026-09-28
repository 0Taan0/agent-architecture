from __future__ import annotations

from typing import Any

from core.base import BaseAgent
from core.schemas import ImprovementProposal


class CriticAgent(BaseAgent):
    def run(self, task: ImprovementProposal) -> dict[str, Any]:
        return {"accepted": task.expected_gain is None or task.expected_gain > 0}
