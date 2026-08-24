import html
import re
from datetime import datetime, timedelta, timezone

import gradio as gr

from app.agents.compose_agent import compose_draft, compose_reply, refine_draft
from app.auth.session import get_user_id_from_request
from app.config import settings
from app.db.crud import (
    count_sent_drafts_since,
    get_action_items,
    get_daily_briefing_data,
    get_draft_by_id,
    get_latest_summary,
    get_recent_sent_drafts_to,
    get_recent_triage,
    get_sync_state,
    get_user_by_id,
    set_action_item_status,
    set_draft_status,
)
from app.db.models import ActionItemStatus, DraftStatus
from app.db.session import get_session
from app.gmail.client import get_message, send_email
from app.scheduler import run_sync_for_user

NO_SUMMARY_YET_TEXT = "No summary yet. New mail is picked up automatically in the background."
MAX_SENDS_PER_HOUR = 20
SEND_COUNTDOWN_SECONDS = 6

# Persistent pages (in nav + section order) plus the contextual "reply" page,
# which is never a persistent nav destination — see design plan section 0.
SECTIONS = ["briefing", "inbox", "tasks", "compose", "draft", "reply"]
NAV_ITEM_BASE = ["sidebar-nav-item"]
NAV_ITEM_ACTIVE = ["sidebar-nav-item", "active"]

PRIORITY_ORDER = ["urgent", "action", "important", "informational", "newsletter", "low"]
PRIORITY_STYLE = {
    "urgent": ("#E2574C", "\U0001F534 Urgent"),
    "action": ("#F0A15C", "\U0001F7E0 Action"),
    "important": ("#2F9BE0", "\U0001F7E1 Important"),
    "informational": ("#2FBF9D", "\U0001F535 Info"),
    "newsletter": ("#94A3B8", "⚪ Newsletter"),
    "low": ("#94A3B8", "\U0001F5D1 Low"),
}
PRIORITY_RADIO_CHOICES = [("All", "all")] + [
    (PRIORITY_STYLE[p][1], p) for p in PRIORITY_ORDER
]

CATEGORY_LABELS = {
    "security": "Security",
    "billing": "Billing",
    "shipping": "Shipping",
    "travel": "Travel",
    "promotional": "Promotional",
    "newsletter": "Newsletter",
    "spam": "Spam",
    "personal": "Personal",
    "work": "Work",
    "other": "Other",
}

LOGIN_HERO_HTML = """
<div class="login-hero-card">
  <div class="login-logo"><span></span></div>
  <h1>Gmail Agent</h1>
  <p>AI-powered inbox summaries, action items, and drafted replies &mdash; every reply is reviewed by you before anything sends.</p>
  <a class="google-signin-btn" href="/auth/login">
    <span class="google-g">G</span>
    <span>Continue with Google</span>
  </a>
</div>
"""

PAGE_HEADER_HTML = {
    "briefing": (
        "<div class='page-header'><span class='eyebrow'>Briefing</span>"
        "<h2>{greeting}</h2>"
        "<p>Your inbox, summarized automatically every few hours.</p>"
        "<div class='horizon-rule'></div></div>"
    ),
    "inbox": (
        "<div class='page-header'><span class='eyebrow'>Inbox</span>"
        "<h2>Everything, sorted</h2>"
        "<p>Every email from recent syncs, triaged by priority.</p>"
        "<div class='horizon-rule'></div></div>"
    ),
    "tasks": (
        "<div class='page-header'><span class='eyebrow'>Tasks</span>"
        "<h2>Open items</h2>"
        "<p>What you owe people, until it's done or dismissed.</p>"
        "<div class='horizon-rule'></div></div>"
    ),
    "compose": (
        "<div class='page-header'><span class='eyebrow'>Compose</span>"
        "<h2>Write a new email</h2>"
        "<p>Describe what you want to send, in plain language.</p>"
        "<div class='horizon-rule'></div></div>"
    ),
    "draft": (
        "<div class='page-header'><span class='eyebrow'>Review</span>"
        "<h2>Check before sending</h2>"
        "<p>Refine as many times as you like &mdash; sending is always your call.</p>"
        "<div class='horizon-rule'></div></div>"
    ),
    "reply": (
        "<div class='page-header'><span class='eyebrow'>Reply</span>"
        "<h2>Draft a reply</h2>"
        "<p>Nothing sends until you review and approve it below.</p>"
        "<div class='horizon-rule'></div></div>"
    ),
}

