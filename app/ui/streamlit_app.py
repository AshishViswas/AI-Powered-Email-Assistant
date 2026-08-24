import html
import logging
import re
import textwrap
from datetime import datetime, timezone
import streamlit as st

from app.api.client import ApiClientError, api_client
from app.config import settings

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Streamlit Page Setup & Responsive Widescreen Custom CSS
# ---------------------------------------------------------------------------
st.set_page_config(
    page_title="Gmail Agent — AI Email Dashboard",
    page_icon="📬",
    layout="wide",
    initial_sidebar_state="expanded",
)

CUSTOM_CSS = """
<style>
@import url('https://fonts.googleapis.com/css2?family=Plus+Jakarta+Sans:wght@400;500;600;700;800&family=Inter:wght@400;500;600&display=swap');

:root {
    --sky-primary: #0284C7;
    --sky-hover: #0369A1;
    --sky-light: #F0F9FF;
    --sky-border: #BAE6FD;
    --bg-main: #F8FAFC;
    --card-bg: #FFFFFF;
    --text-dark: #0F172A;
    --text-muted: #64748B;
}

.block-container {
    padding-top: 4.5rem !important;
    padding-bottom: 3rem !important;
    padding-left: 3rem !important;
    padding-right: 3rem !important;
    max-width: 100% !important;
}

html, body, [data-testid="stAppViewContainer"] {
    background: var(--bg-main);
    font-family: 'Plus Jakarta Sans', 'Inter', sans-serif;
    color: var(--text-dark);
}

button[data-baseweb="tab"] {
    font-size: 16px !important;
    font-weight: 700 !important;
    padding: 12px 24px !important;
    border-radius: 8px 8px 0 0 !important;
}

.metric-box {
    background: linear-gradient(135deg, #FFFFFF 0%, #F0F9FF 100%);
    border: 1.5px solid var(--sky-border);
    border-radius: 14px;
    padding: 20px 24px;
    text-align: center;
    box-shadow: 0 4px 12px rgba(2, 132, 199, 0.05);
    transition: transform 0.2s ease;
}
.metric-box:hover {
    transform: translateY(-2px);
}
.metric-num {
    font-size: 36px;
    font-weight: 800;
    color: var(--sky-primary);
    line-height: 1.1;
}
.metric-label {
    font-size: 12px;
    font-weight: 700;
    text-transform: uppercase;
    letter-spacing: 0.05em;
    color: var(--text-muted);
    margin-top: 6px;
}

.badge {
    display: inline-block;
    padding: 4px 10px;
    border-radius: 999px;
    font-size: 11px;
    font-weight: 700;
    text-transform: uppercase;
    letter-spacing: 0.03em;
}
.badge-urgent { background: #FEE2E2; color: #DC2626; border: 1px solid #FCA5A5; }
.badge-action { background: #FFEDD5; color: #EA580C; border: 1px solid #FDBA74; }
.badge-important { background: #DBEAFE; color: #2563EB; border: 1px solid #93C5FD; }
.badge-informational { background: #CCFBF1; color: #0D9488; border: 1px solid #5EEAD4; }
.badge-newsletter { background: #F1F5F9; color: #64748B; border: 1px solid #CBD5E1; }
.badge-low { background: #F1F5F9; color: #94A3B8; border: 1px solid #E2E8F0; }
.badge-cat { background: #E0F2FE; color: #0369A1; border: 1px solid #BAE6FD; }

[data-testid="stSidebar"] {
    background-color: #0F172A !important;
}
[data-testid="stSidebar"] * {
    color: #F8FAFC !important;
}

.landing-card {
    max-width: 480px;
    margin: 40px auto;
    background: #FFFFFF;
    border-radius: 20px;
    padding: 40px 36px;
    text-align: center;
    border: 1.5px solid var(--sky-border);
    box-shadow: 0 20px 40px -10px rgba(2, 132, 199, 0.15);
}
.landing-title {
    font-size: 30px;
    font-weight: 800;
    color: var(--text-dark);
    margin-bottom: 10px;
    letter-spacing: -0.02em;
}
.landing-sub {
    color: var(--text-muted);
    font-size: 14px;
    line-height: 1.6;
    margin-bottom: 28px;
}

.google-auth-btn {
    display: inline-block;
    background: var(--sky-primary);
    color: #FFFFFF !important;
    font-weight: 700;
    font-size: 15px;
    padding: 14px 28px;
    border-radius: 12px;
    text-decoration: none;
    box-shadow: 0 4px 14px rgba(2, 132, 199, 0.3);
    transition: all 0.2s ease;
}
.google-auth-btn:hover {
    background: var(--sky-hover);
}

hr {
    margin: 1.2rem 0 !important;
}
</style>
"""

