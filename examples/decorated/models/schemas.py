from dataclasses import dataclass


@dataclass
class Query:
    text: str


@dataclass
class ResearchResult:
    title: str
    sources: list[str]
    summary: str
    confidence: float
