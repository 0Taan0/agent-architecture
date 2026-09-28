"""Project registration lives in the USER HOME, never inside the user's project."""
from __future__ import annotations

import hashlib
import os
import sqlite3
from pathlib import Path


def home_dir() -> Path:
    p = Path(os.environ.get("AGENT_ARCHITECT_HOME", Path.home() / ".agent-architect"))
    p.mkdir(parents=True, exist_ok=True)
    return p


class ProjectRegistry:
    def __init__(self, path: Path | None = None) -> None:
        self.path = path or home_dir() / "projects.db"
        with self._conn() as c:
            c.execute("CREATE TABLE IF NOT EXISTS projects (id TEXT PRIMARY KEY, name TEXT, "
                      "path TEXT UNIQUE NOT NULL, python TEXT)")

    def _conn(self) -> sqlite3.Connection:
        conn = sqlite3.connect(self.path)
        conn.row_factory = sqlite3.Row
        return conn

    def register(self, path: Path, python: str | None = None) -> dict:
        path = Path(path).resolve()
        pid = "proj_" + hashlib.sha256(str(path).encode()).hexdigest()[:10]
        with self._conn() as c:
            c.execute("INSERT OR REPLACE INTO projects(id, name, path, python) VALUES (?,?,?,?)",
                      (pid, path.name, str(path), python))
        return {"id": pid, "name": path.name, "path": str(path), "python": python}

    def get(self, pid: str) -> dict | None:
        with self._conn() as c:
            r = c.execute("SELECT * FROM projects WHERE id=?", (pid,)).fetchone()
        return dict(r) if r else None

    def list(self) -> list[dict]:
        with self._conn() as c:
            return [dict(r) for r in c.execute("SELECT * FROM projects ORDER BY name")]