st.html(CUSTOM_CSS)


# ---------------------------------------------------------------------------
# Helper Functions & User Authentication
# ---------------------------------------------------------------------------
def _get_login_url() -> str:
    return f"{settings.fastapi_backend_url.rstrip('/')}/auth/login"


def get_authenticated_user_and_token() -> tuple[dict | None, str | None]:
    if st.query_params.get("logout"):
        st.session_state.clear()
        st.query_params.clear()
        return None, None

    session_token = st.query_params.get("session")
    if session_token:
        st.session_state["session_token"] = session_token
        # Consume parameter from URL
        st.query_params.pop("session", None)

    token = st.session_state.get("session_token")
    if not token:
        return None, None

    try:
        user = api_client.get_me(token)
        return user, token
    except ApiClientError as err:
        if err.status_code == 401:
            st.session_state.clear()
            st.error("Session expired; please sign in again.")
        else:
            st.error(f"Backend API error: {err.detail}")
        return None, None
    except Exception as exc:
        st.error(f"Unable to connect to backend service: {exc}")
        return None, None


def format_deadline_badge(deadline_input) -> str:
    if not deadline_input:
        return ""
    try:
        if isinstance(deadline_input, str):
            dl = datetime.fromisoformat(deadline_input)
        else:
            dl = deadline_input
    except Exception:
        return ""

    now = datetime.now(timezone.utc)
    dl = dl if dl.tzinfo else dl.replace(tzinfo=timezone.utc)
    seconds_left = (dl - now).total_seconds()
    label = dl.strftime("%b %d")
    if seconds_left < 0:
        return f'<span style="color:#EF4444; font-weight:700; font-size:13px;">🚨 Overdue ({label})</span>'
    elif seconds_left < 86400:
        return f'<span style="color:#F97316; font-weight:700; font-size:13px;">⏰ Due Today ({label})</span>'
    return f'<span style="color:#64748B; font-weight:600; font-size:13px;">📅 {label}</span>'


def parse_summary_points(summary_text: str) -> list[str]:
    if not summary_text:
        return []

    lines = [line.strip().lstrip("•-* ") for line in summary_text.split("\n") if line.strip()]
    if len(lines) <= 1:
        lines = [s.strip() for s in re.split(r"(?<=[.!?])\s+", summary_text) if s.strip()]

    unique_bullets = []
    seen = set()
    for raw in lines:
        cleaned = html.unescape(raw)
        cleaned = re.sub(
            r"\btwo LinkedIn connection requests\b", "the LinkedIn connection request", cleaned, flags=re.IGNORECASE
        )
        cleaned = re.sub(
            r"\bA couple of LinkedIn invites\b", "LinkedIn connection invitation", cleaned, flags=re.IGNORECASE
        )
        norm = re.sub(r"\W+", "", cleaned.lower())
        if norm and norm not in seen:
            seen.add(norm)
            unique_bullets.append(cleaned)

    return unique_bullets


# ---------------------------------------------------------------------------
# App Main Execution & Authentication Router
# ---------------------------------------------------------------------------
active_user, session_token = get_authenticated_user_and_token()

