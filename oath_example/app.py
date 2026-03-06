# app.py
import streamlit as st
import plotly.express as px
import pandas as pd
from ado_client import get_credential, get_ado_connection, fetch_work_items, ADO_SCOPE
from teams import load_teams

st.set_page_config(page_title="ADO Dashboard", layout="wide")
# st.title("Data Modeling Management Dashboard")

# --- Session state for auth ---
if "credential" not in st.session_state:
    st.session_state.credential = None

teams = load_teams()
team_names = list(teams.keys())

# --- Sidebar: Config ---
with st.sidebar:
    st.header("Configuration")
    org_url = st.text_input("Org URL", value="https://dev.azure.com/HOLMAN")
    project = st.text_input("Project", value="IT")

    if st.session_state.credential is None:
        if st.button("Sign In with Microsoft"):
            try:
                cred = get_credential()
                cred.get_token(ADO_SCOPE)
                st.session_state.credential = cred
                st.rerun()
            except Exception as e:
                st.error(f"Authentication failed: {e}")
    else:
        st.success("Signed in")
        if st.button("Sign Out"):
            st.session_state.credential = None
            st.rerun()

    st.divider()
    st.header("Timeframe")
    date_range = st.date_input("Date Range", value=[
        pd.Timestamp.now() - pd.Timedelta(days=180),
        pd.Timestamp.now()
    ])

    st.divider()
    selected_team = st.selectbox("Team", team_names if team_names else ["(No teams configured)"])

# --- Data Fetch (cached) ---
@st.cache_data(ttl=300)
def load_data(org_url, token, project, start_date, end_date, area_paths, members):
    conn = get_ado_connection(org_url, token)

    clauses = []
    if area_paths:
        area_clause = " OR ".join(
            f"[System.AreaPath] UNDER '{project}\\{ap}'" for ap in area_paths
        )
        clauses.append(f"({area_clause})")
    if members:
        member_clause = " OR ".join(
            f"[System.AssignedTo] = '{m}'" for m in members
        )
        clauses.append(f"({member_clause})")

    if not clauses:
        return pd.DataFrame(columns=[
            "id", "state", "assigned_to", "type",
            "created_date", "closed_date",
            "area_path", "title", "tags", "comment_count",
        ])

    combined = " OR ".join(clauses)
    wiql = f"""
        SELECT [System.Id] FROM WorkItems
        WHERE [System.TeamProject] = '{project}'
        AND ({combined})
        AND [System.CreatedDate] >= '{start_date}'
        AND [System.CreatedDate] <= '{end_date}'
        ORDER BY [System.CreatedDate] DESC
    """
    return fetch_work_items(conn, project, wiql)


st.title(f"Management Dashboard: {selected_team}")