CUSTOM_CSS = """
@import url('https://fonts.googleapis.com/css2?family=Manrope:wght@500;700;800&family=Inter:wght@400;500;600&display=swap');

:root {
    /* "Dawn over the inbox" palette: twilight sidebar, sky-blue focus,
       sunrise amber for urgency, sea teal for success. */
    --twilight: #0B1E3D;
    --twilight-soft: #16294C;
    --sky: #2F9BE0;
    --sky-hover: #217EBA;
    --sky-tint: #EAF4FC;
    --sunrise: #F0A15C;
    --sunrise-soft: #FCE3C9;
    --sea: #2FBF9D;
    --sea-soft: #D6F5EC;
    --coral: #E2574C;
    --coral-soft: #FBDEDB;
    --ink: #10243E;
    --ink-muted: #5C7A9C;
    /* Bug #0 fix: any dark/colored card must use these explicitly on every
       child, never rely on inheriting from a lighter ancestor rule. */
    --on-dark: #FFFFFF;
    --on-dark-muted: #B9CDE8;
    --border: #DCE8F5;
    --bg: #F3F8FD;
    --card: #FFFFFF;
}

html, body { margin: 0 !important; padding: 0 !important; }

.gradio-container {
    background: var(--bg) !important;
    max-width: 100% !important;
    width: 100% !important;
    margin: 0 !important;
    padding: 0 !important;
    font-family: 'Inter', -apple-system, BlinkMacSystemFont, "Segoe UI", Roboto, sans-serif !important;
    color: var(--ink) !important;
}

h1, h2, h3, .page-header h2 {
    font-family: 'Manrope', 'Inter', sans-serif !important;
}

footer { display: none !important; }

.app-shell {
    gap: 0 !important;
    min-height: 100vh !important;
    flex-wrap: nowrap !important;
}

/* ---------- Sidebar ---------- */
.sidebar {
    background: var(--twilight) !important;
    padding: 24px 18px !important;
    border-radius: 0 !important;
    align-self: stretch !important;
    min-height: 100vh !important;
    display: flex !important;
    flex-direction: column !important;
}
/* Every sidebar text element sets on-dark color explicitly (bug #0 rule),
   not via inheriting from .sidebar alone. */
.sidebar h1, .sidebar h2, .sidebar h3, .sidebar p, .sidebar span, .sidebar label {
    color: var(--on-dark-muted) !important;
}

.brand-row {
    display: flex !important;
    align-items: center !important;
    gap: 10px !important;
    padding: 4px 6px 22px 6px !important;
}
.brand-mark {
    width: 30px; height: 30px; border-radius: 9px; flex-shrink: 0;
    background: linear-gradient(135deg, var(--sky) 0%, var(--sunrise) 100%);
}
.brand-name {
    font-family: 'Manrope', sans-serif !important;
    font-weight: 800 !important;
    font-size: 15.5px !important;
    color: var(--on-dark) !important;
    letter-spacing: 0.2px;
}

.nav-group { display: flex !important; flex-direction: column !important; gap: 3px !important; }
.nav-divider {
    height: 1px;
    background: rgba(255,255,255,0.1);
    margin: 12px 6px;
}

.sidebar-nav-item button {
    width: 100% !important;
    justify-content: flex-start !important;
    text-align: left !important;
    background: transparent !important;
    border: none !important;
    border-left: 3px solid transparent !important;
    color: var(--on-dark-muted) !important;
    border-radius: 8px !important;
    padding: 10px 12px !important;
    font-family: 'Inter', sans-serif !important;
    font-weight: 500 !important;
    font-size: 14px !important;
    box-shadow: none !important;
    transition: background 0.12s ease, color 0.12s ease !important;
}
.sidebar-nav-item button:hover {
    background: var(--twilight-soft) !important;
    color: var(--on-dark) !important;
}
.sidebar-nav-item.active button {
    background: var(--twilight-soft) !important;
    color: var(--on-dark) !important;
    border-left: 3px solid var(--sunrise) !important;
    font-weight: 600 !important;
}

.sidebar-footer {
    margin-top: auto !important;
    padding-top: 14px !important;
    border-top: 1px solid rgba(255,255,255,0.08) !important;
}
.sidebar-footer p, .sidebar-footer span { font-size: 12.5px !important; }
.logout-link a {
    color: #8CA6C6 !important;
    font-size: 12.5px !important;
    text-decoration: none !important;
}
.logout-link a:hover { color: var(--on-dark) !important; text-decoration: underline !important; }

/* ---------- Main area ---------- */
.main-area {
    padding: 36px 40px !important;
    width: 100% !important;
    max-width: 980px !important;
}

.page-header { margin-bottom: 4px !important; }
.page-header .eyebrow {
    display: inline-block;
    font-family: 'Manrope', sans-serif;
    font-size: 11.5px;
    font-weight: 700;
    letter-spacing: 0.08em;
    text-transform: uppercase;
    color: var(--sky);
    margin-bottom: 6px;
}
.page-header h2 {
    margin: 0 0 4px 0 !important;
    font-size: 25px !important;
    font-weight: 800 !important;
    color: var(--ink) !important;
}
.page-header p { margin: 0 !important; font-size: 13.5px !important; color: var(--ink-muted) !important; }
.horizon-rule {
    height: 3px;
    width: 64px;
    border-radius: 999px;
    background: linear-gradient(90deg, var(--sky) 0%, var(--sunrise) 100%);
    margin: 14px 0 6px 0;
}

.sync-status { font-size: 12.5px !important; color: var(--ink-muted) !important; margin: 0 0 18px 0 !important; }

/* ---------- Stat cards (dark strip, light cards inside — bug #0 safe:
   no paragraph text sits directly on the dark background) ---------- */
.stat-strip {
    background: var(--twilight);
    border-radius: 14px;
    padding: 16px;
    margin-bottom: 22px;
    display: flex;
    gap: 12px;
    flex-wrap: wrap;
}
.stat-card {
    background: var(--card);
    border-radius: 10px;
    padding: 14px 10px;
    flex: 1 1 130px;
    text-align: center;
}
.stat-card .stat-number { font-family: 'Manrope', sans-serif; font-size: 26px; font-weight: 800; line-height: 1.2; }
.stat-card .stat-label {
    font-size: 11px; color: var(--ink-muted); text-transform: uppercase;
    letter-spacing: 0.05em; margin-top: 2px; font-weight: 600;
}

.section-card {
    background: var(--card) !important;
    border: 1px solid var(--border) !important;
    border-radius: 14px !important;
    padding: 22px 24px !important;
    margin-bottom: 20px !important;
    box-shadow: 0 1px 2px rgba(16, 36, 62, 0.03) !important;
}
.section-card .card-eyebrow {
    font-family: 'Manrope', sans-serif !important;
    font-size: 11px !important;
    font-weight: 700 !important;
    letter-spacing: 0.07em !important;
    text-transform: uppercase !important;
    color: var(--ink-muted) !important;
    margin-bottom: 10px !important;
}
.section-card .hint-text { color: var(--ink-muted) !important; font-size: 12.5px !important; margin-top: 10px !important; }

.briefing-columns { display: flex !important; gap: 20px !important; flex-wrap: wrap !important; }
.briefing-columns > * { flex: 1 1 260px !important; min-width: 240px !important; }
.briefing-mini-card ul { margin: 0; padding-left: 18px; font-size: 13px; line-height: 1.8; color: var(--ink); }
.briefing-mini-card li { margin-bottom: 2px; }
.digest-card { color: var(--ink-muted) !important; font-size: 13px !important; }

/* ---------- Flush HTML: strip Gradio's default block chrome (gray
   background/border/padding) from HTML components used purely to inject
   content into a parent card — without this, every gr.HTML nested inside a
   Group renders its own nested "field" box and looks disabled/placeholder-y
   even when it has real content. ---------- */
.flush-html,
.flush-html > div,
.flush-html .block {
    background: transparent !important;
    border: none !important;
    box-shadow: none !important;
    padding: 0 !important;
    min-height: 0 !important;
}

/* ---------- Priority filter chips (styled gr.Radio) ---------- */
.priority-filter .wrap { display: flex !important; flex-wrap: wrap !important; gap: 8px !important; }
.priority-filter label {
    border-radius: 999px !important;
    border: 1px solid var(--border) !important;
    padding: 6px 14px !important;
    background: #FFFFFF !important;
    font-size: 12.5px !important;
    color: var(--ink-muted) !important;
}
.priority-filter input[type=radio] { display: none !important; }
.priority-filter label:has(input:checked) {
    background: var(--sky-tint) !important;
    border-color: var(--sky) !important;
    color: var(--sky-hover) !important;
    font-weight: 600 !important;
}
.filter-row { gap: 10px !important; align-items: center !important; margin-bottom: 14px !important; }

/* ---------- Card-list rows (replacing the spreadsheet-style Dataframe) ---------- */
.card-row {
    background: var(--card) !important;
    border: 1px solid var(--border) !important;
    border-left: 4px solid var(--border) !important;
    border-radius: 10px !important;
    align-items: center !important;
    gap: 0 !important;
    position: relative !important;
    margin-bottom: 8px !important;
    min-height: 58px !important;
    transition: box-shadow .12s ease, background .12s ease !important;
}
.card-row:hover { background: var(--sky-tint) !important; box-shadow: 0 2px 10px rgba(16,36,62,0.07) !important; }
.card-row.priority-urgent { border-left-color: var(--coral) !important; }
.card-row.priority-action { border-left-color: var(--sunrise) !important; }
.card-row.priority-important { border-left-color: var(--sky) !important; }
.card-row.priority-informational { border-left-color: var(--sea) !important; }
.card-row.priority-newsletter, .card-row.priority-low { border-left-color: #CBD5E1 !important; }

/* Invisible full-row button — click handling only, no visible label. Sits
   below the visual content layer; z-index keeps it clickable everywhere
   except where a higher-stacked interactive element (Done button) overrides. */
.card-row-btn { position: absolute !important; inset: 0 !important; z-index: 1 !important; margin: 0 !important; }
.card-row-btn button {
    width: 100% !important;
    height: 100% !important;
    min-height: 0 !important;
    padding: 0 !important;
    background: transparent !important;
    border: none !important;
    box-shadow: none !important;
    opacity: 0 !important;
    cursor: pointer !important;
}

/* Visual content layer — real title/subtitle styling, sits on top but never
   intercepts clicks (pointer-events:none) so they fall through to the
   invisible button beneath. */
.card-row-content {
    position: relative !important;
    z-index: 2 !important;
    pointer-events: none !important;
    display: flex !important;
    flex-direction: column !important;
    justify-content: center !important;
    gap: 2px !important;
    width: 100% !important;
}
.card-row-content.flush-html {
    padding: 12px 100px 12px 14px !important;
    min-height: 58px !important;
}
.card-row-content .row-title {
    font-weight: 600 !important;
    font-size: 13.5px !important;
    color: var(--ink) !important;
    line-height: 1.4 !important;
}
.card-row-content .row-subtitle {
    font-weight: 400 !important;
    font-size: 12px !important;
    color: var(--ink-muted) !important;
    line-height: 1.4 !important;
}

.card-row-deadline {
    position: absolute !important;
    top: 12px !important;
    right: 14px !important;
    z-index: 2 !important;
    pointer-events: none !important;
    font-size: 11px !important;
    text-align: right !important;
    max-width: 90px !important;
}

.card-row-done-btn {
    position: absolute !important;
    bottom: 8px !important;
    right: 14px !important;
    z-index: 3 !important;
    opacity: 0 !important;
    transition: opacity .12s ease !important;
}
.card-row-done-btn button { pointer-events: auto !important; }
.card-row:hover .card-row-done-btn { opacity: 1 !important; }
.card-row-done-btn button {
    min-width: unset !important;
    width: auto !important;
    padding: 2px 12px !important;
    font-size: 11px !important;
    border-radius: 999px !important;
    background: var(--sea-soft) !important;
    border: 1px solid var(--sea) !important;
    color: #0C6B56 !important;
    box-shadow: none !important;
}

.empty-state { color: var(--ink-muted) !important; font-size: 13px !important; padding: 8px 2px !important; }

/* ---------- Back link (contextual Reply page) ---------- */
.back-link button {
    background: transparent !important;
    border: none !important;
    box-shadow: none !important;
    color: var(--sky) !important;
    font-weight: 600 !important;
    font-size: 13px !important;
    padding: 0 0 14px 0 !important;
    width: auto !important;
    min-width: unset !important;
}
.back-link button:hover { text-decoration: underline !important; background: transparent !important; }

button.primary {
    background: var(--sky) !important;
    border: none !important;
    color: #FFFFFF !important;
    font-weight: 600 !important;
    border-radius: 9px !important;
}
button.primary:hover { background: var(--sky-hover) !important; }
button.secondary {
    background: #FFFFFF !important;
    border: 1px solid var(--border) !important;
    color: var(--ink) !important;
    border-radius: 9px !important;
    font-weight: 500 !important;
}
button.secondary:hover { background: var(--sky-tint) !important; }

.countdown-message { color: var(--sunrise) !important; font-weight: 600 !important; }

textarea, input[type=text] {
    border-radius: 9px !important;
    border: 1px solid var(--border) !important;
}
textarea:focus, input[type=text]:focus {
    border-color: var(--sky) !important;
    box-shadow: 0 0 0 3px var(--sky-tint) !important;
}

/* ---------- Login hero ---------- */
.login-hero {
    min-height: 100vh !important;
    width: 100% !important;
    display: flex !important;
    align-items: center !important;
    justify-content: center !important;
    background:
        radial-gradient(circle at 15% 10%, var(--sky-tint) 0%, transparent 45%),
        radial-gradient(circle at 85% 90%, var(--sunrise-soft) 0%, transparent 45%),
        var(--bg) !important;
}
.login-hero-card {
    background: var(--card);
    border: 1px solid var(--border);
    border-radius: 18px;
    padding: 48px 44px;
    max-width: 420px;
    text-align: center;
    box-shadow: 0 12px 40px rgba(11, 30, 61, 0.08);
}
.login-logo { display: flex; justify-content: center; margin-bottom: 18px; }
.login-logo span {
    display: block; width: 44px; height: 44px; border-radius: 13px;
    background: linear-gradient(135deg, var(--sky) 0%, var(--sunrise) 100%);
}
.login-hero-card h1 {
    margin: 0 0 8px 0; font-size: 23px; font-weight: 800;
    font-family: 'Manrope', sans-serif; color: var(--ink);
}
.login-hero-card p { color: var(--ink-muted); font-size: 14px; margin-bottom: 26px; line-height: 1.55; }
.google-signin-btn {
    display: inline-flex;
    align-items: center;
    gap: 10px;
    background: var(--ink);
    border: 1px solid var(--ink);
    border-radius: 9px;
    padding: 12px 22px;
    text-decoration: none;
    color: #FFFFFF;
    font-weight: 600;
    font-size: 14px;
    transition: background 0.15s;
}
.google-signin-btn:hover { background: #0A1A32; }
.google-g {
    display: inline-flex;
    align-items: center;
    justify-content: center;
    width: 20px;
    height: 20px;
    border-radius: 50%;
    background: #FFFFFF;
    color: var(--ink);
    font-size: 12px;
    font-weight: 700;
}

/* ---------- Responsive ---------- */
@media (max-width: 900px) {
    .app-shell { flex-direction: column !important; }
    .sidebar { min-height: auto !important; width: 100% !important; }
    .main-area { padding: 20px !important; max-width: 100% !important; }
    .briefing-columns { flex-direction: column !important; }
}
"""

