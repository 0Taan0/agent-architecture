"""architecture.yaml <-> ArchitectureModel. The architecture lives in the user's repo,
not in a platform database (Spec ch. 16, decided in the build guide)."""
from __future__ import annotations

from pathlib import Path

import yaml

from .architecture import ArchitectureModel, LayoutModel

ARCHITECTURE_FILE = "architecture.yaml"
LAYOUT_FILE = ".architecture/layout.yaml"


def to_yaml(model: ArchitectureModel) -> str:
    data = model.sorted().model_dump(mode="json", exclude_none=True)
    return yaml.safe_dump(data, sort_keys=False, allow_unicode=True)


def from_yaml(text: str) -> ArchitectureModel:
    return ArchitectureModel.model_validate(yaml.safe_load(text) or {})


def load_architecture(root: Path) -> ArchitectureModel | None:
    path = Path(root) / ARCHITECTURE_FILE
    if not path.exists():
        return None
    return from_yaml(path.read_text(encoding="utf-8"))


def save_architecture(root: Path, model: ArchitectureModel) -> Path:
    path = Path(root) / ARCHITECTURE_FILE
    path.write_text(to_yaml(model), encoding="utf-8")
    return path


def load_layout(root: Path) -> LayoutModel:
    path = Path(root) / LAYOUT_FILE
    if not path.exists():
        return LayoutModel()
    return LayoutModel.model_validate(yaml.safe_load(path.read_text(encoding="utf-8")) or {})


def save_layout(root: Path, layout: LayoutModel) -> Path:
    path = Path(root) / LAYOUT_FILE
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(yaml.safe_dump(layout.model_dump(mode="json"), sort_keys=True),
                    encoding="utf-8")
    return path
