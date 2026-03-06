"""
dbt YAML Editor — Streamlit App
"""

import os
from datetime import datetime

import streamlit as st
from dotenv import load_dotenv

from ado_git_client import ADOGitClient, ADOGitClientError, ADOConflictError
from lib.auth import render_login_page
from lib.config import load_config
from lib.yaml_parser import (
    parse_yaml,
    dump_yaml,
    get_models,
    get_sources,
    get_columns,
    ensure_field,
)

load_dotenv()

# ── Page config ──────────────────────────────────────────────────────────────

st.set_page_config(page_title="dbt YAML Editor", layout="wide")

STANDARD_TESTS = ["not_null", "unique", "accepted_values", "relationships"]

# ── Styling ──────────────────────────────────────────────────────────────────

st.markdown(
    """
    <style>
    /* tighten spacing */
    .block-container { padding-top: 1.5rem; }
    /* subtle dividers */
    hr { margin: 0.5rem 0; }
    </style>
    """,
    unsafe_allow_html=True,
)

# ── Init session state ───────────────────────────────────────────────────────


def init_state():
    defaults = {
        "client": None,
        "config": None,
        "connected": False,
        "yaml_files": [],
        "current_branch": None,
        "selected_file": None,
        "raw_yaml": None,
        "parsed_yaml": None,
        "working_branch": None,
        "committed_files": [],
        "app_prs": [],  # PRs created in this session
    }
    for k, v in defaults.items():
        if k not in st.session_state:
            st.session_state[k] = v


init_state()

# ── Connect to ADO ───────────────────────────────────────────────────────────


def connect():
    try:
        cfg = load_config()
        st.session_state.config = cfg

        if cfg.auth_mode == "oauth":
            token = render_login_page(cfg)
            if token is None:
                st.stop()
            client = ADOGitClient(
                org=cfg.org,
                project=cfg.project,
                repo=cfg.repo,
                token=token,
                target_branch=cfg.default_branch,
            )
        else:
            client = ADOGitClient(
                org=cfg.org,
                project=cfg.project,
                repo=cfg.repo,
                pat=cfg.pat,
                target_branch=cfg.default_branch,
            )

        # test connection by listing refs
        client.get_ref(cfg.default_branch)
        st.session_state.client = client
        st.session_state.current_branch = cfg.default_branch
        st.session_state.connected = True
    except EnvironmentError as e:
        st.error(f"Config error: {e}")
    except ADOGitClientError as e:
        st.error(f"Connection failed: {e}")


if not st.session_state.connected:
    connect()

# ── Helpers ──────────────────────────────────────────────────────────────────


@st.cache_data(ttl=120)
def fetch_yaml_files(_client, branch):
    """Fetch list of YAML files from the repo. Cached 2 min."""
    try:
        return _client.list_yaml_files("/models", branch=branch)
    except ADOGitClientError:
        return []


def load_file(path, branch):
    """Load a YAML file from ADO into session state."""
    client = st.session_state.client
    raw = client.get_file(path, branch=branch)
    st.session_state.raw_yaml = raw
    st.session_state.parsed_yaml = parse_yaml(raw)
    st.session_state.selected_file = path


def get_working_branch():
    """Return or create the timestamped working branch."""
    if st.session_state.working_branch:
        return st.session_state.working_branch
    cfg = st.session_state.config
    ts = datetime.now().strftime("%Y%m%d-%H%M%S")
    branch = f"{cfg.branch_prefix}{ts}"
    client = st.session_state.client
    client.create_branch(branch)
    st.session_state.working_branch = branch
    return branch


def group_files_by_folder(files: list[str]) -> dict[str, list[str]]:
    """Group file paths by their parent folder."""
    groups = {}
    for f in files:
        parts = f.rsplit("/", 1)
        folder = parts[0] if len(parts) > 1 else "/"
        groups.setdefault(folder, []).append(f)
    return groups


# ── Sidebar ──────────────────────────────────────────────────────────────────

