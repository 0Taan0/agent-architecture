"""Call sites whose callee can be named for certain.

"Incomplete yes, wrong no": a call is only reported when its callee is an imported name,
a name defined at module level, or a method on a variable or `self.attr` that is bound to
exactly one constructor call. `g = Graph(); g.add_edge(..)` is reported as
`lib.Graph().add_edge`. A callee on a variable with any other binding (parameter, loop
target, second assignment) is left out.

The digest shows these callee names to the discovery agent, and rules in mapping.yaml
match them, so both sides speak about the same thing.
"""
from __future__ import annotations

import ast
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from core.inspector.context import ParsedModule

_DEFS = (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)
_FUNCS = (ast.FunctionDef, ast.AsyncFunctionDef)
# Marker: the name is bound, but not to one known constructor call
_AMBIGUOUS = ""


def certain_calls(m: ParsedModule) -> list[tuple[str, ast.Call]]:
    """All (callee, call node) pairs of a module whose callee is certain."""
    known = set(m.imports) | {n.name for n in m.tree.body if isinstance(n, _DEFS)}

    def bindings(nodes: list[ast.AST], key_of) -> dict[str, str]:
        # name -> dotted constructor, or _AMBIGUOUS as soon as it has any other binding
        typed: dict[int, str] = {}
        for n in nodes:
            target = n.targets[0] if isinstance(n, ast.Assign) and len(n.targets) == 1 else (
                n.target if isinstance(n, ast.AnnAssign) else None)
            if target is not None and isinstance(n.value, ast.Call) \
                    and dotted_chain(n.value.func)[0] in known:
                typed[id(target)] = m.resolve(n.value.func)
        seen: dict[str, set[str]] = {}
        for n in nodes:
            key = key_of(n)
            if key:
                seen.setdefault(key, set()).add(typed.get(id(n), _AMBIGUOUS))
        return {k: v.pop() if len(v) == 1 else _AMBIGUOUS for k, v in seen.items()}

    module_vars = bindings(list(_scope(m.tree)), _local_key)
    # id(function) -> self attributes of its class
    self_vars: dict[int, dict[str, str]] = {}
    # ast.walk is breadth-first, so inner classes override outer ones
    for node in ast.walk(m.tree):
        if isinstance(node, ast.ClassDef):
            members = list(ast.walk(node))
            attrs = bindings(members, _self_key)
            self_vars.update({id(fn): attrs for fn in members if isinstance(fn, _FUNCS)})
        elif isinstance(node, (ast.Global, ast.Nonlocal)):
            module_vars.update(dict.fromkeys(node.names, _AMBIGUOUS))

    found: list[tuple[str, ast.Call]] = []
    for scope in ast.walk(m.tree):
        if not isinstance(scope, (ast.Module, *_DEFS)):
            continue
        nodes = list(_scope(scope))
        local = module_vars if scope is m.tree else bindings(nodes, _local_key)
        for n in nodes:
            root, attrs = dotted_chain(n.func) if isinstance(n, ast.Call) else (None, [])
            if root is None:
                continue
            origin = None
            if root == "self":
                if len(attrs) > 1:
                    origin, attrs = self_vars.get(id(scope), {}).get(f"self.{attrs[0]}"), attrs[1:]
            elif root in local:
                origin = local[root]
            elif root in module_vars:
                origin = module_vars[root] if root not in known else None
            elif root in known:
                found.append((m.resolve(n.func), n))
            if origin and attrs:
                found.append((f"{origin}().{'.'.join(attrs)}", n))
    return found


def dotted_chain(expr: ast.expr) -> tuple[str | None, list[str]]:
    """`a.b.c` -> ("a", ["b", "c"]); anything that is not a plain dotted name -> (None, [])."""
    attrs: list[str] = []
    while isinstance(expr, ast.Attribute):
        attrs.append(expr.attr)
        expr = expr.value
    if isinstance(expr, ast.Name):
        return expr.id, attrs[::-1]
    return None, []


def _local_key(node: ast.AST) -> str | None:
    # Name a node binds in its scope: assignment, loop/with/comprehension target, parameter
    if isinstance(node, ast.Name) and isinstance(node.ctx, ast.Store):
        return node.id
    return node.arg if isinstance(node, ast.arg) else None


def _self_key(node: ast.AST) -> str | None:
    if isinstance(node, ast.Attribute) and isinstance(node.ctx, ast.Store) \
            and isinstance(node.value, ast.Name) and node.value.id == "self":
        return f"self.{node.attr}"
    return None


def _scope(node: ast.AST):
    # Nodes of `node`'s own scope: nested functions and classes are not entered
    stack = list(ast.iter_child_nodes(node))
    while stack:
        n = stack.pop()
        yield n
        if not isinstance(n, _DEFS):
            stack.extend(ast.iter_child_nodes(n))