# Order: briefing, inbox, tasks, compose, draft, reply sections, then the
# matching nav buttons in the same order — see _nav_updates.
NAV_ALL_HIDDEN = tuple(gr.update(visible=False) for _ in range(len(SECTIONS) * 2))

# draft_display, refine_box, refine_button, send_button, discard_button, compose_status
DRAFT_PANEL_HIDDEN = (
    gr.update(value="", visible=False),
    gr.update(value="", visible=False),
    gr.update(visible=False),
    gr.update(visible=False),
    gr.update(visible=False),
    gr.update(value="", visible=False),
)


def _nav_updates(active: str, reply_available: bool, draft_available: bool):
    section_updates = tuple(gr.update(visible=(name == active)) for name in SECTIONS)
    visibility = {
        "briefing": True,
        "inbox": True,
        "tasks": True,
        "compose": True,
        "draft": draft_available or active == "draft",
        "reply": reply_available or active == "reply",
    }
    nav_updates = tuple(
        gr.update(
            elem_classes=NAV_ITEM_ACTIVE if name == active else NAV_ITEM_BASE,
            visible=visibility[name],
        )
        for name in SECTIONS
    )
    return (*section_updates, *nav_updates)


def _extract_email(raw_from: str) -> str:
    """Pull a bare email address out of a raw 'From' header like
    '"John Doe" <john@x.com>'. Falls back to the raw string if no match."""
    if not raw_from:
        return ""
    match = re.search(r"[\w.+-]+@[\w-]+\.[\w.-]+", raw_from)
    return match.group(0) if match else raw_from.strip()


def _humanize_seconds(seconds: float) -> str:
    seconds = max(0, int(seconds))
    if seconds < 60:
        return f"{seconds}s"
    minutes = seconds // 60
    if minutes < 60:
        return f"{minutes}m"
    hours = minutes // 60
    return f"{hours}h {minutes % 60}m"


def _format_sync_status(sync_state) -> str:
    if sync_state is None or sync_state.last_synced_at is None:
        return "First sync will run automatically shortly after you sign in."
    now = datetime.now(timezone.utc)
    last = sync_state.last_synced_at
    last = last if last.tzinfo else last.replace(tzinfo=timezone.utc)
    elapsed = (now - last).total_seconds()
    next_in = settings.SYNC_INTERVAL_SECONDS - elapsed
    next_txt = "starting shortly" if next_in <= 0 else f"in {_humanize_seconds(next_in)}"
    return f"Last synced {_humanize_seconds(elapsed)} ago · next digest {next_txt}"


