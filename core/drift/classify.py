"""Classification is a table, not a heuristic (Spec 7.6)."""
from __future__ import annotations

from enum import Enum


class Severity(str, Enum):
    BREAKING = "breaking"
    STRUCTURAL = "structural"
    COSMETIC = "cosmetic"


class Resolution(str, Enum):
    ADOPT = "adopt"      # architecture follows the code
    ENFORCE = "enforce"  # change proposal for the code
    ACCEPT = "accept"    # keep the deviation, documented


SEVERITY: dict[str, Severity] = {
    "agent_removed_in_code": Severity.BREAKING,
    "agent_added_in_code": Severity.STRUCTURAL,
    "agent_renamed": Severity.STRUCTURAL,
    "schema_field_removed": Severity.BREAKING,
    "schema_field_type_changed": Severity.BREAKING,
    "schema_field_added": Severity.STRUCTURAL,
    "schema_removed_in_code": Severity.BREAKING,
    "schema_added_in_code": Severity.STRUCTURAL,
    "tool_removed_in_code": Severity.BREAKING,
    "tool_added_in_code": Severity.STRUCTURAL,
    "io_schema_changed": Severity.BREAKING,
    "model_changed": Severity.STRUCTURAL,
    "connection_removed_in_code": Severity.BREAKING,
    "connection_added_in_code": Severity.STRUCTURAL,
    "generated_file_modified": Severity.STRUCTURAL,
    "generated_file_missing": Severity.BREAKING,
    "declaration_drift": Severity.STRUCTURAL,
    "adapter_conflict": Severity.STRUCTURAL,
    "description_changed": Severity.COSMETIC,
    "code_ref_moved": Severity.COSMETIC,
}


def severity_of(kind: str) -> Severity:
    return SEVERITY.get(kind, Severity.STRUCTURAL)