with st.sidebar:
    st.title("dbt YAML Editor")

    if not st.session_state.connected:
        st.warning("Not connected. Check environment variables.")
        st.stop()

    cfg = st.session_state.config
    st.caption(f"**Repo:** {cfg.org}/{cfg.project}/{cfg.repo}")

    # Branch selector
    branch = st.text_input(
        "Branch",
        value=st.session_state.current_branch,
        help="Branch to browse files from",
    )
    if branch != st.session_state.current_branch:
        st.session_state.current_branch = branch
        st.session_state.selected_file = None
        st.session_state.raw_yaml = None
        st.session_state.parsed_yaml = None
        st.rerun()

    # Working branch indicator
    if st.session_state.working_branch:
        st.success(f"Working branch: `{st.session_state.working_branch}`")

    st.divider()

    # File selector
    files = fetch_yaml_files(st.session_state.client, st.session_state.current_branch)
    st.session_state.yaml_files = files

    if not files:
        st.info("No YAML files found under /models")
    else:
        selected = st.selectbox(
            "Select YAML file",
            options=files,
            index=files.index(st.session_state.selected_file) if st.session_state.selected_file in files else 0,
            format_func=lambda x: x.split("/models/")[-1] if "/models/" in x else x,
        )
        if selected != st.session_state.selected_file:
            load_file(selected, st.session_state.current_branch)
            st.rerun()

    st.divider()

    # ── Create PR button ─────────────────────────────────────────────────
    if st.session_state.working_branch and st.session_state.committed_files:
        st.subheader("Create Pull Request")
        pr_title = st.text_input(
            "PR Title",
            value=f"YAML updates {datetime.now().strftime('%Y-%m-%d')}",
        )
        pr_desc = st.text_area(
            "PR Description",
            value="Updates from dbt YAML Editor:\n- "
            + "\n- ".join(
                f.split("/models/")[-1] if "/models/" in f else f
                for f in st.session_state.committed_files
            ),
            height=100,
        )
        if st.button("Create PR", type="primary", use_container_width=True):
            try:
                client = st.session_state.client
                pr = client.create_pull_request(
                    source_branch=st.session_state.working_branch,
                    title=pr_title,
                    description=pr_desc,
                )
                pr_id = pr.get("pullRequestId")
                pr_url = (
                    f"https://dev.azure.com/{cfg.org}/{cfg.project}"
                    f"/_git/{cfg.repo}/pullrequest/{pr_id}"
                )
                st.session_state.app_prs.append(
                    {
                        "id": pr_id,
                        "title": pr_title,
                        "branch": st.session_state.working_branch,
                        "created": datetime.now().isoformat(),
                        "url": pr_url,
                    }
                )
                # Reset working branch so next commit starts fresh
                st.session_state.working_branch = None
                st.session_state.committed_files = []
                st.success(f"PR #{pr_id} created!")
                st.markdown(f"[View PR]({pr_url})")
            except ADOGitClientError as e:
                st.error(f"Failed to create PR: {e}")

# ── Main area — tabs ─────────────────────────────────────────────────────────

tab_editor, tab_prs = st.tabs(["Editor", "Pull Requests"])

# ═════════════════════════════════════════════════════════════════════════════
# EDITOR TAB
# ═════════════════════════════════════════════════════════════════════════════