def _priority_value(priority) -> str | None:
    if priority is None:
        return None
    return priority.value if hasattr(priority, "value") else str(priority)


def _category_value(category) -> str | None:
    if category is None:
        return None
    return category.value if hasattr(category, "value") else str(category)


def _category_label(category) -> str | None:
    value = _category_value(category)
    if value is None:
        return None
    return CATEGORY_LABELS.get(value, value.title())


def _priority_badge(priority) -> str:
    value = _priority_value(priority)
    if value is None:
        return '<span style="color:#94A3B8;">&mdash;</span>'
    color, label = PRIORITY_STYLE.get(value, ("#94A3B8", value.title()))
    return f'<span style="color:{color}; font-weight:600;">{label}</span>'


def _deadline_badge(deadline) -> str:
    if deadline is None:
        return '<span style="color:#94A3B8;">&mdash;</span>'
    now = datetime.now(timezone.utc)
    dl = deadline if deadline.tzinfo else deadline.replace(tzinfo=timezone.utc)
    seconds_left = (dl - now).total_seconds()
    label = dl.strftime("%b %d")
    if seconds_left < 0:
        return f'<span style="color:#E2574C; font-weight:600;">&#9679; Overdue &middot; {label}</span>'
    if seconds_left < 86400:
        return f'<span style="color:#F0A15C; font-weight:600;">&#9679; Due today &middot; {label}</span>'
    if seconds_left < 86400 * 7:
        return f'<span style="color:#2F9BE0; font-weight:600;">&#9679; This week &middot; {label}</span>'
    return f'<span style="color:#5C7A9C;">{label}</span>'


def _format_draft(draft) -> str:
    return f"**To:** {draft.to_addr}\n\n**Subject:** {draft.subject}\n\n**Body:**\n\n{draft.body}"


# ---------------------------------------------------------------------------
# Pure render functions — HTML/data builders with no I/O, testable in
# isolation with sample data before wiring into the page layouts.
# ---------------------------------------------------------------------------

def render_stat_cards_html(briefing: dict) -> str:
    counts = briefing["priority_counts"]
    urgent = counts.get("urgent", 0)
    action = counts.get("action", 0)
    overdue = len(briefing["overdue"])
    cards = [
        (briefing["new_email_count"], "New mail", "#10243E"),
        (urgent, "Urgent", "#E2574C" if urgent else "#94A3B8"),
        (action, "Action needed", "#F0A15C" if action else "#94A3B8"),
        (overdue, "Overdue", "#E2574C" if overdue else "#94A3B8"),
    ]
    cards_html = "".join(
        f'<div class="stat-card"><div class="stat-number" style="color:{color};">{count}</div>'
        f'<div class="stat-label">{label}</div></div>'
        for count, label, color in cards
    )
    return f'<div class="stat-strip">{cards_html}</div>'


def render_mini_list_html(lines: list[str]) -> str:
    if not lines:
        return "<ul><li>Nothing here right now.</li></ul>"
    return "<ul>" + "".join(f"<li>{line}</li>" for line in lines) + "</ul>"


def _card_row_html(title: str, subtitle: str) -> str:
    # Two separately-styled lines (bold title, muted subtitle) — a plain Gradio
    # Button label can't do this (single uniform text style), which is why the
    # old version read as one undifferentiated run-on line. Escape both: raw
    # sender strings like "Name <addr@x.com>" would otherwise be parsed as an
    # (invalid, silently dropped) HTML tag and vanish from the row.
    safe_title = html.escape(title)
    subtitle_html = f'<div class="row-subtitle">{html.escape(subtitle)}</div>' if subtitle else ""
    return f'<div class="row-title">{safe_title}</div>{subtitle_html}'


def render_triage_state(triage_rows) -> list[dict]:
    return [
        {
            "message_id": row.message_id,
            "priority": _priority_value(row.priority) or "low",
            "title": row.subject or "(no subject)",
            "subtitle": " · ".join(
                part for part in [
                    row.sender or "Unknown sender",
                    _category_label(row.category),
                    row.suggested_action,
                ] if part
            ),
            "deadline_html": _deadline_badge(row.deadline),
        }
        for row in triage_rows
    ]


def render_tasks_state(action_items) -> list[dict]:
    rows = []
    for item in action_items:
        who = item.related_person or item.source_sender or "Unknown sender"
        title = item.description
        if item.status == ActionItemStatus.done:
            title = f"✓ {title}"
        elif item.status == ActionItemStatus.dismissed:
            title = f"— {title}"
        rows.append(
            {
                "id": item.id,
                "message_id": item.source_message_id,
                "priority": _priority_value(item.priority) or "low",
                "title": title,
                "subtitle": who,
                "deadline_html": _deadline_badge(item.deadline),
                "done": item.status != ActionItemStatus.open,
            }
        )
    return rows


# ---------------------------------------------------------------------------
# Data-fetch functions — one per page, reusing existing crud functions,
# no duplicated queries between them.
# ---------------------------------------------------------------------------

def _briefing_data(user_id: int):
    db = get_session()
    try:
        user = get_user_by_id(db, user_id)
        if user is None:
            return None
        summary_text = NO_SUMMARY_YET_TEXT
        latest_summary = get_latest_summary(db, user.id)
        if latest_summary is not None:
            summary_text = latest_summary.summary_text
        display_name = (user.name or user.email.split("@")[0]).split(" ")[0]
        briefing = get_daily_briefing_data(db, user.id)
        sync_status = _format_sync_status(get_sync_state(db, user.id))
        return {
            "display_name": display_name,
            "digest": summary_text,
            "sync_status": sync_status,
            **briefing,
        }
    finally:
        db.close()


def _inbox_data(user_id: int, priority_filter: str = "all", search_text: str = ""):
    db = get_session()
    try:
        rows = get_recent_triage(db, user_id)
        sync_status = _format_sync_status(get_sync_state(db, user_id))
    finally:
        db.close()

    if priority_filter and priority_filter != "all":
        rows = [r for r in rows if _priority_value(r.priority) == priority_filter]
    needle = (search_text or "").strip().lower()
    if needle:
        rows = [
            r for r in rows
            if needle in (r.subject or "").lower() or needle in (r.sender or "").lower()
        ]
    rows.sort(key=lambda r: PRIORITY_ORDER.index(_priority_value(r.priority) or "low"))
    return render_triage_state(rows), sync_status


def _tasks_data(user_id: int, filter_text: str = "", include_done: bool = False):
    db = get_session()
    try:
        items = get_action_items(db, user_id, include_done=include_done)
        sync_status = _format_sync_status(get_sync_state(db, user_id))
    finally:
        db.close()

    needle = (filter_text or "").strip().lower()
    if needle:
        items = [
            item for item in items
            if needle in item.description.lower() or needle in (item.source_sender or "").lower()
        ]
    return render_tasks_state(items), sync_status