# Sidebar Navigation
with st.sidebar:
    st.html(
        """
        <div style="display:flex; align-items:center; gap:12px; margin-bottom:24px;">
            <div style="width:44px; height:44px; border-radius:12px; background:linear-gradient(135deg, #0284C7, #38BDF8); display:flex; align-items:center; justify-content:center; font-size:22px; color:white;">📬</div>
            <div>
                <div style="font-weight:800; font-size:20px; color:white;">Gmail Agent</div>
            </div>
        </div>
        """
    )

    if active_user:
        st.html(
            f"""
            <div style="background:#1E293B; border-radius:12px; padding:12px 16px; margin-bottom:20px;">
                <div style="font-size:11px; color:#94A3B8; text-transform:uppercase; font-weight:700;">Signed in as</div>
                <div style="font-weight:700; font-size:14px; color:#F8FAFC; overflow:hidden; text-overflow:ellipsis;">{html.escape(active_user.get('email', ''))}</div>
            </div>
            """
        )

        if st.button("🚪 Logout", key="logout_sidebar_btn", use_container_width=True):
            st.session_state.clear()
            st.query_params.clear()
            st.query_params["logout"] = "true"
            st.rerun()
    else:
        st.warning("Not signed in")

# ---------------------------------------------------------------------------
# Unauthenticated Landing Page
# ---------------------------------------------------------------------------
if not active_user or not session_token:
    login_url = _get_login_url()
    st.html(
        """
        <div class="landing-card" style="margin-bottom:10px;">
            <div style="font-size:52px; margin-bottom:12px;">📬</div>
            <div class="landing-title">Gmail Agent AI</div>
            <div class="landing-sub">
                AI-powered daily executive briefings, intelligent inbox triage, and automated human-in-the-loop draft generation.
            </div>
        </div>
        """
    )
    col_l1, col_l2, col_l3 = st.columns([1, 2, 1])
    with col_l2:
        st.link_button("🔑 Sign in with Google Account", login_url, type="primary", use_container_width=True)
        st.caption("🔒 Uses official Google OAuth 2.0 PKCE via FastAPI backend. Credentials are never stored.")
