"""
Configuration loader — reads ADO connection details from environment variables.
"""

import os
from dataclasses import dataclass
from typing import Optional


@dataclass
class ADOConfig:
    org: str
    project: str
    repo: str
    auth_mode: str = "pat"
    pat: Optional[str] = None
    default_branch: str = "main"
    branch_prefix: str = "yaml-edit/"
    # OAuth fields (only used when auth_mode == "oauth")
    azure_tenant_id: Optional[str] = None
    azure_client_id: Optional[str] = None
    azure_client_secret: Optional[str] = None


def load_config() -> ADOConfig:
    """Load ADO config from environment variables. Raises if required vars are missing."""
    auth_mode = os.environ.get("AUTH_MODE", "pat").lower()

    # Always required
    missing = []
    for var in ("ADO_ORG", "ADO_PROJECT", "ADO_REPO"):
        if not os.environ.get(var):
            missing.append(var)

    # Mode-specific requirements
    if auth_mode == "pat":
        if not os.environ.get("ADO_PAT"):
            missing.append("ADO_PAT")
    elif auth_mode == "oauth":
        for var in ("AZURE_TENANT_ID", "AZURE_CLIENT_ID"):
            if not os.environ.get(var):
                missing.append(var)
    else:
        raise EnvironmentError(
            f"Invalid AUTH_MODE '{auth_mode}'. Must be 'pat' or 'oauth'."
        )

    if missing:
        raise EnvironmentError(
            f"Missing required environment variables: {', '.join(missing)}"
        )

    return ADOConfig(
        org=os.environ["ADO_ORG"],
        project=os.environ["ADO_PROJECT"],
        repo=os.environ["ADO_REPO"],
        auth_mode=auth_mode,
        pat=os.environ.get("ADO_PAT"),
        default_branch=os.environ.get("ADO_DEFAULT_BRANCH", "main"),
        branch_prefix=os.environ.get("ADO_BRANCH_PREFIX", "yaml-edit/"),
        azure_tenant_id=os.environ.get("AZURE_TENANT_ID"),
        azure_client_id=os.environ.get("AZURE_CLIENT_ID"),
        azure_client_secret=os.environ.get("AZURE_CLIENT_SECRET"),
    )
