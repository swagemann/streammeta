"""
ruamel.yaml wrapper for round-trip YAML editing.

Preserves comments, key ordering, and formatting so diffs are minimal.
"""

from io import StringIO

from ruamel.yaml import YAML


def _make_yaml() -> YAML:
    y = YAML()
    y.preserve_quotes = True
    y.width = 4096  # prevent line-wrapping
    return y


def parse_yaml(text: str):
    """Parse YAML string, returning a ruamel.yaml CommentedMap."""
    y = _make_yaml()
    return y.load(text)


def dump_yaml(data) -> str:
    """Dump a ruamel.yaml object back to string, preserving formatting."""
    y = _make_yaml()
    buf = StringIO()
    y.dump(data, buf)
    return buf.getvalue()


# ---------------------------------------------------------------------------
# Helpers to extract / update dbt schema structures
# ---------------------------------------------------------------------------

def get_models(parsed) -> list:
    """Return the list of models from a parsed dbt schema YAML."""
    if parsed is None:
        return []
    # dbt schema files have either 'models' or 'sources' at top level
    return parsed.get("models", []) or []


def get_sources(parsed) -> list:
    if parsed is None:
        return []
    return parsed.get("sources", []) or []


def get_columns(model: dict) -> list:
    """Return the columns list for a model/source table, creating if absent."""
    if "columns" not in model or model["columns"] is None:
        model["columns"] = []
    return model["columns"]


def ensure_field(d: dict, key: str, default):
    """Ensure a key exists in a CommentedMap with a default value."""
    if key not in d or d[key] is None:
        d[key] = default
    return d[key]
