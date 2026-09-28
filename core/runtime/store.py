"""Runs live in SQLite inside the project (.architecture/runs.db). Local-first, one user."""
from __future__ import annotations

import json
import sqlite3
from pathlib import Path

RUNS_DB = ".architecture/runs.db"

_SCHEMA = """
CREATE TABLE IF NOT EXISTS runs (
    run_id TEXT PRIMARY KEY,
    started_at TEXT NOT NULL,
    architecture_hash TEXT NOT NULL,
    code_commit TEXT,
    code_dirty INTEGER NOT NULL DEFAULT 0,
    input_hash TEXT,
    status TEXT NOT NULL,
    duration_ms REAL,
    error TEXT,
    manifest_json TEXT NOT NULL,
    output_json TEXT,
    trace_path TEXT
);
CREATE INDEX IF NOT EXISTS idx_runs_arch ON runs(architecture_hash);
"""


class RunStore:
    def __init__(self, root: Path) -> None:
        self.path = Path(root) / RUNS_DB
        self.path.parent.mkdir(parents=True, exist_ok=True)
        with self._conn() as c:
            c.executescript(_SCHEMA)

    def _conn(self) -> sqlite3.Connection:
        conn = sqlite3.connect(self.path)
        conn.row_factory = sqlite3.Row
        return conn

    def insert(self, run_id: str, started_at: str, manifest: dict, trace_path: str) -> None:
        with self._conn() as c:
            c.execute(
                "INSERT INTO runs(run_id, started_at, architecture_hash, code_commit, code_dirty,"
                " input_hash, status, manifest_json, trace_path) VALUES (?,?,?,?,?,?,?,?,?)",
                (run_id, started_at, manifest["architecture_hash"], manifest.get("code_commit"),
                 int(bool(manifest.get("code_dirty"))), manifest.get("input_hash"), "running",
                 json.dumps(manifest), trace_path))

    def finish(self, run_id: str, status: str, duration_ms: float, output: str | None,
               error: str | None, manifest: dict | None = None) -> None:
        with self._conn() as c:
            if manifest is not None:
                c.execute("UPDATE runs SET status=?, duration_ms=?, output_json=?, error=?, "
                          "manifest_json=? WHERE run_id=?",
                          (status, duration_ms, output, error, json.dumps(manifest), run_id))
            else:
                c.execute("UPDATE runs SET status=?, duration_ms=?, output_json=?, error=? "
                          "WHERE run_id=?", (status, duration_ms, output, error, run_id))

    def list(self, limit: int = 50) -> list[dict]:
        with self._conn() as c:
            rows = c.execute("SELECT * FROM runs ORDER BY started_at DESC LIMIT ?",
                             (limit,)).fetchall()
        return [_row(r) for r in rows]

    def get(self, run_id: str) -> dict | None:
        with self._conn() as c:
            r = c.execute("SELECT * FROM runs WHERE run_id=?", (run_id,)).fetchone()
        return _row(r) if r else None

    def run_ids_for_hash(self, architecture_hash: str) -> list[str]:
        """The query no IDE has: which past runs used this architecture state."""
        with self._conn() as c:
            rows = c.execute("SELECT run_id FROM runs WHERE architecture_hash=? "
                             "ORDER BY started_at", (architecture_hash,)).fetchall()
        return [r["run_id"] for r in rows]


def _row(r: sqlite3.Row) -> dict:
    d = dict(r)
    d["manifest"] = json.loads(d.pop("manifest_json") or "{}")
    d["code_dirty"] = bool(d["code_dirty"])
    return d
