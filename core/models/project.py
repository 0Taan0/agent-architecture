"""ProjectContext: everything an adapter needs to know about the target project.
Pure data + path helpers. No parsing here (that is the inspector's job)."""
from __future__ import annotations

import sys
from pathlib import Path

from pydantic import BaseModel, Field

DEFAULT_EXCLUDES = {".git", ".venv", "venv", "node_modules", "__pycache__", ".architecture",
                    "dist", "build", ".mypy_cache", ".pytest_cache", ".tox"}


class ProjectContext(BaseModel):
    root: Path
    name: str | None = None
    python: Path | None = None            # interpreter of the project's venv
    excludes: set[str] = Field(default_factory=lambda: set(DEFAULT_EXCLUDES))

    @classmethod
    def open(cls, root: str | Path, python: str | Path | None = None) -> ProjectContext:
        root = Path(root).resolve()
        if not root.is_dir():
            raise FileNotFoundError(f"project root does not exist: {root}")
        py = Path(python) if python else cls._detect_python(root)
        return cls(root=root, name=root.name, python=py)

    @staticmethod
    def _detect_python(root: Path) -> Path | None:
        for cand in (".venv/bin/python", "venv/bin/python", ".venv/Scripts/python.exe",
                     "venv/Scripts/python.exe"):
            p = root / cand
            if p.exists():
                return p
        return None

    @property
    def interpreter(self) -> str:
        return str(self.python) if self.python else sys.executable

    @property
    def display_name(self) -> str:
        return self.name or self.root.name

    def python_files(self) -> list[Path]:
        out: list[Path] = []
        for p in sorted(self.root.rglob("*.py")):
            rel_parts = p.relative_to(self.root).parts
            if any(part in self.excludes for part in rel_parts[:-1]):
                continue
            out.append(p)
        return out

    def rel(self, path: Path) -> str:
        return path.resolve().relative_to(self.root).as_posix()

    def abs(self, rel: str) -> Path:
        return (self.root / rel).resolve()

    def is_inside(self, path: Path) -> bool:
        """Path-traversal guard used by the backend /file endpoint."""
        try:
            path.resolve().relative_to(self.root)
            return True
        except ValueError:
            return False

    def module_name(self, path: Path) -> str:
        rel = path.resolve().relative_to(self.root).with_suffix("")
        parts = list(rel.parts)
        if parts and parts[-1] == "__init__":
            parts = parts[:-1]
        return ".".join(parts)

    def read_text(self, rel: str) -> str:
        return self.abs(rel).read_text(encoding="utf-8")
