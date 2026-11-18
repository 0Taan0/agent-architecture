from core.inspector.rules import RulesAdapter

from .declarations import DeclarationsAdapter
from .project_local import ProjectLocalAdapter

ALL_ADAPTERS = [DeclarationsAdapter, ProjectLocalAdapter, RulesAdapter]

__all__ = ["ALL_ADAPTERS", "DeclarationsAdapter", "ProjectLocalAdapter", "RulesAdapter"]
