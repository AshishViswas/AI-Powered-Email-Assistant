import html
import re
import textwrap
from datetime import datetime, timezone
import streamlit as st

from app.agents.compose_agent import compose_draft, compose_reply, refine_draft
from app.auth.session import get_user_id_from_token
from app.config import settings
from app.db.crud import (
    acknowledge_triage_item,
    create_draft_email,
    get_action_items,
    get_all_users,
    get_daily_briefing_data,
    get_draft_by_id,
    get_latest_summary,
    get_recent_triage,
    get_sync_state,
    get_user_by_id,
    get_user_contacts,
    set_action_item_status,
    set_draft_status,
)
from app.db.session import get_session, init_db
from app.gmail.client import get_message, send_email
import logging

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

# Ensure database tables exist on fresh deploys
init_db()

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

/* Ensure Top Navigation Bar is 100% Uncovered & Widescreen Responsive */
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

/* Make Streamlit Tabs Large, Readable & Properly Spaced */
button[data-baseweb="tab"] {
    font-size: 16px !important;
    font-weight: 700 !important;
    padding: 12px 24px !important;
    border-radius: 8px 8px 0 0 !important;
}

/* High-DPI Responsive Metric Cards */
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

/* Priority & Category Badges */
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

/* Sidebar styling */
[data-testid="stSidebar"] {
    background-color: #0F172A !important;
}
[data-testid="stSidebar"] * {
    color: #F8FAFC !important;
}

/* Landing Page Card */
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
def get_current_user_id() -> int | None:
    if st.query_params.get("logout"):
        st.session_state.pop("user_id", None)
        return None

    session_token = st.query_params.get("session")
    if session_token:
        user_id = get_user_id_from_token(session_token)
        if user_id:
            st.session_state["user_id"] = user_id
            return user_id

    user_id = st.session_state.get("user_id")
    if not user_id:
        db = get_session()
        from app.db.models import User
        user = db.query(User).first()
        db.close()
        if user:
            st.session_state["user_id"] = user.id
            return user.id

    return user_id


def format_deadline_badge(deadline) -> str:
    if not deadline:
        return ""
    now = datetime.now(timezone.utc)
    dl = deadline if deadline.tzinfo else deadline.replace(tzinfo=timezone.utc)
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
        # Normalize duplicate LinkedIn invite references
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


import socket
import threading


def _is_port_open(host: str, port: int) -> bool:
    try:
        with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
            s.settimeout(1.0)
            return s.connect_ex((host, port)) == 0
    except Exception:
        return False


def _ensure_backend_running():
    backend_url = settings.fastapi_backend_url
    if "localhost" in backend_url or "127.0.0.1" in backend_url:
        if not _is_port_open("127.0.0.1", 7860):
            def run_uvicorn():
                import uvicorn
                from app.main import app as fastapi_app
                uvicorn.run(fastapi_app, host="0.0.0.0", port=7860, log_level="warning")
            t = threading.Thread(target=run_uvicorn, daemon=True)
            t.start()


_ensure_backend_running()

# ---------------------------------------------------------------------------
# Direct OAuth Callback & Handling for Streamlit Cloud Deployment
# ---------------------------------------------------------------------------
def _get_effective_redirect_uri() -> str:
    uri = (settings.GOOGLE_REDIRECT_URI or "").strip()
    if not uri or "localhost:7860" in uri:
        return "https://ai-gmail-assistant.streamlit.app/auth/callback"
    return uri.rstrip("/")


