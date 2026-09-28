"""Stage 2 - Replay (guaranteed). A fake client that returns the recorded responses in
the recorded order. Only works because the trace stores full request AND response."""
from __future__ import annotations

from pathlib import Path

from .trace import TraceEvent, read_trace


class ReplayExhausted(RuntimeError):
    pass


class ReplayClient:
    """Inject this where the model client would be. `complete(request)` returns the
    next recorded response. Optionally verifies the request still matches."""

    def __init__(self, events: list[TraceEvent], *, strict: bool = False) -> None:
        self._calls = [e for e in events if e.event == "gen_ai.call"]
        self._pos = 0
        self.strict = strict

    @classmethod
    def from_trace(cls, path: Path, **kw) -> ReplayClient:
        return cls(read_trace(path), **kw)

    @property
    def remaining(self) -> int:
        return len(self._calls) - self._pos

    def complete(self, request: dict | None = None) -> dict:
        if self._pos >= len(self._calls):
            raise ReplayExhausted("no more recorded responses")
        ev = self._calls[self._pos]
        self._pos += 1
        recorded = ev.data.get("request") or {}
        if self.strict and request is not None and request != recorded:
            raise ValueError(f"replay mismatch at call {self._pos}: request differs from "
                             f"the recorded one")
        return ev.data.get("response") or {}
