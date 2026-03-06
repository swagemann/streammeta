"""
Azure AD OAuth authentication for the Streamlit app.

Uses azure.identity InteractiveBrowserCredential to open a browser login
popup. The credential object is cached in session state so tokens can be
silently refreshed.
"""

import logging
from typing import Optional

import streamlit as st
from azure.identity import InteractiveBrowserCredential

logger = logging.getLogger(__name__)

# Azure DevOps resource scope
ADO_SCOPE = "499b84ac-1321-427f-aa17-267ca6975798/.default"


def _get_credential(config) -> InteractiveBrowserCredential:
    """Create an interactive browser credential for Azure AD login."""
    kwargs = {}
    if config.azure_tenant_id and config.azure_tenant_id != "organizations":
        kwargs["tenant_id"] = config.azure_tenant_id
    if config.azure_client_id:
        kwargs["client_id"] = config.azure_client_id
    return InteractiveBrowserCredential(**kwargs)


def render_login_page(config) -> Optional[str]:
    """Render the OAuth login UI and handle the login lifecycle.

    Returns the access token if authenticated, or None if login is in progress.
    When None is returned, the caller should call st.stop().
    """
    # Already authenticated — get a (possibly refreshed) token
    if st.session_state.get("oauth_credential"):
        try:
            token = st.session_state.oauth_credential.get_token(ADO_SCOPE).token
            return token
        except Exception:
            # Token refresh failed — clear and re-auth
            st.session_state.pop("oauth_credential", None)

    # Show login page
    st.title("dbt YAML Editor")
    st.markdown("---")
    st.subheader("Sign in to continue")
    st.caption("This app requires Azure DevOps access via your Microsoft account.")

    if st.button("Sign in with Microsoft", type="primary"):
        try:
            cred = _get_credential(config)
            cred.get_token(ADO_SCOPE)  # triggers browser login popup
            st.session_state.oauth_credential = cred
            st.rerun()
        except Exception as e:
            st.error(f"Authentication failed: {e}")

    return None