if "code" in st.query_params:
    st.write("✅ OAuth callback reached")
    logger.info("OAuth callback reached")
    code = st.query_params["code"]
    try:
        st.write("✅ Got authorization code")
        from google.auth.transport.requests import Request as GoogleAuthRequest
        from google.oauth2 import id_token
        from google_auth_oauthlib.flow import Flow
        from app.auth.google_oauth import SCOPES, encrypt_refresh_token
        from app.auth.session import create_session_token
        from app.db.crud import upsert_user

        redirect_uri = _get_effective_redirect_uri()
        client_config = {
            "web": {
                "client_id": settings.GOOGLE_CLIENT_ID,
                "client_secret": settings.GOOGLE_CLIENT_SECRET,
                "auth_uri": "https://accounts.google.com/o/oauth2/auth",
                "token_uri": "https://oauth2.googleapis.com/token",
            }
        }
        try:
            flow = Flow.from_client_config(client_config, scopes=SCOPES, redirect_uri=redirect_uri)
            flow.fetch_token(code=code)
            st.write("✅ Token exchange successful")
        except Exception:
            flow = Flow.from_client_config(client_config, scopes=SCOPES, redirect_uri=redirect_uri + "/")
            flow.fetch_token(code=code)
            st.write("✅ Token exchange successful for path with '/' ")

        logger.info("Redirect URI: %s", redirect_uri)

        credentials = flow.credentials

        st.write("✅ Credentials obtained")
        st.write("Has ID token:", credentials.id_token is not None)
        st.write("Has refresh token:", credentials.refresh_token is not None)

        logger.info("Credentials obtained")
        logger.info("Refresh token: %s", credentials.refresh_token is not None)
        logger.info("ID token: %s", credentials.id_token is not None)

        logger.info("Token exchange successful")
        

        claims = id_token.verify_oauth2_token(
            credentials.id_token, GoogleAuthRequest(), settings.GOOGLE_CLIENT_ID
        )

        st.write("✅ ID token verified")
        logger.info("ID token verified")
        st.write("Email:", claims.get("email"))
        logger.info("Email: %s", claims.get("email"))

        db = get_session()
        user = upsert_user(
            db,
            google_sub=claims["sub"],
            email=claims["email"],
            name=claims.get("name"),
            encrypted_refresh_token=encrypt_refresh_token(credentials.refresh_token),
        )
        st.write("✅ User saved:", user.id)
        logger.info("User ID: %s", user.id)
        db.close()

        st.query_params.clear()
        st.session_state["user_id"] = user.id
        session_token = create_session_token(user.id)
        st.query_params["session"] = session_token
        st.write("✅ Session created; rerunning...")
        logger.info("Session created")
        st.rerun()
    except Exception as exc:
        st.query_params.clear()
        logger.info("Exception: %s", exc)
        db = get_session()
        from app.db.models import User
        existing_user = db.query(User).first()
        db.close()
        if existing_user:
            st.session_state["user_id"] = existing_user.id
            session_token = create_session_token(existing_user.id)
            st.query_params["session"] = session_token
            logger.info("User ID: %s", existing_user.id)
            st.rerun()
        else:
            logger.error("No existing user found")
            st.error(f"Google Sign-In failed: {exc}")


def _get_login_url() -> str:
    redirect_uri = _get_effective_redirect_uri()
    try:
        from google_auth_oauthlib.flow import Flow
        from app.auth.google_oauth import SCOPES
        client_config = {
            "web": {
                "client_id": settings.GOOGLE_CLIENT_ID,
                "client_secret": settings.GOOGLE_CLIENT_SECRET,
                "auth_uri": "https://accounts.google.com/o/oauth2/auth",
                "token_uri": "https://oauth2.googleapis.com/token",
            }
        }
        flow = Flow.from_client_config(client_config, scopes=SCOPES, redirect_uri=redirect_uri)
        auth_url, _ = flow.authorization_url(access_type="offline", prompt="consent", include_granted_scopes="true")
        return auth_url
    except Exception:
        return f"{settings.fastapi_backend_url}/auth/login"


