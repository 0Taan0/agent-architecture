"""Copy this into your project. Runtime no-op, no dependency on agent-architect."""


def agent(name=None, **meta):
    def wrap(cls):
        cls.__architecture__ = {"name": name or cls.__name__, **meta}
        return cls
    return wrap
