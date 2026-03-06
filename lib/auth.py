"""
Azure AD OAuth authentication for the Streamlit app.

Uses MSAL to perform the Authorization Code flow with PKCE.
Browser-based redirect flow:
  1. Generate auth URL → user clicks → redirected to Microsoft login
  2. Azure AD redirects back with ?code= in query params
  3. Exchange code for access token → use as Bearer token for ADO API
"""

import logging
from typing import Optional

import msal
import streamlit as st

logger = logging.getLogger(__name__)

# Azure DevOps resource scope
ADO_SCOPE = ["499b84ac-1321-427f-aa17-267ca6975798/.default"]


def _build_msal_app(config):
    """Build the appropriate MSAL application instance."""
    authority = f"https://login.microsoftonline.com/{config.azure_tenant_id}"

    if config.azure_client_secret:
        return msal.ConfidentialClientApplication(
            client_id=config.azure_client_id,
            client_credential=config.azure_client_secret,
            authority=authority,
        )
    return msal.PublicClientApplication(
        client_id=config.azure_client_id,
        authority=authority,
    )


def _get_redirect_uri() -> str:
    """Determine the redirect URI from the request context."""
    try:
        headers = st.context.headers
        host = headers.get("Host", "localhost:8501")
        scheme = headers.get("X-Forwarded-Proto", "http")
        return f"{scheme}://{host}/"
    except Exception:
        return "http://localhost:8501/"


def get_auth_url(config) -> tuple[str, dict]:
    """Generate the Microsoft OAuth authorization URL.

    Returns (auth_url, flow_state). flow_state must be stored in session
    state and passed to exchange_code_for_token.
    """
    app = _build_msal_app(config)
    redirect_uri = _get_redirect_uri()

    flow = app.initiate_auth_code_flow(
        scopes=ADO_SCOPE,
        redirect_uri=redirect_uri,
    )

    if "auth_uri" not in flow:
        raise RuntimeError(
            f"Failed to generate auth URL: {flow.get('error_description', 'unknown')}"
        )

    return flow["auth_uri"], flow


def exchange_code_for_token(config, auth_code_flow: dict, query_params: dict) -> dict:
    """Exchange the authorization code for an access token.

    Returns dict with 'access_token' on success or 'error' on failure.
    """
    app = _build_msal_app(config)

    result = app.acquire_token_by_auth_code_flow(
        auth_code_flow=auth_code_flow,
        auth_response=query_params,
    )

    if "access_token" in result:
        logger.info("OAuth token acquired successfully.")
    else:
        logger.error(
            "OAuth token acquisition failed: %s - %s",
            result.get("error", "unknown"),
            result.get("error_description", ""),
        )

    return result


def render_login_page(config) -> Optional[str]:
    """Render the OAuth login UI and handle the full login lifecycle.

    Returns the access token if authenticated, or None if login is in progress.
    When None is returned, the login UI has been rendered and st.stop() should
    be called by the caller.
    """
    # Already authenticated
    if st.session_state.get("oauth_token"):
        return st.session_state.oauth_token

    # Check for auth code in redirect URL
    query_params = st.query_params.to_dict()

    if "code" in query_params and "oauth_flow" in st.session_state:
        with st.spinner("Completing sign-in..."):
            result = exchange_code_for_token(
                config,
                st.session_state.oauth_flow,
                query_params,
            )

        if "access_token" in result:
            st.session_state.oauth_token = result["access_token"]
            del st.session_state.oauth_flow
            st.query_params.clear()
            st.rerun()
        else:
            error = result.get("error_description", result.get("error", "Unknown error"))
            st.error(f"Sign-in failed: {error}")
            if "oauth_flow" in st.session_state:
                del st.session_state.oauth_flow
            return None

    # Show login page
    st.title("dbt YAML Editor")
    st.markdown("---")
    st.subheader("Sign in to continue")
    st.caption("This app requires Azure DevOps access via your Microsoft account.")

    if st.button("Sign in with Microsoft", type="primary"):
        try:
            auth_url, flow = get_auth_url(config)
            st.session_state.oauth_flow = flow
            st.markdown(
                f'<meta http-equiv="refresh" content="0;url={auth_url}">',
                unsafe_allow_html=True,
            )
            st.stop()
        except Exception as e:
            st.error(f"Failed to start login: {e}")

    return None