def _load_status(request: gr.Request):
    user_id = get_user_id_from_request(request)
    db = get_session()
    try:
        user = get_user_by_id(db, user_id) if user_id is not None else None
    finally:
        db.close()

    if user is None:
        return (
            gr.update(visible=True),
            gr.update(value="", visible=False),
            gr.update(visible=False),
            gr.update(value=""),
            gr.update(value=""),
            gr.update(value=""),
            gr.update(value=""),
            [],
            "",
            [],
            "",
            False,
            False,
            *NAV_ALL_HIDDEN,
        )

    briefing = _briefing_data(user_id)
    inbox_state, inbox_sync = _inbox_data(user_id)
    tasks_state, tasks_sync = _tasks_data(user_id)

    header_html = PAGE_HEADER_HTML["briefing"].format(greeting=f"Good day, {briefing['display_name']} \U0001F44B")
    deadlines_html = render_mini_list_html(
        [f"{item.description} &mdash; due {item.deadline.strftime('%b %d')}" for item in briefing["todays_deadlines"]]
    )
    suggested_html = render_mini_list_html([item.description for item in briefing["suggested_actions"]])

    return (
        gr.update(visible=False),
        gr.update(value=f"Signed in as **{user.email}**", visible=True),
        gr.update(visible=True),
        gr.update(value=header_html),
        gr.update(value=render_stat_cards_html(briefing)),
        gr.update(value=deadlines_html),
        gr.update(value=suggested_html),
        inbox_state,
        inbox_sync,
        tasks_state,
        tasks_sync,
        False,
        False,
        *_nav_updates("briefing", False, False),
    )


def _refresh_briefing(request: gr.Request):
    user_id = get_user_id_from_request(request)
    if user_id is None:
        return gr.update(), gr.update(), gr.update(), gr.update()

    briefing = _briefing_data(user_id)
    if briefing is None:
        return gr.update(), gr.update(), gr.update(), gr.update()

    header_html = PAGE_HEADER_HTML["briefing"].format(greeting=f"Good day, {briefing['display_name']} \U0001F44B")
    deadlines_html = render_mini_list_html(
        [f"{item.description} &mdash; due {item.deadline.strftime('%b %d')}" for item in briefing["todays_deadlines"]]
    )
    suggested_html = render_mini_list_html([item.description for item in briefing["suggested_actions"]])
    return (
        gr.update(value=header_html),
        gr.update(value=render_stat_cards_html(briefing)),
        gr.update(value=deadlines_html),
        gr.update(value=suggested_html),
    )


def _refresh_inbox(priority_filter: str, search_text: str, request: gr.Request):
    user_id = get_user_id_from_request(request)
    if user_id is None:
        return gr.skip(), gr.update()
    state, sync_status = _inbox_data(user_id, priority_filter, search_text)
    return state, sync_status


def _refresh_tasks(filter_text: str, include_done: bool, request: gr.Request):
    user_id = get_user_id_from_request(request)
    if user_id is None:
        return gr.skip(), gr.update()
    state, sync_status = _tasks_data(user_id, filter_text, include_done)
    return state, sync_status


def _go_to(section: str):
    def _handler(reply_available, draft_available):
        # Reply is contextual, not a persistent destination (see design plan
        # Section 0) — it should only stay reachable while you're actually
        # navigating within the reply flow. Moving to any other primary page
        # clears it, so it doesn't linger pointing at a stale email forever.
        effective_reply_available = reply_available if section == "reply" else False
        return _nav_updates(section, effective_reply_available, draft_available)

    return _handler


def _load_original_email(user_id: int, message_id: str):
    """Fetches the live email plus prior-correspondence context. Returns
    (email_markdown, history_update, status_update)."""
    email_md = ""
    status_update = gr.update(value="", visible=False)
    history_update = gr.update(value="", visible=False)
    db = get_session()
    try:
        user = get_user_by_id(db, user_id) if user_id is not None else None
        if user is None:
            status_update = gr.update(value="Not logged in — please sign in first.", visible=True)
            return email_md, history_update, status_update
        try:
            original = get_message(user, message_id)
            email_md = (
                f"**From:** {original['sender']}\n\n"
                f"**Subject:** {original['subject']}\n\n---\n\n{original['body']}"
            )
            sender_addr = _extract_email(original.get("sender", ""))
            past_drafts = get_recent_sent_drafts_to(db, user.id, sender_addr)
            if past_drafts:
                lines = ["**Previous replies to this sender**"]
                for draft in past_drafts:
                    when = draft.updated_at.strftime("%b %d") if draft.updated_at else "unknown date"
                    lines.append(f"- *{when}* &mdash; {draft.subject}")
                history_update = gr.update(value="\n".join(lines), visible=True)
        except Exception as exc:  # noqa: BLE001
            status_update = gr.update(value=f"Could not load the original email: {exc}", visible=True)
    finally:
        db.close()
    return email_md, history_update, status_update


def _select_triage_item(message_id: str, draft_available: bool, request: gr.Request):
    user_id = get_user_id_from_request(request)
    email_md, history_update, status_update = _load_original_email(user_id, message_id)
    return (
        email_md,
        message_id,
        None,  # no action_item_id — this reply didn't come from an action item
        status_update,
        "",
        history_update,
        gr.update(value="← Back to Inbox"),
        "inbox",
        gr.update(visible=False),  # mark-done button hidden — triage rows aren't action items
        True,
        *_nav_updates("reply", True, draft_available),
    )


def _select_task_item(action_item_id: int, message_id: str, draft_available: bool, request: gr.Request):
    user_id = get_user_id_from_request(request)
    email_md, history_update, status_update = _load_original_email(user_id, message_id)
    return (
        email_md,
        message_id,
        action_item_id,
        status_update,
        "",
        history_update,
        gr.update(value="← Back to Tasks"),
        "tasks",
        gr.update(visible=True),
        True,
        *_nav_updates("reply", True, draft_available),
    )


def _back_to_origin(origin: str, reply_available: bool, draft_available: bool):
    # "Back" means leaving the reply context entirely, same reasoning as _go_to.
    return _nav_updates(origin or "briefing", False, draft_available)


def _mark_item_done_inline(action_item_id: int | None, filter_text: str, include_done: bool, request: gr.Request):
    user_id = get_user_id_from_request(request)
    if user_id is None or action_item_id is None:
        return gr.skip(), gr.update()

    db = get_session()
    try:
        set_action_item_status(db, user_id, action_item_id, ActionItemStatus.done)
    finally:
        db.close()

    state, sync_status = _tasks_data(user_id, filter_text, include_done)
    return state, sync_status


def _mark_item_done_from_reply(
    action_item_id: int | None, origin: str, reply_available: bool, draft_available: bool, request: gr.Request
):
    user_id = get_user_id_from_request(request)
    if user_id is None or action_item_id is None:
        return (
            gr.update(value="Nothing to mark done here.", visible=True),
            *_nav_updates("reply", reply_available, draft_available),
        )

    db = get_session()
    try:
        set_action_item_status(db, user_id, action_item_id, ActionItemStatus.done)
    finally:
        db.close()

    target = origin or "tasks"
    return (
        gr.update(value="Marked as done.", visible=True),
        *_nav_updates(target, False, draft_available),
    )


def _generate_reply_draft(guidance: str, message_id: str | None, reply_available: bool, request: gr.Request):
    user_id = get_user_id_from_request(request)
    if user_id is None or not message_id:
        message = "Not logged in — please sign in first." if user_id is None else "Select an email first."
        return (
            gr.update(value=message, visible=True),
            None,
            *DRAFT_PANEL_HIDDEN,
            False,
            *_nav_updates("reply", reply_available, False),
        )

    db = get_session()
    try:
        user = get_user_by_id(db, user_id)
        draft_id = compose_reply(db, user, message_id, guidance or "")
        draft = get_draft_by_id(db, user_id, draft_id)
        return (
            gr.update(value="", visible=False),
            draft_id,
            gr.update(value=_format_draft(draft), visible=True),
            gr.update(value="", visible=True),
            gr.update(visible=True),
            gr.update(visible=True),
            gr.update(visible=True),
            gr.update(value="Reply drafted — review it below before sending.", visible=True),
            True,
            *_nav_updates("draft", reply_available, True),
        )
    except Exception as exc:  # noqa: BLE001
        return (
            gr.update(value=f"Could not draft reply: {exc}", visible=True),
            None,
            *DRAFT_PANEL_HIDDEN,
            False,
            *_nav_updates("reply", reply_available, False),
        )
    finally:
        db.close()


