"""Dynamic tool registry - the classic thing a static inspector cannot see."""
_TOOLS: dict[str, object] = {}


def register(name: str, tool: object) -> None:
    _TOOLS[name] = tool


def load_plugins(names: list[str]) -> None:
    for n in names:
        register(n, __import__(n))   # dynamic: unresolved by design