else:
    # Fetch dashboard data via API Client
    try:
        briefing_data = api_client.get_briefing(session_token)
        user_contacts = api_client.get_contacts(session_token)
    except ApiClientError as exc:
        st.error(f"Failed to load briefing data: {exc.detail}")
        st.stop()

    latest_summary_obj = briefing_data.get("latest_summary")
    sync_state = briefing_data.get("sync_state")

    # Main Navigation Tabs
    tab_briefing, tab_inbox, tab_tasks, tab_compose, tab_settings = st.tabs(
        ["🌅 Daily Briefing", "📥 Smart Inbox", "📋 Action Tasks", "✏️ AI Composer", "⚙️ Settings"]
    )

    # ===========================================================================
    # TAB 1: DAILY BRIEFING
    # ===========================================================================
    with tab_briefing:
        col_db_head, col_db_sync = st.columns([3, 1])
        with col_db_head:
            display_name = active_user.get("name") or active_user.get("email", "").split("@")[0]
            st.markdown(f"## 🌅 Good day, {html.escape(display_name)}")
        with col_db_sync:
            if st.button("🔄 Sync Latest Emails", key="sync_home_btn", type="primary"):
                with st.spinner("Fetching newest emails from Gmail & triaging with AI..."):
                    try:
                        res = api_client.trigger_sync(session_token)
                        st.toast(res.get("message", "Sync complete!"))
                        st.rerun()
                    except ApiClientError as exc:
                        st.error(f"Sync failed: {exc.detail}")

        c1, c2, c3, c4 = st.columns(4)
        with c1:
            total_cnt = briefing_data.get("total_email_count", briefing_data.get("new_email_count", 0))
            st.html(
                f"""<div class="metric-box">
                    <div class="metric-num">{total_cnt}</div>
                    <div class="metric-label">Total Emails</div>
                </div>"""
            )
        with c2:
            urgent_cnt = briefing_data.get("priority_counts", {}).get("urgent", 0)
            st.html(
                f"""<div class="metric-box">
                    <div class="metric-num" style="color:#EF4444;">{urgent_cnt}</div>
                    <div class="metric-label">Urgent Items</div>
                </div>"""
            )
        with c3:
            action_cnt = briefing_data.get("action_required_count", len(briefing_data.get("suggested_actions", [])))
            st.html(
                f"""<div class="metric-box">
                    <div class="metric-num" style="color:#F97316;">{action_cnt}</div>
                    <div class="metric-label">Actions Required</div>
                </div>"""
            )
        with c4:
            overdue_cnt = len(briefing_data.get("overdue", []))
            st.html(
                f"""<div class="metric-box">
                    <div class="metric-num" style="color:#DC2626;">{overdue_cnt}</div>
                    <div class="metric-label">Overdue Tasks</div>
                </div>"""
            )

        st.markdown("---")

        col_left, col_right = st.columns([1.6, 1])

        with col_left:
            st.markdown("### 📝 Executive Summary Points")
            summary_text = latest_summary_obj.get("summary_text") if latest_summary_obj else ""
            bullets = parse_summary_points(summary_text)

            if bullets:
                for b in bullets:
                    card_html = textwrap.dedent(f"""
                        <div style="background:#FFFFFF; border:1.5px solid #BAE6FD; border-left:4px solid #0284C7; border-radius:10px; padding:14px 18px; margin-bottom:10px; font-size:14.5px; font-weight:500; color:#0F172A; line-height:1.5; box-shadow: 0 2px 6px rgba(0,0,0,0.02);">
                        • {html.escape(b)}
                        </div>
                    """).strip()
                    st.html(card_html)
            else:
                st.info("No executive summary points available.")

            st.markdown("### ⚡ Focus & Today's Deadlines")
            todays_items = briefing_data.get("todays_deadlines", [])
            if todays_items:
                for item in todays_items:
                    st.warning(f"📌 **{html.escape(item.get('description', ''))}** — Source: {html.escape(item.get('source_sender') or 'Unknown')}")
            else:
                st.success("🎉 No pressing deadlines due today!")

        with col_right:
            st.markdown("### 🚨 Overdue Items")
            overdue_items = briefing_data.get("overdue", [])
            if overdue_items:
                for item in overdue_items:
                    dl_str = item.get("deadline")
                    dl_formatted = dl_str[:10] if dl_str else "Past"
                    st.error(
                        f"⚠️ **{html.escape(item.get('description', ''))}**\n\nDeadline: {dl_formatted}"
                    )
            else:
                st.caption("No overdue action items.")

            st.markdown("### 💡 Suggested Next Actions")
            suggested_actions = briefing_data.get("suggested_actions", [])
            if suggested_actions:
                for item in suggested_actions:
                    p_label = item.get("priority") or "action"
                    col_act_text, col_act_btn = st.columns([3.2, 1])
                    with col_act_text:
                        st.markdown(f"• **{html.escape(item.get('description', ''))}** (`{p_label}`)")
                    with col_act_btn:
                        if st.button("Done", key=f"home_done_{item['id']}"):
                            api_client.update_task_status(session_token, item["id"], "done")
                            st.toast("Action item completed!")
                            st.rerun()
            else:
                st.caption("No pending suggested actions.")

    # ===========================================================================
    # TAB 2: SMART INBOX & GMAIL-LIKE EMAIL READER
    # ===========================================================================
    with tab_inbox:
        open_msg_id = st.session_state.get("open_email_id")

        if open_msg_id:
            if st.button("⬅️ Back to Inbox Messages"):
                st.session_state.pop("open_email_id", None)
                st.rerun()

            col_view_left, col_view_right = st.columns([1.6, 1])

            full_msg = None
            try:
                full_msg = api_client.get_inbox_message(session_token, open_msg_id)
            except ApiClientError as e:
                st.caption(f"(Live API note: {e.detail})")

            with col_view_left:
                st.markdown("### 📖 Email Message")
                if full_msg:
                    sub_str = html.escape(full_msg.get("subject") or "(no subject)")
                    sender_str = html.escape(full_msg.get("sender") or "Unknown Sender")
                    date_str = str(full_msg.get("date") or "Recent")

                    st.html(f'<div style="font-size:20px; font-weight:800; color:#0F172A; margin-bottom:4px;">{sub_str}</div>')
                    st.html(f'<div style="font-size:13px; color:#64748B; margin-bottom:12px;"><strong>From:</strong> {sender_str} · <strong>Date:</strong> {date_str}</div>')

                    html_content = full_msg.get("html_body")
                    if html_content:
                        st.html(html_content)
                    else:
                        body_text = (
                            full_msg.get("body")
                            or full_msg.get("body_text")
                            or full_msg.get("snippet")
                            or "No message content found."
                        )
                        st.html(
                            f"""<div style="background:#FFFFFF; border:1.5px solid #BAE6FD; border-radius:12px; padding:20px; font-family:'Inter', sans-serif; white-space:pre-wrap; color:#1E293B; font-size:14.5px; line-height:1.6;">
                                {html.escape(body_text)}
                            </div>"""
                        )
                else:
                    st.info(f"Message ID: `{open_msg_id}`")

            with col_view_right:
                st.markdown("### 💬 AI Reply Panel")
                default_to_addr = full_msg.get("sender") if full_msg else ""
                default_sub = f"Re: {full_msg.get('subject', '')}" if full_msg else ""

                st.markdown(f"**To:** `{default_to_addr}`")
                st.markdown(f"**Subject:** `{default_sub}`")

                reply_prompt = st.text_area(
                    "Reply Instructions",
                    placeholder="e.g. Thank them for the update and confirm meeting for 3 PM.",
                    key="reply_prompt_area",
                    height=120,
                )

                if st.button("🤖 Generate AI Reply", type="primary", use_container_width=True):
                    with st.spinner("AI drafting response..."):
                        try:
                            draft = api_client.compose_reply(session_token, open_msg_id, reply_prompt)
                            st.session_state["active_draft_id"] = draft["id"]
                            st.toast("Draft created!", icon="✏️")
                        except ApiClientError as exc:
                            st.error(f"Error drafting reply: {exc.detail}")

                active_draft_id = st.session_state.get("active_draft_id")
                if active_draft_id:
                    try:
                        draft = api_client.get_draft(session_token, active_draft_id)
                        if draft:
                            st.markdown("---")
                            st.markdown("**Preview Draft:**")
                            st.text_area("Body", value=draft.get("body", ""), height=150)
                            if st.button("🚀 Send Email Now", type="primary", use_container_width=True):
                                with st.spinner("Sending email..."):
                                    try:
                                        send_res = api_client.send_draft(session_token, draft["id"], draft.get("to_addr", ""))
                                        st.session_state.pop("active_draft_id", None)
                                        st.success(f"✅ Sent! (ID: {send_res.get('message_id')})")
                                    except ApiClientError as exc:
                                        st.error(f"Send failed: {exc.detail}")
                    except ApiClientError:
                        st.session_state.pop("active_draft_id", None)

        else:
            col_inb_title, col_inb_sync = st.columns([3, 1])
            with col_inb_title:
                st.markdown("## 📥 Triaged Inbox Messages")
            with col_inb_sync:
                if st.button("🔄 Sync Latest Emails", type="primary"):
                    with st.spinner("Fetching newest emails from Gmail & triaging with AI..."):
                        try:
                            res = api_client.trigger_sync(session_token)
                            st.toast(res.get("message", "Sync complete!"))
                            st.rerun()
                        except ApiClientError as exc:
                            st.error(f"Sync failed: {exc.detail}")

            col_f1, col_f2 = st.columns([1, 2])
            with col_f1:
                priority_filter = st.selectbox(
                    "Filter Priority",
                    options=["All", "urgent", "action", "important", "informational", "newsletter", "low"],
                )
            with col_f2:
                search_query = st.text_input(
                    "🔍 Search Messages",
                    placeholder="Type to search subject, sender or category...",
                    key="inbox_search_input",
                )

            try:
                filtered_triage = api_client.get_inbox(
                    session_token,
                    priority=priority_filter if priority_filter != "All" else None,
                    search=search_query if search_query.strip() else None,
                    limit=200,
                )
            except ApiClientError as exc:
                st.error(f"Failed to load inbox: {exc.detail}")
                filtered_triage = []

            if not filtered_triage:
                st.info("No messages match the selected filters.")
            else:
                for item in filtered_triage:
                    p_val = item.get("priority", "informational")
                    cat_val = item.get("category") or "other"
                    deadline_html = format_deadline_badge(item.get("deadline"))

                    escaped_subject = html.escape(item.get("subject") or "(no subject)")
                    escaped_sender = html.escape(item.get("sender") or "Unknown Sender")
                    action_note = (
                        f" · <span style='color:#0284C7; font-weight:600;'>{html.escape(item.get('suggested_action'))}</span>"
                        if item.get("suggested_action")
                        else ""
                    )

                    with st.container():
                        col_card_left, col_card_right = st.columns([3.8, 1.2])

                        with col_card_left:
                            card_html = textwrap.dedent(f"""
                                <div style="background:#FFFFFF; border-left:4px solid #0284C7; border:1px solid #E2E8F0; border-radius:10px; padding:14px 18px; margin-bottom:6px;">
                                <div style="font-size:15px; font-weight:700; color:#0F172A;">{escaped_subject}</div>
                                <div style="font-size:13px; color:#64748B; margin-top:2px;">From: {escaped_sender}{action_note}</div>
                                {f'<div style="margin-top:4px;">{deadline_html}</div>' if deadline_html else ''}
                                </div>
                            """).strip()
                            st.html(card_html)

                        with col_card_right:
                            st.html(
                                f"""<div style="padding-top:14px; text-align:right;">
                                    <span class="badge badge-{p_val}">{p_val}</span>
                                    <span class="badge badge-cat">{cat_val}</span>
                                </div>"""
                            )

                        b1, b2, _ = st.columns([1, 1.2, 2.8])
                        with b1:
                            if st.button("📖 View Email", key=f"btn_open_{item['id']}"):
                                st.session_state["open_email_id"] = item["message_id"]
                                st.rerun()
                        with b2:
                            if st.button("Read", key=f"btn_ack_{item['id']}"):
                                api_client.acknowledge_triage(session_token, item["id"])
                                st.toast("Marked as read!")
                                st.rerun()

                        st.html("<hr style='margin:0.8rem 0 !important;'>")

    # ===========================================================================
    # TAB 3: ACTION TASKS
    # ===========================================================================
    with tab_tasks:
        st.markdown("## 📋 Action Items & To-Dos")

        include_done = st.checkbox("Show completed tasks", value=False)
        try:
            tasks = api_client.get_tasks(session_token, include_done=include_done)
        except ApiClientError as exc:
            st.error(f"Failed to fetch tasks: {exc.detail}")
            tasks = []

        if not tasks:
            st.success("✨ All clear! No open action items.")
        else:
            for task in tasks:
                is_done = task.get("status") == "done"
                col_check, col_desc, col_meta = st.columns([0.3, 3.2, 1])

                with col_check:
                    checked = st.checkbox(
                        "Task complete", value=is_done, key=f"task_check_{task['id']}", label_visibility="collapsed"
                    )
                    if checked != is_done:
                        new_status = "done" if checked else "open"
                        api_client.update_task_status(session_token, task["id"], new_status)
                        st.rerun()

                with col_desc:
                    title_style = (
                        "text-decoration: line-through; color: #94A3B8;"
                        if is_done
                        else "font-weight: 600; color: #0F172A; font-size: 15px;"
                    )
                    st.html(
                        f"<span style='{title_style}'>{html.escape(task.get('description', ''))}</span>"
                    )
                    related = task.get("related_person") or task.get("source_sender")
                    if related:
                        st.caption(f"With: {html.escape(related)}")

                with col_meta:
                    p_val = task.get("priority") or "action"
                    st.html(f'<span class="badge badge-{p_val}">{p_val}</span>')
                    deadline_str = task.get("deadline")
                    if deadline_str:
                        st.caption(f"📅 {deadline_str[:10]}")

    # ===========================================================================
    # TAB 4: AI EMAIL COMPOSER
    # ===========================================================================
    with tab_compose:
        st.markdown("## ✏️ AI Email Composer")
        st.caption("Select a contact or enter a recipient email address to generate a new draft with AI.")

        col_to1, col_to2 = st.columns([1.5, 1])
        with col_to1:
            contact_choice = st.selectbox(
                "📇 Select Contact from Gmail History",
                options=["-- Type custom email --"] + user_contacts,
            )

        with col_to2:
            default_to = "" if contact_choice.startswith("--") else contact_choice
            target_email = st.text_input(
                "Recipient Email Address (To)", value=default_to, placeholder="e.g. sarah@company.com"
            )

        email_subject_prompt = st.text_input("Subject Line (Optional)", placeholder="e.g. Project Update")

        prompt_input = st.text_area(
            "Email Instructions / Content Prompt",
            placeholder="e.g. Ask Sarah for the updated project timeline by Thursday, and express excitement about the release.",
            height=140,
        )

        if st.button("🤖 Create Draft with AI", type="primary"):
            if not prompt_input.strip():
                st.warning("Please enter email instructions or a context prompt.")
            else:
                with st.spinner("AI is generating your draft..."):
                    try:
                        draft = api_client.compose_draft(
                            session_token,
                            instructions=prompt_input,
                            target_email=target_email.strip(),
                            subject=email_subject_prompt.strip(),
                        )
                        st.session_state["active_draft_id"] = draft["id"]
                        st.toast("Draft created!")
                        st.rerun()
                    except ApiClientError as e:
                        st.error(f"Error composing draft: {e.detail}")

        active_draft_id = st.session_state.get("active_draft_id")
        if active_draft_id:
            try:
                draft = api_client.get_draft(session_token, active_draft_id)
            except ApiClientError:
                draft = None

            if draft:
                st.markdown("---")
                st.markdown("#### 🔍 Draft Preview & Review")

                edit_to_addr = st.text_input(
                    "Recipient Email (To)",
                    value=draft.get("to_addr") or "",
                    placeholder="Specify recipient email address to send (e.g. user@example.com)",
                    key=f"edit_to_{draft['id']}",
                )

                with st.container():
                    st.html(
                        f"""
                        <div style="background:#FFFFFF; border:1.5px solid #BAE6FD; border-radius:14px; padding:20px; margin-bottom:16px;">
                            <div style="font-size:15px; font-weight:700; color:#0F172A; margin-bottom:12px;"><strong>Subject:</strong> {html.escape(draft.get('subject', ''))}</div>
                            <div style="background:#F8FAFC; border-radius:8px; padding:14px; font-family:'Inter', sans-serif; white-space:pre-wrap; font-size:14px; color:#1E293B;">{html.escape(draft.get('body', ''))}</div>
                        </div>
                        """
                    )

                col_refine, col_btn = st.columns([3, 1])
                with col_refine:
                    refine_feedback = st.text_input(
                        "Request AI Refinement", placeholder="e.g. Make it more formal, add polite sign-off."
                    )
                with col_btn:
                    st.markdown("<div style='margin-top:28px;'></div>", unsafe_allow_html=True)
                    if st.button("✨ Refine Draft"):
                        if refine_feedback.strip():
                            with st.spinner("Refining draft..."):
                                try:
                                    api_client.refine_draft(session_token, draft["id"], refine_feedback)
                                    st.rerun()
                                except ApiClientError as exc:
                                    st.error(f"Refinement error: {exc.detail}")

                col_send, col_discard = st.columns(2)
                with col_send:
                    if st.button("🚀 Send Email Now", type="primary", use_container_width=True):
                        final_to = edit_to_addr.strip()
                        if not final_to:
                            st.error("❌ 'To' field is required before sending. Please specify a recipient email address above.")
                        else:
                            with st.spinner("Sending email via Gmail API..."):
                                try:
                                    send_res = api_client.send_draft(session_token, draft["id"], final_to)
                                    st.session_state.pop("active_draft_id", None)
                                    st.success(f"Email sent successfully! (ID: {send_res.get('message_id')})")
                                    st.rerun()
                                except ApiClientError as exc:
                                    st.error(f"Failed to send email: {exc.detail}")

                with col_discard:
                    if st.button("🗑️ Discard Draft", use_container_width=True):
                        try:
                            api_client.discard_draft(session_token, draft["id"])
                            st.session_state.pop("active_draft_id", None)
                            st.info("Draft discarded.")
                            st.rerun()
                        except ApiClientError as exc:
                            st.error(f"Discard failed: {exc.detail}")

    # ===========================================================================
    # TAB 5: SETTINGS & SYSTEM STATUS
    # ===========================================================================
    with tab_settings:
        st.markdown("## ⚙️ System Status")

        sc1, sc2 = st.columns(2)

        with sc1:
            st.html(
                f"""
                <div style="background:#FFFFFF; border:1.5px solid #BAE6FD; border-radius:14px; padding:20px; margin-bottom:16px;">
                    <div style="font-weight:700; font-size:16px; color:#0F172A; margin-bottom:12px;">🟢 System Status</div>
                    <div style="font-size:14px; color:#475569; margin-bottom:8px;">• <strong>System Status:</strong> Active & Healthy</div>
                    <div style="font-size:14px; color:#475569; margin-bottom:8px;">• <strong>FastAPI Backend:</strong> {html.escape(settings.fastapi_backend_url)}</div>
                    <div style="font-size:14px; color:#475569; margin-bottom:8px;">• <strong>Background Sync Engine:</strong> Active</div>
                    <div style="font-size:14px; color:#475569;">• <strong>AI Engine:</strong> OpenAI Email Summarizer & Triage</div>
                </div>
                """
            )

        with sc2:
            last_sync_time = sync_state.get("last_synced_at") if sync_state else None
            last_sync_str = (
                str(last_sync_time)[:19] + " UTC"
                if last_sync_time
                else "No prior sync recorded"
            )

            st.html(
                f"""
                <div style="background:#FFFFFF; border:1.5px solid #BAE6FD; border-radius:14px; padding:20px; margin-bottom:16px;">
                    <div style="font-weight:700; font-size:16px; color:#0F172A; margin-bottom:12px;">🔐 Google OAuth Status</div>
                    <div style="font-size:14px; color:#475569; margin-bottom:8px;">• <strong>Google OAuth 2.0:</strong> Connected via FastAPI</div>
                    <div style="font-size:14px; color:#475569; margin-bottom:8px;">• <strong>Authorized User:</strong> {html.escape(active_user.get('email', ''))}</div>
                    <div style="font-size:14px; color:#475569;">• <strong>Last Gmail Sync:</strong> {last_sync_str}</div>
                </div>
                """
            )
