from __future__ import annotations

from pydantic import BaseModel


class ProcessDescription(BaseModel):
    name: str
    steps: list[str]
    owner: str | None = None


class AnalysisResult(BaseModel):
    bottlenecks: list[str]
    confidence: float


class ImprovementProposal(BaseModel):
    title: str
    rationale: str
    expected_gain: float | None = None