if st.session_state.credential and project and selected_team and selected_team != "(No teams configured)":
    token = st.session_state.credential.get_token(ADO_SCOPE).token

    team_config = teams[selected_team]
    team_members = team_config.get("members", [])
    team_areas = team_config.get("areas", [])

    df = load_data(
        org_url, token, project,
        str(date_range[0]), str(date_range[1]),
        tuple(team_areas), tuple(team_members)
    )

    # Derive tags_list
    df["tags_list"] = df["tags"].apply(
        lambda t: [x.strip() for x in t.split(";") if x.strip()] if t else []
    )

    # Build team lookup
    member_to_team = {}
    for tn, tc in teams.items():
        for m in tc.get("members", []):
            member_to_team[m] = tn
    df["team"] = df["assigned_to"].map(member_to_team).fillna("Unassigned")

    # Compute ticket age
    now = pd.Timestamp.now(tz="UTC")
    df["age_days"] = (now - df["created_date"]).dt.days

    # Determine if ticket is in designated areas
    def is_in_designated_areas(area_path):
        for ap in team_areas:
            full_path = f"{project}\\{ap}"
            if area_path == full_path or area_path.startswith(full_path + "\\"):
                return True
        return False

    df["in_designated_area"] = df["area_path"].apply(is_in_designated_areas)

    # Extract display area (last segment or child area)
    df["display_area"] = df["area_path"].apply(lambda p: p.split("\\")[-1] if p else "Unknown")

    # Main filtered data (in designated areas)
    filtered = df[df["in_designated_area"]]

    # --- KPI Row ---
    closed_items = filtered.dropna(subset=["closed_date"])
    days_to_close = closed_items.apply(
        lambda r: (r["closed_date"] - r["created_date"]).days, axis=1
    )
    median_days = days_to_close.median() if len(days_to_close) > 0 else None

    def count_area(keyword):
        return len(filtered[filtered["area_path"].str.contains(keyword, case=False, na=False)])

    total_tickets = len(filtered)
    active_tickets = len(filtered[filtered["state"].isin(["Approved", "Active", "In Progress", "New", "Created", "Evaluate"])])
    closed_tickets = len(closed_items)

    # Aging KPIs
    open_for_aging = filtered[~filtered["state"].isin(["Closed", "Resolved", "Done", "Complete"])]
    over_2_weeks_count = len(open_for_aging[open_for_aging["age_days"] > 14])
    over_month_count = len(open_for_aging[open_for_aging["age_days"] > 30])

    # Wrong area count
    wrong_area_count = len(df[
        (~df["in_designated_area"]) &
        (df["assigned_to"].isin(team_members)) &
        (df["state"].isin(["New", "Created", "Evaluate", "Approved", "Active", "In Progress"]))
    ])

    # Non-area KPIs
    all_kpis = [
        ("Total", total_tickets),
        ("Active", active_tickets),
        ("Closed", closed_tickets),
    ]

    all_kpis.append(("Median Days", round(median_days, 1) if pd.notna(median_days) else "N/A"))
    all_kpis.append(("> 2 Weeks", over_2_weeks_count))
    all_kpis.append(("> Month", over_month_count))
    all_kpis.append(("Wrong Area", wrong_area_count))

    # FleetTrack only for Data Modeling team
    if selected_team == "Data Modeling":
        fleet_track_tickets = len(filtered[filtered["tags_list"].apply(lambda t: "Fleet Track" in t)])
        all_kpis.append(("FleetTrack", fleet_track_tickets))

    # Area KPIs from team config
    for ap in team_areas:
        area_name = ap.split("\\")[-1] if "\\" in ap else ap
        all_kpis.append((area_name, count_area(area_name)))

    cols = st.columns(len(all_kpis))
    for col, (label, value) in zip(cols, all_kpis):
        col.metric(label, value)

    # --- Tickets Over Time (Bar Chart, full width) ---
    st.subheader("Tickets Over Time (Created vs Closed)")
    created_ts = (filtered.set_index("created_date")
                  .resample("W").size().reset_index(name="created"))
    closed_ts = (filtered.dropna(subset=["closed_date"])
                 .set_index("closed_date")
                 .resample("W").size().reset_index(name="closed"))
    ts = created_ts.merge(closed_ts, left_on="created_date",
                          right_on="closed_date", how="outer").fillna(0)
    ts["date"] = ts["created_date"].combine_first(ts["closed_date"])
    ts_melted = ts.melt(id_vars=["date"], value_vars=["created", "closed"],
                        var_name="series", value_name="count")
    fig = px.bar(ts_melted, x="date", y="count", color="series", barmode="stack",
                 labels={"count": "Count", "date": "Week", "series": ""})
    fig.update_layout(xaxis_title="Week", yaxis_title="Count")
    st.plotly_chart(fig, use_container_width=True)

    # --- Tickets Older Than 2 Weeks & Tickets by Area ---
    col_old, col_area = st.columns([2, 1])

    with col_old:
        st.subheader("Old Tickets")
        open_states = [s for s in filtered["state"].unique() if s not in ["Closed", "Resolved", "Done", "Complete"]]
        old_tickets = filtered[(filtered["state"].isin(open_states)) & (filtered["age_days"] > 14) & (filtered["assigned_to"].isin(team_members))]
        if not old_tickets.empty:
            # old_display = old_tickets[["id", "title", "assigned_to", "state", "created_date", "age_days"]].copy()
            # old_display.columns = ["ID", "Title", "Assigned To", "State", "Created", "Age (Days)"]
            # old_display["Created"] = old_display["Created"].dt.strftime("%Y-%m-%d")
            old_display = old_tickets[["id", "title", "assigned_to", "state", "age_days"]].copy()
            old_display.columns = ["ID", "Title", "Assigned To", "State", "Age (Days)"]
            old_display = old_display.sort_values("Age (Days)", ascending=False)
            old_display["ID"] = old_display["ID"].apply(lambda x: f"{org_url}/{project}/_workitems/edit/{x}")
            st.dataframe(old_display, use_container_width=True, hide_index=True,
                column_config={"ID": st.column_config.LinkColumn(display_text=r"(\d+)$")})
        else:
            st.info("No open tickets older than 2 weeks.")

    with col_area:
        st.subheader("Tickets by Area")
        if not filtered.empty:
            area_counts = filtered["display_area"].value_counts().reset_index()
            area_counts.columns = ["Area", "Count"]
            area_counts = area_counts.head(15)
            fig_area = px.pie(area_counts, names="Area", values="Count", hole=0.4)
            fig_area.update_traces(textposition="inside", textinfo="label+value")
            fig_area.update_layout(showlegend=False)
            st.plotly_chart(fig_area, use_container_width=True)
        else:
            st.info("No items to display.")

    # --- Team Summary Table ---
    st.divider()
    st.subheader("Team Summary")

    if team_members:
        member_names = sorted(set(team_members) & set(filtered["assigned_to"].dropna().unique()))
    else:
        member_names = sorted(filtered["assigned_to"].dropna().unique())
    team_rows = []

    new_states = ["New", "Created"]
    evaluate_states = ["Evaluate"]
    active_group_states = ["Approved", "Active", "In Progress"]
    complete_states = ["Closed", "Resolved", "Done", "Complete"]
    blocked_states = ["Blocked"]

    for m in member_names:
        m_df = filtered[filtered["assigned_to"] == m]
        m_closed = m_df[m_df["state"].isin(complete_states)]
        m_days = m_closed.apply(
            lambda r: (r["closed_date"] - r["created_date"]).days if pd.notna(r["closed_date"]) else None, axis=1
        ).dropna()

        m_open = m_df[~m_df["state"].isin(complete_states)]
        older_week = len(m_open[m_open["age_days"] > 14])
        older_month = len(m_open[m_open["age_days"] > 30])

        avg_comments = m_df["comment_count"].mean() if "comment_count" in m_df.columns and m_df["comment_count"].notna().any() else None

        team_rows.append({
            "Member": m,
            # "Team": member_to_team.get(m, "Unassigned"),
            "Created": int(len(m_df[m_df["state"].isin(new_states)])),
            "Evaluate": int(len(m_df[m_df["state"].isin(evaluate_states)])),
            "Active": int(len(m_df[m_df["state"].isin(active_group_states)])),
            "Complete": int(len(m_closed)),
            "Blocked": int(len(m_df[m_df["state"].isin(blocked_states)])),
            "Avg Days to Complete": round(m_days.mean(), 1) if len(m_days) > 0 else "N/A",
            "Avg Comments/Ticket": round(avg_comments, 1) if pd.notna(avg_comments) else "N/A",
            "> 2 Weeks": int(older_week),
            "> Month": int(older_month),
        })

    team_summary_df = pd.DataFrame(team_rows)

    # Style with bright yellow/red text on aging columns
    def style_aging(df):
        styles = pd.DataFrame('', index=df.index, columns=df.columns)
        if "> 2 Weeks" in df.columns:
            styles["> 2 Weeks"] = df["> 2 Weeks"].apply(
                lambda v: "color: #FFD700; font-weight: bold" if v > 0 else ""
            )
        if "> Month" in df.columns:
            styles["> Month"] = df["> Month"].apply(
                lambda v: "color: #FF0000; font-weight: bold" if v > 0 else ""
            )
        return styles

    styled_summary = team_summary_df.style.apply(style_aging, axis=None).format({
        "Avg Days to Complete": lambda x: f"{x:.1f}" if isinstance(x, (int, float)) else x,
        "Avg Comments/Ticket": lambda x: f"{x:.1f}" if isinstance(x, (int, float)) else x,
    })
    st.dataframe(styled_summary, use_container_width=True, hide_index=True)

    # --- Team Ticket Details ---
    st.divider()
    st.subheader("Team Ticket Details")

    if team_members:
        all_members = sorted(set(team_members) & set(filtered["assigned_to"].dropna().unique()))
    else:
        all_members = sorted(filtered["assigned_to"].dropna().unique())
    selected_member = st.selectbox("Select Team Member", ["All"] + list(all_members))

    if selected_member == "All":
        if team_members:
            detail_df = filtered[filtered["assigned_to"].isin(team_members)].copy()
        else:
            detail_df = filtered.copy()
    else:
        detail_df = filtered[filtered["assigned_to"] == selected_member].copy()

    state_order = ["New", "Created", "Evaluate", "Approved", "Active", "In Progress", "Blocked"]

    # Filter to only states in state_order
    detail_df = detail_df[detail_df["state"].isin(state_order)]

    # Combine Active, Approved, In Progress -> "Active"
    detail_df["display_status"] = detail_df["state"].replace({
        "Approved": "Active",
        "In Progress": "Active",
    })

    # Sort by age descending
    detail_df = detail_df.sort_values("age_days", ascending=False)

    if not detail_df.empty:
        # display_df = detail_df[["id", "title", "assigned_to", "display_status", "type", "area_path", "created_date", "age_days", "comment_count", "tags"]].copy()
        # display_df.columns = ["ID", "Title", "Assigned To", "Status", "Type", "Area Path", "Created", "Age (Days)", "Comments", "Tags"]
        display_df = detail_df[["id", "title", "assigned_to", "display_status", "type", "area_path", "created_date", "age_days", "comment_count"]].copy()
        display_df.columns = ["ID", "Title", "Assigned To", "Status", "Type", "Area Path", "Created", "Age (Days)", "Comments"]
        display_df["Created"] = display_df["Created"].dt.strftime("%Y-%m-%d")

        def style_old_rows(row):
            if row["Age (Days)"] > 30:
                return ["color: #FF0000; font-weight: bold"] * len(row)
            return [""] * len(row)

        display_df["ID"] = display_df["ID"].apply(lambda x: f"{org_url}/{project}/_workitems/edit/{x}")
        styled_detail = display_df.style.apply(style_old_rows, axis=1)
        st.dataframe(styled_detail, use_container_width=True, hide_index=True,
            column_config={"ID": st.column_config.LinkColumn(display_text=r"(\d+)$")})
    else:
        st.info("No active tickets to display.")

    # --- Tickets Not in Designated Areas ---
    st.divider()
    st.subheader("Team Tickets Outside Designated Areas")

    active_states = ["New", "Created", "Evaluate", "Approved", "Active", "In Progress"]
    outside_df = df[
        (~df["in_designated_area"]) &
        (df["assigned_to"].isin(team_members)) &
        (df["state"].isin(active_states))
    ].copy()

    if not outside_df.empty:
        outside_df = outside_df.sort_values("age_days", ascending=False)
        outside_display = outside_df[["id", "title", "assigned_to", "state", "area_path", "created_date", "age_days"]].copy()
        outside_display.columns = ["ID", "Title", "Assigned To", "Status", "Area Path", "Created", "Age (Days)"]
        outside_display["Created"] = outside_display["Created"].dt.strftime("%Y-%m-%d")
        outside_display["ID"] = outside_display["ID"].apply(lambda x: f"{org_url}/{project}/_workitems/edit/{x}")
        st.dataframe(outside_display, use_container_width=True, hide_index=True,
            column_config={"ID": st.column_config.LinkColumn(display_text=r"(\d+)$")})
    else:
        st.info("No active team tickets outside designated areas.")

else:
    st.info("Sign in with your Microsoft account and select a team to get started.")