with tab_editor:
    if not st.session_state.connected:
        st.stop()

    if st.session_state.parsed_yaml is None:
        st.info("Select a YAML file from the sidebar to begin editing.")
        st.stop()

    parsed = st.session_state.parsed_yaml
    models = get_models(parsed)
    sources = get_sources(parsed)

    # Combine models + source tables for editing
    items = []
    for m in models:
        items.append(("model", m))
    for src in sources:
        for table in src.get("tables", []) or []:
            items.append(("source", table))

    if not items:
        st.warning("No models or sources found in this file.")
        st.stop()

    # Model/source selector
    item_names = [f"{kind}: {item.get('name', '?')}" for kind, item in items]
    selected_idx = st.selectbox(
        "Select model / source",
        range(len(item_names)),
        format_func=lambda i: item_names[i],
    )

    kind, item = items[selected_idx]
    st.markdown(f"### {item.get('name', 'Unnamed')}")

    # ── Table-level fields ────────────────────────────────────────────────

    col1, col2 = st.columns([2, 1])

    with col1:
        desc = st.text_area(
            "Description",
            value=item.get("description", "") or "",
            key=f"desc_{selected_idx}",
            height=100,
        )
        item["description"] = desc

    with col2:
        existing_tags = item.get("tags", []) or []
        if not isinstance(existing_tags, list):
            existing_tags = [existing_tags]
        new_tag = st.text_input("Add tag", key=f"new_tag_{selected_idx}")
        all_tag_options = list(set(existing_tags + ([new_tag] if new_tag else [])))
        tags = st.multiselect(
            "Tags",
            options=all_tag_options,
            default=existing_tags,
            key=f"tags_{selected_idx}",
        )
        item["tags"] = tags if tags else ensure_field(item, "tags", [])
        if tags:
            item["tags"] = tags

    # ── Columns ───────────────────────────────────────────────────────────

    st.markdown("#### Columns")

    columns = get_columns(item)

    if not columns:
        st.caption("No columns defined.")
    else:
        for col_idx, col in enumerate(columns):
            col_name = col.get("name", f"column_{col_idx}")
            with st.expander(f"**{col_name}**", expanded=False):
                # Description
                col_desc = st.text_area(
                    "Description",
                    value=col.get("description", "") or "",
                    key=f"col_desc_{selected_idx}_{col_idx}",
                    height=68,
                )
                col["description"] = col_desc

                # Tags
                c1, c2 = st.columns([1, 2])
                with c1:
                    col_new_tag = st.text_input(
                        "Add tag",
                        key=f"col_new_tag_{selected_idx}_{col_idx}",
                    )
                with c2:
                    col_existing_tags = col.get("tags", []) or []
                    if not isinstance(col_existing_tags, list):
                        col_existing_tags = [col_existing_tags]
                    col_tag_opts = list(
                        set(col_existing_tags + ([col_new_tag] if col_new_tag else []))
                    )
                    col_tags = st.multiselect(
                        "Tags",
                        options=col_tag_opts,
                        default=col_existing_tags,
                        key=f"col_tags_{selected_idx}_{col_idx}",
                    )
                    if col_tags:
                        col["tags"] = col_tags
                    elif "tags" in col:
                        col["tags"] = []

                # Tests
                st.caption("Tests")
                existing_tests = col.get("tests", []) or []
                # Normalize: tests can be strings or dicts
                existing_test_names = []
                for t in existing_tests:
                    if isinstance(t, str):
                        existing_test_names.append(t)
                    elif isinstance(t, dict):
                        existing_test_names.extend(t.keys())

                selected_tests = st.multiselect(
                    "Standard tests",
                    options=STANDARD_TESTS,
                    default=[t for t in existing_test_names if t in STANDARD_TESTS],
                    key=f"col_tests_{selected_idx}_{col_idx}",
                )

                # Handle accepted_values param
                av_values = ""
                if "accepted_values" in selected_tests:
                    # Find existing accepted_values config
                    for t in existing_tests:
                        if isinstance(t, dict) and "accepted_values" in t:
                            vals = t["accepted_values"].get("values", [])
                            av_values = ", ".join(str(v) for v in vals)
                    av_values = st.text_input(
                        "Accepted values (comma-separated)",
                        value=av_values,
                        key=f"av_{selected_idx}_{col_idx}",
                    )

                # Handle relationships param
                rel_to = ""
                rel_field = ""
                if "relationships" in selected_tests:
                    for t in existing_tests:
                        if isinstance(t, dict) and "relationships" in t:
                            rel_to = t["relationships"].get("to", "")
                            rel_field = t["relationships"].get("field", "")
                    rc1, rc2 = st.columns(2)
                    with rc1:
                        rel_to = st.text_input(
                            "Related to (ref)",
                            value=rel_to,
                            key=f"rel_to_{selected_idx}_{col_idx}",
                        )
                    with rc2:
                        rel_field = st.text_input(
                            "Field",
                            value=rel_field,
                            key=f"rel_field_{selected_idx}_{col_idx}",
                        )

                # Rebuild tests list
                new_tests = []
                for t in selected_tests:
                    if t == "accepted_values" and av_values.strip():
                        vals = [v.strip() for v in av_values.split(",") if v.strip()]
                        new_tests.append({"accepted_values": {"values": vals}})
                    elif t == "relationships" and rel_to.strip():
                        new_tests.append(
                            {
                                "relationships": {
                                    "to": rel_to.strip(),
                                    "field": rel_field.strip(),
                                }
                            }
                        )
                    else:
                        new_tests.append(t)

                col["tests"] = new_tests if new_tests else []
                # Clean up empty tests key
                if not col["tests"]:
                    if "tests" in col and not col["tests"]:
                        del col["tests"]

    # ── Raw YAML preview ──────────────────────────────────────────────────

    with st.expander("Raw YAML Preview"):
        new_yaml = dump_yaml(parsed)
        st.code(new_yaml, language="yaml")

    # ── Commit ────────────────────────────────────────────────────────────

    st.divider()

    col_a, col_b = st.columns([3, 1])
    with col_a:
        commit_msg = st.text_input(
            "Commit message",
            value=f"Update {st.session_state.selected_file.split('/')[-1] if st.session_state.selected_file else 'yaml'}",
        )
    with col_b:
        st.write("")  # spacing
        st.write("")
        commit_clicked = st.button("Commit", type="primary", use_container_width=True)

    if commit_clicked:
        new_yaml = dump_yaml(parsed)
        # Check for actual changes
        if new_yaml == st.session_state.raw_yaml:
            st.warning("No changes to commit.")
        else:
            try:
                client = st.session_state.client
                branch = get_working_branch()
                client.push_changes(
                    branch=branch,
                    file_path=st.session_state.selected_file,
                    content=new_yaml,
                    commit_message=commit_msg,
                )
                # Track committed file
                if st.session_state.selected_file not in st.session_state.committed_files:
                    st.session_state.committed_files.append(st.session_state.selected_file)
                # Update raw_yaml so subsequent "no changes" check works
                st.session_state.raw_yaml = new_yaml
                st.success(f"Committed to `{branch}`")
            except ADOConflictError:
                st.error(
                    "Branch was updated by someone else. "
                    "Refresh the file and try again."
                )
            except ADOGitClientError as e:
                st.error(f"Commit failed: {e}")