def _compose_now(request_text: str, reply_available: bool, request: gr.Request):
    user_id = get_user_id_from_request(request)
    if user_id is None or not request_text.strip():
        message = "Not logged in — please sign in first." if user_id is None else "Enter a request first."
        return (
            None,
            *DRAFT_PANEL_HIDDEN[:-1],
            gr.update(value=message, visible=True),
            request_text,
            False,
            *_nav_updates("compose", reply_available, False),
        )

    db = get_session()
    try:
        draft_id = compose_draft(db, user_id, request_text)
        draft = get_draft_by_id(db, user_id, draft_id)
        return (
            draft_id,
            gr.update(value=_format_draft(draft), visible=True),
            gr.update(value="", visible=True),
            gr.update(visible=True),
            gr.update(visible=True),
            gr.update(visible=True),
            gr.update(value="Draft created — review it below before sending.", visible=True),
            "",
            True,
            *_nav_updates("draft", reply_available, True),
        )
    except Exception as exc:  # noqa: BLE001
        return (
            None,
            *DRAFT_PANEL_HIDDEN[:-1],
            gr.update(value=f"Could not create draft: {exc}", visible=True),
            request_text,
            False,
            *_nav_updates("compose", reply_available, False),
        )
    finally:
        db.close()


def _refine_now(feedback: str, draft_id: int | None, request: gr.Request):
    user_id = get_user_id_from_request(request)
    if user_id is None or draft_id is None:
        return (draft_id, *DRAFT_PANEL_HIDDEN)

    db = get_session()
    try:
        refine_draft(db, user_id, draft_id, feedback or "")
        draft = get_draft_by_id(db, user_id, draft_id)
        return (
            draft_id,
            gr.update(value=_format_draft(draft), visible=True),
            gr.update(value="", visible=True),
            gr.update(visible=True),
            gr.update(visible=True),
            gr.update(visible=True),
            gr.update(value="Draft updated.", visible=True),
        )
    except Exception as exc:  # noqa: BLE001
        draft = get_draft_by_id(db, user_id, draft_id)
        return (
            draft_id,
            gr.update(value=_format_draft(draft) if draft else "", visible=True),
            gr.update(value="", visible=True),
            gr.update(visible=True),
            gr.update(visible=True),
            gr.update(visible=True),
            gr.update(value=f"Could not refine draft: {exc}", visible=True),
        )
    finally:
        db.close()


def _discard_now(draft_id: int | None, request: gr.Request):
    user_id = get_user_id_from_request(request)
    if user_id is None or draft_id is None:
        return (None, *DRAFT_PANEL_HIDDEN)

    db = get_session()
    try:
        set_draft_status(db, user_id, draft_id, DraftStatus.discarded)
        return (None, *DRAFT_PANEL_HIDDEN[:-1], gr.update(value="Draft discarded.", visible=True))
    finally:
        db.close()


# ---------- Undo-send countdown flow ----------
# SEND_FLOW_OUTPUTS order, shared by start/cancel/tick handlers:
# draft_display, refine_box, refine_button, send_button, discard_button,
# compose_status, countdown_message, cancel_send_button, send_countdown_state, draft_id_state

def _start_send_countdown(draft_id: int | None):
    if draft_id is None:
        return (
            gr.update(), gr.update(), gr.update(), gr.update(), gr.update(),
            gr.update(value="No draft selected.", visible=True),
            gr.update(visible=False),
            gr.update(visible=False),
            0,
            draft_id,
        )
    return (
        gr.update(visible=False),
        gr.update(visible=False),
        gr.update(visible=False),
        gr.update(visible=False),
        gr.update(visible=False),
        gr.update(value="", visible=False),
        gr.update(value=f"Sending in {SEND_COUNTDOWN_SECONDS}s&hellip; you can still cancel.", visible=True),
        gr.update(visible=True),
        SEND_COUNTDOWN_SECONDS,
        draft_id,
    )


def _cancel_send_countdown(draft_id: int | None, request: gr.Request):
    user_id = get_user_id_from_request(request)
    if user_id is None or draft_id is None:
        return (
            *DRAFT_PANEL_HIDDEN[:-1],
            gr.update(value="Draft no longer available.", visible=True),
            gr.update(visible=False),
            gr.update(visible=False),
            0,
            None,
        )

    db = get_session()
    try:
        draft = get_draft_by_id(db, user_id, draft_id)
    finally:
        db.close()

    if draft is None or draft.status != DraftStatus.pending:
        return (
            *DRAFT_PANEL_HIDDEN[:-1],
            gr.update(value="Draft no longer pending.", visible=True),
            gr.update(visible=False),
            gr.update(visible=False),
            0,
            None,
        )

    return (
        gr.update(value=_format_draft(draft), visible=True),
        gr.update(value="", visible=True),
        gr.update(visible=True),
        gr.update(visible=True),
        gr.update(visible=True),
        gr.update(value="Send cancelled.", visible=True),
        gr.update(visible=False),
        gr.update(visible=False),
        0,
        draft_id,
    )


def _send_tick(countdown: int, draft_id: int | None, request: gr.Request):
    # Note: gr.update() is not reliable as a "leave unchanged" value for
    # gr.State outputs (known Gradio limitation) — use gr.skip() for the
    # send_countdown_state / draft_id_state slots instead, and concrete
    # values everywhere the state should actually change.
    if not countdown or countdown <= 0:
        return (gr.update(), gr.update(), gr.update(), gr.update(), gr.update(),
                gr.update(), gr.update(), gr.update(), gr.skip(), gr.skip())

    remaining = countdown - 1
    if remaining > 0:
        return (
            gr.update(), gr.update(), gr.update(), gr.update(), gr.update(), gr.update(),
            gr.update(value=f"Sending in {remaining}s&hellip; you can still cancel."),
            gr.update(),
            remaining,
            gr.skip(),
        )

    # Countdown hit zero — this is the one place that actually calls gmail.send,
    # exactly like the original human-triggered send handler. The agent never
    # has this tool; only this UI-driven flow, after the user's own click and
    # an uncancelled grace period, can send mail.
    user_id = get_user_id_from_request(request)
    if user_id is None or draft_id is None:
        return (
            *DRAFT_PANEL_HIDDEN[:-1],
            gr.update(value="Send failed: not logged in.", visible=True),
            gr.update(visible=False),
            gr.update(visible=False),
            0,
            None,
        )

    db = get_session()
    draft = None
    try:
        user = get_user_by_id(db, user_id)
        draft = get_draft_by_id(db, user_id, draft_id)
        if draft is None or draft.status != DraftStatus.pending:
            return (
                *DRAFT_PANEL_HIDDEN[:-1],
                gr.update(value="Draft is no longer pending.", visible=True),
                gr.update(visible=False),
                gr.update(visible=False),
                0,
                None,
            )

        since = datetime.now(timezone.utc) - timedelta(hours=1)
        if count_sent_drafts_since(db, user_id, since) >= MAX_SENDS_PER_HOUR:
            return (
                gr.update(value=_format_draft(draft), visible=True),
                gr.update(value="", visible=True),
                gr.update(visible=True),
                gr.update(visible=True),
                gr.update(visible=True),
                gr.update(value=f"Rate limit reached ({MAX_SENDS_PER_HOUR} sends/hour). Try again later.", visible=True),
                gr.update(visible=False),
                gr.update(visible=False),
                0,
                draft_id,
            )

        send_email(user, to_addr=draft.to_addr, subject=draft.subject, body=draft.body)
        set_draft_status(db, user_id, draft_id, DraftStatus.sent)

        return (
            *DRAFT_PANEL_HIDDEN[:-1],
            gr.update(value="Sent!", visible=True),
            gr.update(visible=False),
            gr.update(visible=False),
            0,
            None,
        )
    except Exception as exc:  # noqa: BLE001 — keep the draft visible so the user can retry or discard
        return (
            gr.update(value=_format_draft(draft) if draft else "", visible=True),
            gr.update(value="", visible=True),
            gr.update(visible=True),
            gr.update(visible=True),
            gr.update(visible=True),
            gr.update(value=f"Send failed: {exc}", visible=True),
            gr.update(visible=False),
            gr.update(visible=False),
            0,
            draft_id,
        )
    finally:
        db.close()


