"""
Configuration loader — reads ADO connection details from environment variables.
"""

import os
from dataclasses import dataclass


@dataclass
class ADOConfig:
    org: str
    project: str
    repo: str
    pat: str
    default_branch: str = "main"
    branch_prefix: str = "yaml-edit/"


def load_config() -> ADOConfig:
    """Load ADO config from environment variables. Raises if required vars are missing."""
    missing = []
    for var in ("ADO_ORG", "ADO_PROJECT", "ADO_REPO", "ADO_PAT"):
        if not os.environ.get(var):
            missing.append(var)
    if missing:
        raise EnvironmentError(
            f"Missing required environment variables: {', '.join(missing)}"
        )
    return ADOConfig(
        org=os.environ["ADO_ORG"],
        project=os.environ["ADO_PROJECT"],
        repo=os.environ["ADO_REPO"],
        pat=os.environ["ADO_PAT"],
        default_branch=os.environ.get("ADO_DEFAULT_BRANCH", "main"),
        branch_prefix=os.environ.get("ADO_BRANCH_PREFIX", "yaml-edit/"),
    )