# ═════════════════════════════════════════════════════════════════════════════
# PULL REQUESTS TAB
# ═════════════════════════════════════════════════════════════════════════════

with tab_prs:
    st.subheader("Pull Requests")

    # Show PRs created in this session
    if st.session_state.app_prs:
        st.markdown("**Created in this session:**")
        for pr in reversed(st.session_state.app_prs):
            col1, col2, col3 = st.columns([3, 1, 1])
            with col1:
                st.markdown(f"[#{pr['id']} — {pr['title']}]({pr['url']})")
            with col2:
                st.caption(pr["created"][:16])
            with col3:
                st.caption(pr["branch"])

    st.divider()

    # Fetch PRs from ADO with branch prefix filter
    if st.session_state.connected:
        if st.button("Refresh PRs from ADO"):
            st.session_state["_refresh_prs"] = True

        try:
            client = st.session_state.client
            cfg = st.session_state.config
            # Get active + completed PRs created by our prefix
            active_prs = client.list_pull_requests(status="active", top=25)
            completed_prs = client.list_pull_requests(status="completed", top=25)
            all_prs = active_prs + completed_prs

            # Filter to branches matching our prefix
            prefix_ref = f"refs/heads/{cfg.branch_prefix}"
            editor_prs = [
                pr for pr in all_prs
                if pr.get("sourceRefName", "").startswith(prefix_ref)
            ]

            if editor_prs:
                st.markdown(f"**PRs from YAML Editor** (`{cfg.branch_prefix}*`):")
                for pr in editor_prs:
                    pr_id = pr["pullRequestId"]
                    title = pr["title"]
                    status = pr["status"]
                    created = pr.get("creationDate", "")[:10]
                    pr_url = (
                        f"https://dev.azure.com/{cfg.org}/{cfg.project}"
                        f"/_git/{cfg.repo}/pullrequest/{pr_id}"
                    )

                    status_label = {
                        "active": "Active",
                        "completed": "Completed",
                        "abandoned": "Abandoned",
                    }.get(status, status)

                    col1, col2, col3 = st.columns([3, 1, 1])
                    with col1:
                        st.markdown(f"[#{pr_id} — {title}]({pr_url})")
                    with col2:
                        st.caption(created)
                    with col3:
                        st.caption(status_label)
            else:
                st.info("No YAML Editor PRs found in ADO.")

        except ADOGitClientError as e:
            st.error(f"Failed to fetch PRs: {e}")
