"""Evaluation harness - Slice 5. Placeholder with the one contract that must hold:
evaluation compares runs by architecture_hash, so runs under different hashes are
flagged as not directly comparable (Spec 9.1 / 11.3)."""
from __future__ import annotations

from pydantic import BaseModel

from core.runtime import RunStore


class Comparability(BaseModel):
    comparable: bool
    reason: str


def comparable(store: RunStore, run_a: str, run_b: str) -> Comparability:
    a, b = store.get(run_a), store.get(run_b)
    if a is None or b is None:
        return Comparability(comparable=False, reason="run not found")
    if a["architecture_hash"] != b["architecture_hash"]:
        return Comparability(comparable=False,
                             reason="runs used different architecture states")
    return Comparability(comparable=True, reason="same architecture_hash")