def build_app() -> gr.Blocks:
    with gr.Blocks(title="Gmail Agent") as demo:
        with gr.Row(elem_classes="app-shell"):
            with gr.Column(scale=0, min_width=232, elem_classes="sidebar"):
                gr.HTML('<div class="brand-row"><span class="brand-mark"></span><span class="brand-name">Gmail Agent</span></div>')
                with gr.Column(elem_classes="nav-group"):
                    nav_briefing_btn = gr.Button("\U0001F305  Briefing", visible=False, elem_classes=NAV_ITEM_ACTIVE)
                    nav_inbox_btn = gr.Button("\U0001F4E5  Inbox", visible=False, elem_classes=NAV_ITEM_BASE)
                    nav_tasks_btn = gr.Button("✅  Tasks", visible=False, elem_classes=NAV_ITEM_BASE)
                    nav_reply_btn = gr.Button("↩️  Reply", visible=False, elem_classes=NAV_ITEM_BASE)
                    gr.HTML('<div class="nav-divider"></div>')
                    nav_compose_btn = gr.Button("✉️  Compose", visible=False, elem_classes=NAV_ITEM_BASE)
                    nav_draft_btn = gr.Button("\U0001F5C2️  Drafts", visible=False, elem_classes=NAV_ITEM_BASE)
                with gr.Column(elem_classes="sidebar-footer"):
                    logged_in_text = gr.Markdown(visible=False)
                    logout_link = gr.HTML('<a href="/auth/logout">Log out</a>', elem_classes="logout-link", visible=False)

            with gr.Column(scale=1, elem_classes="main-area"):
                with gr.Column(visible=True, elem_classes="login-hero") as login_section:
                    gr.HTML(LOGIN_HERO_HTML)

                # ---------------- Briefing ----------------
                with gr.Column(visible=False, elem_classes="section") as briefing_section:
                    briefing_header = gr.HTML(PAGE_HEADER_HTML["briefing"].format(greeting="Good day"))
                    briefing_sync_status = gr.Markdown("", elem_classes="sync-status")
                    briefing_stats = gr.HTML("")
                    with gr.Row(elem_classes="briefing-columns"):
                        with gr.Group(elem_classes="section-card briefing-mini-card"):
                            gr.Markdown("Today's deadlines", elem_classes="card-eyebrow")
                            briefing_deadlines = gr.HTML("", elem_classes="flush-html")
                        with gr.Group(elem_classes="section-card briefing-mini-card"):
                            gr.Markdown("Suggested actions", elem_classes="card-eyebrow")
                            briefing_suggested = gr.HTML("", elem_classes="flush-html")
                    with gr.Group(elem_classes="section-card digest-card"):
                        gr.Markdown("Latest digest", elem_classes="card-eyebrow")
                        briefing_digest = gr.Markdown(NO_SUMMARY_YET_TEXT, elem_classes="flush-html")

                # ---------------- Inbox ----------------
                with gr.Column(visible=False, elem_classes="section") as inbox_section:
                    gr.HTML(PAGE_HEADER_HTML["inbox"])
                    inbox_sync_status = gr.Markdown("", elem_classes="sync-status")
                    with gr.Row(elem_classes="filter-row"):
                        inbox_priority_filter = gr.Radio(
                            choices=PRIORITY_RADIO_CHOICES, value="all", show_label=False,
                            container=False, elem_classes="priority-filter", scale=3,
                        )
                        inbox_search_box = gr.Textbox(show_label=False, placeholder="Search subject or sender…", scale=2)
                    inbox_items_state = gr.State(value=[])

                    @gr.render(inputs=[inbox_items_state])
                    def _render_inbox_rows(rows):
                        if not rows:
                            gr.Markdown("Nothing here for this filter.", elem_classes="empty-state")
                            return
                        for row in rows:
                            with gr.Row(elem_classes=f"card-row priority-{row['priority']}"):
                                row_btn = gr.Button("", elem_classes="card-row-btn")
                                gr.HTML(
                                    _card_row_html(row["title"], row["subtitle"]),
                                    elem_classes="card-row-content flush-html",
                                )
                                gr.HTML(row["deadline_html"], elem_classes="card-row-deadline flush-html")
                                row_btn.click(
                                    _select_triage_item,
                                    inputs=[gr.State(row["message_id"]), draft_available_state],
                                    outputs=[
                                        reply_email_display, selected_message_id_state,
                                        selected_action_item_id_state, reply_status,
                                        reply_guidance_box, reply_history_output,
                                        back_link_button, reply_origin_state, mark_done_button,
                                        reply_available_state, *nav_outputs,
                                    ],
                                )

                # ---------------- Tasks ----------------
                with gr.Column(visible=False, elem_classes="section") as tasks_section:
                    gr.HTML(PAGE_HEADER_HTML["tasks"])
                    tasks_sync_status = gr.Markdown("", elem_classes="sync-status")
                    with gr.Row(elem_classes="filter-row"):
                        tasks_filter_box = gr.Textbox(
                            show_label=False, placeholder="Filter by keyword or sender…", scale=3,
                        )
                        tasks_show_done = gr.Checkbox(label="Show completed", scale=1, value=False)
                    tasks_items_state = gr.State(value=[])

                    @gr.render(inputs=[tasks_items_state])
                    def _render_task_rows(rows):
                        if not rows:
                            gr.Markdown("No open items — you're caught up.", elem_classes="empty-state")
                            return
                        for row in rows:
                            with gr.Row(elem_classes=f"card-row priority-{row['priority']}"):
                                row_btn = gr.Button("", elem_classes="card-row-btn")
                                gr.HTML(
                                    _card_row_html(row["title"], row["subtitle"]),
                                    elem_classes="card-row-content flush-html",
                                )
                                gr.HTML(row["deadline_html"], elem_classes="card-row-deadline flush-html")
                                row_btn.click(
                                    _select_task_item,
                                    inputs=[
                                        gr.State(row["id"]), gr.State(row["message_id"]), draft_available_state,
                                    ],
                                    outputs=[
                                        reply_email_display, selected_message_id_state,
                                        selected_action_item_id_state, reply_status,
                                        reply_guidance_box, reply_history_output,
                                        back_link_button, reply_origin_state, mark_done_button,
                                        reply_available_state, *nav_outputs,
                                    ],
                                )
                                if not row["done"]:
                                    done_btn = gr.Button("✓ Done", elem_classes="card-row-done-btn")
                                    done_btn.click(
                                        _mark_item_done_inline,
                                        inputs=[gr.State(row["id"]), tasks_filter_box, tasks_show_done],
                                        outputs=[tasks_items_state, tasks_sync_status],
                                    )

                # ---------------- Reply (contextual) ----------------
                with gr.Column(visible=False, elem_classes="section") as reply_section:
                    back_link_button = gr.Button("← Back", elem_classes="back-link")
                    gr.HTML(PAGE_HEADER_HTML["reply"])
                    with gr.Group(elem_classes="section-card"):
                        gr.Markdown("Original email", elem_classes="card-eyebrow")
                        reply_email_display = gr.Markdown(elem_classes="flush-html")
                        reply_history_output = gr.Markdown(visible=False, elem_classes="flush-html")
                    with gr.Group(elem_classes="section-card"):
                        reply_guidance_box = gr.Textbox(label="What do you want to say? (optional)")
                        with gr.Row():
                            reply_generate_button = gr.Button("Generate draft reply", variant="primary")
                            mark_done_button = gr.Button("Mark as done", variant="secondary")
                        reply_status = gr.Markdown(visible=False)

                # ---------------- Compose ----------------
                with gr.Column(visible=False, elem_classes="section") as compose_section:
                    gr.HTML(PAGE_HEADER_HTML["compose"])
                    with gr.Group(elem_classes="section-card"):
                        compose_request_box = gr.Textbox(
                            label="What do you want to send?",
                            placeholder='e.g. "email abc@gmail.com asking him to send the project docs"',
                        )
                        compose_button = gr.Button("Compose draft", variant="primary")

                # ---------------- Draft review ----------------
                with gr.Column(visible=False, elem_classes="section") as draft_section:
                    gr.HTML(PAGE_HEADER_HTML["draft"])
                    with gr.Group(elem_classes="section-card"):
                        draft_display = gr.Markdown(visible=False, elem_classes="flush-html")
                        refine_box = gr.Textbox(label="Refine (optional feedback)", visible=False)
                        with gr.Row():
                            refine_button = gr.Button("Refine", variant="secondary", visible=False)
                            send_button = gr.Button("Send", variant="primary", visible=False)
                            discard_button = gr.Button("Discard", variant="secondary", visible=False)
                        countdown_message = gr.Markdown(visible=False, elem_classes="countdown-message")
                        cancel_send_button = gr.Button("Cancel send", variant="secondary", visible=False)
                        compose_status = gr.Markdown(visible=False)

        selected_message_id_state = gr.State(value=None)
        selected_action_item_id_state = gr.State(value=None)
        reply_origin_state = gr.State(value="briefing")
        draft_id_state = gr.State(value=None)
        reply_available_state = gr.State(value=False)
        draft_available_state = gr.State(value=False)
        send_countdown_state = gr.State(value=0)

        refresh_timer = gr.Timer(settings.UI_REFRESH_SECONDS)
        send_timer = gr.Timer(1)

        nav_outputs = [
            briefing_section, inbox_section, tasks_section, compose_section, draft_section, reply_section,
            nav_briefing_btn, nav_inbox_btn, nav_tasks_btn, nav_compose_btn, nav_draft_btn, nav_reply_btn,
        ]
        draft_panel_outputs = [
            draft_id_state,
            draft_display,
            refine_box,
            refine_button,
            send_button,
            discard_button,
            compose_status,
        ]
        send_flow_outputs = [
            draft_display, refine_box, refine_button, send_button, discard_button,
            compose_status, countdown_message, cancel_send_button, send_countdown_state, draft_id_state,
        ]

        demo.load(
            _load_status,
            inputs=None,
            outputs=[
                login_section,
                logged_in_text,
                logout_link,
                briefing_header,
                briefing_stats,
                briefing_deadlines,
                briefing_suggested,
                inbox_items_state,
                inbox_sync_status,
                tasks_items_state,
                tasks_sync_status,
                reply_available_state,
                draft_available_state,
                *nav_outputs,
            ],
        )
        refresh_timer.tick(
            _refresh_briefing, inputs=None,
            outputs=[briefing_header, briefing_stats, briefing_deadlines, briefing_suggested],
        )
        refresh_timer.tick(
            _refresh_inbox, inputs=[inbox_priority_filter, inbox_search_box],
            outputs=[inbox_items_state, inbox_sync_status],
        )
        refresh_timer.tick(
            _refresh_tasks, inputs=[tasks_filter_box, tasks_show_done],
            outputs=[tasks_items_state, tasks_sync_status],
        )

        inbox_priority_filter.change(
            _refresh_inbox, inputs=[inbox_priority_filter, inbox_search_box],
            outputs=[inbox_items_state, inbox_sync_status],
        )
        inbox_search_box.change(
            _refresh_inbox, inputs=[inbox_priority_filter, inbox_search_box],
            outputs=[inbox_items_state, inbox_sync_status],
        )
        tasks_filter_box.change(
            _refresh_tasks, inputs=[tasks_filter_box, tasks_show_done],
            outputs=[tasks_items_state, tasks_sync_status],
        )
        tasks_show_done.change(
            _refresh_tasks, inputs=[tasks_filter_box, tasks_show_done],
            outputs=[tasks_items_state, tasks_sync_status],
        )

        nav_briefing_btn.click(
            _go_to("briefing"), inputs=[reply_available_state, draft_available_state], outputs=nav_outputs
        )
        nav_inbox_btn.click(
            _go_to("inbox"), inputs=[reply_available_state, draft_available_state], outputs=nav_outputs
        )
        nav_tasks_btn.click(
            _go_to("tasks"), inputs=[reply_available_state, draft_available_state], outputs=nav_outputs
        )
        nav_reply_btn.click(
            _go_to("reply"), inputs=[reply_available_state, draft_available_state], outputs=nav_outputs
        )
        nav_compose_btn.click(
            _go_to("compose"), inputs=[reply_available_state, draft_available_state], outputs=nav_outputs
        )
        nav_draft_btn.click(
            _go_to("draft"), inputs=[reply_available_state, draft_available_state], outputs=nav_outputs
        )
        back_link_button.click(
            _back_to_origin,
            inputs=[reply_origin_state, reply_available_state, draft_available_state],
            outputs=nav_outputs,
        )

        mark_done_button.click(
            _mark_item_done_from_reply,
            inputs=[selected_action_item_id_state, reply_origin_state, reply_available_state, draft_available_state],
            outputs=[reply_status, *nav_outputs],
        )
        reply_generate_button.click(
            _generate_reply_draft,
            inputs=[reply_guidance_box, selected_message_id_state, reply_available_state],
            outputs=[reply_status, *draft_panel_outputs, draft_available_state, *nav_outputs],
        )
        compose_button.click(
            _compose_now,
            inputs=[compose_request_box, reply_available_state],
            outputs=[*draft_panel_outputs, compose_request_box, draft_available_state, *nav_outputs],
        )
        refine_button.click(
            _refine_now, inputs=[refine_box, draft_id_state], outputs=draft_panel_outputs
        )
        send_button.click(_start_send_countdown, inputs=[draft_id_state], outputs=send_flow_outputs)
        cancel_send_button.click(_cancel_send_countdown, inputs=[draft_id_state], outputs=send_flow_outputs)
        send_timer.tick(_send_tick, inputs=[send_countdown_state, draft_id_state], outputs=send_flow_outputs)
        discard_button.click(_discard_now, inputs=[draft_id_state], outputs=draft_panel_outputs)

    return demo