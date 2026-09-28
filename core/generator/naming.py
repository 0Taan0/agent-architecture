from __future__ import annotations

import re


def snake(name: str) -> str:
    s = re.sub(r"(?<!^)(?=[A-Z])", "_", name).replace("-", "_").replace(" ", "_").lower()
    s = re.sub(r"_agent$", "", s)
    s = re.sub(r"[^a-z0-9_]", "", s)
    return s or "agent"


def pascal(name: str) -> str:
    return "".join(p.capitalize() for p in snake(name).split("_") if p)
