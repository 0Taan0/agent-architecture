"""Deterministic stable identifiers (Spec 7.12).

IDs are derived from file path + symbol name. Inspecting the same code base twice
yields the same IDs. Only an explicit rename migration moves an ID.
"""
from __future__ import annotations

import hashlib

ID_LENGTH = 7


def stable_id(kind: str, file: str, symbol: str) -> str:
    key = f"{file.replace(chr(92), '/')}::{symbol}".encode()
    return f"{kind}_{hashlib.sha256(key).hexdigest()[:ID_LENGTH]}"


def connection_id(source: str, target: str, condition: str | None = None) -> str:
    key = f"{source}->{target}" + (f"?{condition}" if condition else "")
    return f"conn_{hashlib.sha256(key.encode()).hexdigest()[:ID_LENGTH]}"


def schema_id(file: str, symbol: str) -> str:
    return stable_id("schema", file, symbol)


def agent_id(file: str, symbol: str) -> str:
    return stable_id("agent", file, symbol)
