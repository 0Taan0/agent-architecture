from __future__ import annotations

from typing import Any


class BaseAgent:
    """Common base of all agents in the process-improvement MAS."""
    TOOLS: list[str] = []

    def run(self, task: Any) -> Any:  # pragma: no cover
        raise NotImplementedError