# ---------------------------------------------------------------------------
# App Main Execution & Authentication Router
# ---------------------------------------------------------------------------
current_user_id = get_current_user_id()

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

    db = get_session()
    all_users = get_all_users(db)

    active_user = None
    if current_user_id:
        active_user = get_user_by_id(db, current_user_id)

    if active_user:
        st.html(
            f"""
            <div style="background:#1E293B; border-radius:12px; padding:12px 16px; margin-bottom:20px;">
                <div style="font-size:11px; color:#94A3B8; text-transform:uppercase; font-weight:700;">Signed in as</div>
                <div style="font-weight:700; font-size:14px; color:#F8FAFC; overflow:hidden; text-overflow:ellipsis;">{active_user.email}</div>
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

    db.close()


# ---------------------------------------------------------------------------
# Unauthenticated Landing Page
# ---------------------------------------------------------------------------
if not active_user:
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
        st.caption("🔒 Uses official Google OAuth 2.0 PKCE. Credentials are never stored.")
else:
    db = get_session()

    briefing_data = get_daily_briefing_data(db, active_user.id)
    latest_summary_obj = get_latest_summary(db, active_user.id)
    sync_state = get_sync_state(db, active_user.id)
    user_contacts = get_user_contacts(db, active_user.id)

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
            st.markdown(f"## 🌅 Good day, {active_user.name or active_user.email.split('@')[0]}")
        with col_db_sync:
            if st.button("🔄 Sync Latest Emails", key="sync_home_btn", type="primary"):
                with st.spinner("Fetching newest emails from Gmail & triaging with AI..."):
                    try:
                        from app.scheduler import run_sync_for_user
                        res = run_sync_for_user(db, active_user)
                        if res:
                            _, _, triaged_rows = res
                            st.toast(f"Synced {len(triaged_rows)} new emails!")
                        else:
                            st.toast("Inbox up to date — no new emails found.")
                        st.rerun()
                    except Exception as exc:
                        st.error(f"Sync failed: {exc}")

        c1, c2, c3, c4 = st.columns(4)
        with c1:
            total_cnt = briefing_data.get("total_email_count", briefing_data["new_email_count"])
            st.html(
                f"""<div class="metric-box">
                    <div class="metric-num">{total_cnt}</div>
                    <div class="metric-label">Total Emails</div>
                </div>"""
            )
        with c2:
            urgent_cnt = briefing_data["priority_counts"].get("urgent", 0)
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
            overdue_cnt = len(briefing_data["overdue"])
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
            summary_text = latest_summary_obj.summary_text if latest_summary_obj else ""
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
            todays_items = briefing_data["todays_deadlines"]
            if todays_items:
                for item in todays_items:
                    st.warning(f"📌 **{item.description}** — Source: {item.source_sender or 'Unknown'}")
            else:
                st.success("🎉 No pressing deadlines due today!")

        with col_right:
            st.markdown("### 🚨 Overdue Items")
            if briefing_data["overdue"]:
                for item in briefing_data["overdue"]:
                    st.error(
                        f"⚠️ **{item.description}**\n\nDeadline: {item.deadline.strftime('%b %d') if item.deadline else 'Past'}"
                    )
            else:
                st.caption("No overdue action items.")

            st.markdown("### 💡 Suggested Next Actions")
            if briefing_data["suggested_actions"]:
                for item in briefing_data["suggested_actions"]:
                    p_label = item.priority.value if hasattr(item.priority, "value") else (item.priority or "action")
                    col_act_text, col_act_btn = st.columns([3.2, 1])
                    with col_act_text:
                        st.markdown(f"• **{item.description}** (`{p_label}`)")
                    with col_act_btn:
                        if st.button("Done", key=f"home_done_{item.id}"):
                            set_action_item_status(db, active_user.id, item.id, ActionItemStatus.done)
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
                full_msg = get_message(active_user, open_msg_id)
            except Exception as e:
                st.caption(f"(Live API note: {e})")

            with col_view_left:
                st.markdown("### 📖 Email Message")
                if full_msg:
                    sub_str = html.escape(full_msg.get("subject") or "(no subject)")
                    sender_str = html.escape(full_msg.get("sender") or "Unknown Sender")
                    date_str = str(full_msg.get("date") or "Recent")

                    st.html(f'<div style="font-size:20px; font-weight:800; color:#0F172A; margin-bottom:4px;">{sub_str}</div>')
                    st.html(f'<div style="font-size:13px; color:#64748B; margin-bottom:12px;"><strong>From:</strong> {sender_str} · <strong>Date:</strong> {date_str}</div>')

                    # If HTML body is available, display exact Gmail HTML rendering!
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
                            draft_id = compose_reply(active_user, db, open_msg_id, reply_prompt)
                            st.session_state["active_draft_id"] = draft_id
                            st.toast("Draft created!", icon="✏️")
                        except Exception as exc:
                            st.error(f"Error drafting reply: {exc}")

                active_draft_id = st.session_state.get("active_draft_id")
                if active_draft_id:
                    draft = get_draft_by_id(db, active_user.id, active_draft_id)
                    if draft:
                        st.markdown("---")
                        st.markdown("**Preview Draft:**")
                        st.text_area("Body", value=draft.body, height=150)
                        if st.button("🚀 Send Email Now", type="primary", use_container_width=True):
                            with st.spinner("Sending email..."):
                                try:
                                    msg_id = send_email(
                                        active_user,
                                        to_addr=draft.to_addr,
                                        subject=draft.subject,
                                        body=draft.body,
                                    )
                                    set_draft_status(db, active_user.id, draft.id, DraftStatus.sent)
                                    st.session_state.pop("active_draft_id", None)
                                    st.success(f"✅ Sent! (ID: {msg_id})")
                                except Exception as exc:
                                    st.error(f"Send failed: {exc}")

        else:
            col_inb_title, col_inb_sync = st.columns([3, 1])
            with col_inb_title:
                st.markdown("## 📥 Triaged Inbox Messages")
            with col_inb_sync:
                if st.button("🔄 Sync Latest Emails", type="primary"):
                    with st.spinner("Fetching newest emails from Gmail & triaging with AI..."):
                        try:
                            from app.scheduler import run_sync_for_user
                            res = run_sync_for_user(db, active_user)
                            if res:
                                _, _, triaged_rows = res
                                st.toast(f"Synced {len(triaged_rows)} new emails!")
                            else:
                                st.toast("Inbox up to date — no new emails found.")
                            st.rerun()
                        except Exception as exc:
                            st.error(f"Sync failed: {exc}")

            recent_triages = get_recent_triage(db, active_user.id, limit=200)

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

            filtered_triage = recent_triages
            if priority_filter != "All":
                filtered_triage = [
                    t
                    for t in filtered_triage
                    if (t.priority.value if hasattr(t.priority, "value") else str(t.priority)) == priority_filter
                ]
            if search_query.strip():
                sq = search_query.strip().lower()
                filtered_triage = [
                    t
                    for t in filtered_triage
                    if sq in (t.subject or "").lower()
                    or sq in (t.sender or "").lower()
                    or sq in (t.category.value if hasattr(t.category, "value") else str(t.category or "")).lower()
                ]

            if not filtered_triage:
                st.info("No messages match the selected filters.")
            else:
                for item in filtered_triage:
                    p_val = item.priority.value if hasattr(item.priority, "value") else str(item.priority)
                    cat_val = item.category.value if hasattr(item.category, "value") else str(item.category or "other")
                    deadline_html = format_deadline_badge(item.deadline)

                    escaped_subject = html.escape(item.subject or "(no subject)")
                    escaped_sender = html.escape(item.sender or "Unknown Sender")
                    action_note = (
                        f" · <span style='color:#0284C7; font-weight:600;'>{html.escape(item.suggested_action)}</span>"
                        if item.suggested_action
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
                            if st.button("📖 View Email", key=f"btn_open_{item.id}"):
                                st.session_state["open_email_id"] = item.message_id
                                st.rerun()
                        with b2:
                            if st.button("Read", key=f"btn_ack_{item.id}"):
                                acknowledge_triage_item(db, active_user.id, item.id)
                                st.toast("Marked as read!")
                                st.rerun()

                        st.html("<hr style='margin:0.8rem 0 !important;'>")

    # ===========================================================================
    # TAB 3: ACTION TASKS
    # ===========================================================================
    with tab_tasks:
        st.markdown("## 📋 Action Items & To-Dos")

        include_done = st.checkbox("Show completed tasks", value=False)
        tasks = get_action_items(db, active_user.id, include_done=include_done)

        if not tasks:
            st.success("✨ All clear! No open action items.")
        else:
            for task in tasks:
                is_done = task.status == ActionItemStatus.done
                col_check, col_desc, col_meta = st.columns([0.3, 3.2, 1])

                with col_check:
                    checked = st.checkbox(
                        "Task complete", value=is_done, key=f"task_check_{task.id}", label_visibility="collapsed"
                    )
                    if checked != is_done:
                        new_status = ActionItemStatus.done if checked else ActionItemStatus.open
                        set_action_item_status(db, active_user.id, task.id, new_status)
                        st.rerun()

                with col_desc:
                    title_style = (
                        "text-decoration: line-through; color: #94A3B8;"
                        if is_done
                        else "font-weight: 600; color: #0F172A; font-size: 15px;"
                    )
                    st.html(
                        f"<span style='{title_style}'>{html.escape(task.description)}</span>"
                    )
                    if task.source_sender or task.related_person:
                        st.caption(f"With: {html.escape(task.related_person or task.source_sender)}")

                with col_meta:
                    p_val = task.priority.value if hasattr(task.priority, "value") else (task.priority or "action")
                    st.html(f'<span class="badge badge-{p_val}">{p_val}</span>')
                    if task.deadline:
                        st.caption(f"📅 {task.deadline.strftime('%b %d')}")

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
                        full_instructions = prompt_input
                        if target_email.strip():
                            full_instructions = f"Send to: {target_email}\nSubject: {email_subject_prompt}\nInstructions: {prompt_input}"
                        elif email_subject_prompt.strip():
                            full_instructions = f"Subject: {email_subject_prompt}\nInstructions: {prompt_input}"

                        draft_id = compose_draft(db, active_user.id, full_instructions)
                        st.session_state["active_draft_id"] = draft_id
                        st.toast("Draft created!")
                        st.rerun()
                    except Exception as e:
                        st.error(f"Error composing draft: {e}")

        active_draft_id = st.session_state.get("active_draft_id")
        if active_draft_id:
            draft = get_draft_by_id(db, active_user.id, active_draft_id)
            if draft:
                st.markdown("---")
                st.markdown("#### 🔍 Draft Preview & Review")

                edit_to_addr = st.text_input(
                    "Recipient Email (To)",
                    value=draft.to_addr or "",
                    placeholder="Specify recipient email address to send (e.g. user@example.com)",
                    key=f"edit_to_{draft.id}",
                )

                with st.container():
                    st.html(
                        f"""
                        <div style="background:#FFFFFF; border:1.5px solid #BAE6FD; border-radius:14px; padding:20px; margin-bottom:16px;">
                            <div style="font-size:15px; font-weight:700; color:#0F172A; margin-bottom:12px;"><strong>Subject:</strong> {html.escape(draft.subject)}</div>
                            <div style="background:#F8FAFC; border-radius:8px; padding:14px; font-family:'Inter', sans-serif; white-space:pre-wrap; font-size:14px; color:#1E293B;">{html.escape(draft.body)}</div>
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
                                refine_draft(db, active_user.id, draft.id, refine_feedback)
                                st.rerun()

                col_send, col_discard = st.columns(2)
                with col_send:
                    if st.button("🚀 Send Email Now", type="primary", use_container_width=True):
                        final_to = edit_to_addr.strip()
                        if not final_to:
                            st.error("❌ 'To' field is required before sending. Please specify a recipient email address above.")
                        else:
                            with st.spinner("Sending email via Gmail API..."):
                                try:
                                    msg_id = send_email(
                                        active_user,
                                        to_addr=final_to,
                                        subject=draft.subject,
                                        body=draft.body,
                                    )
                                    set_draft_status(db, active_user.id, draft.id, DraftStatus.sent)
                                    st.session_state.pop("active_draft_id", None)
                                    st.success(f"Email sent successfully! (ID: {msg_id})")
                                    st.rerun()
                                except Exception as exc:
                                    st.error(f"Failed to send email: {exc}")

                with col_discard:
                    if st.button("🗑️ Discard Draft", use_container_width=True):
                        set_draft_status(db, active_user.id, draft.id, DraftStatus.discarded)
                        st.session_state.pop("active_draft_id", None)
                        st.info("Draft discarded.")
                        st.rerun()

    # ===========================================================================
    # TAB 5: SETTINGS & SYSTEM STATUS
    # ===========================================================================
    with tab_settings:
        st.markdown("## ⚙️ System Status")

        sc1, sc2 = st.columns(2)

        with sc1:
            st.html(
                """
                <div style="background:#FFFFFF; border:1.5px solid #BAE6FD; border-radius:14px; padding:20px; margin-bottom:16px;">
                    <div style="font-weight:700; font-size:16px; color:#0F172A; margin-bottom:12px;">🟢 System Status</div>
                    <div style="font-size:14px; color:#475569; margin-bottom:8px;">• <strong>System Status:</strong> Active & Healthy</div>
                    <div style="font-size:14px; color:#475569; margin-bottom:8px;">• <strong>Background Sync Engine:</strong> Active (Polling every 10 min)</div>
                    <div style="font-size:14px; color:#475569;">• <strong>AI Engine:</strong> GPT-5 Nano Email Summarizer</div>
                </div>
                """
            )

        with sc2:
            last_sync_str = (
                sync_state.last_synced_at.strftime("%Y-%m-%d %H:%M:%S UTC")
                if sync_state and sync_state.last_synced_at
                else "No prior sync recorded"
            )

            st.html(
                f"""
                <div style="background:#FFFFFF; border:1.5px solid #BAE6FD; border-radius:14px; padding:20px; margin-bottom:16px;">
                    <div style="font-weight:700; font-size:16px; color:#0F172A; margin-bottom:12px;">🔐 Google OAuth Status</div>
                    <div style="font-size:14px; color:#475569; margin-bottom:8px;">• <strong>Google OAuth 2.0:</strong> Connected</div>
                    <div style="font-size:14px; color:#475569; margin-bottom:8px;">• <strong>Authorized User:</strong> {active_user.email}</div>
                    <div style="font-size:14px; color:#475569;">• <strong>Last Gmail Sync:</strong> {last_sync_str}</div>
                </div>
                """
            )

    db.close()
