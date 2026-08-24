from typing import Literal

from pydantic import BaseModel

from agents import Agent, Runner

Priority = Literal["urgent", "action", "important", "informational", "newsletter", "low"]
Category = Literal["security", "billing", "shipping", "travel", "promotional",
                    "newsletter", "spam", "personal", "work", "other"]

PRIORITY_LABELS = {
    "urgent": "\U0001F534 Urgent",
    "action": "\U0001F7E0 Requires action",
    "important": "\U0001F7E1 Important",
    "informational": "\U0001F535 Informational",
    "newsletter": "\u26AA Newsletter / promotional",
    "low": "\U0001F5D1 Low priority",
}

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


class EmailTriageItem(BaseModel):
    message_id: str
    priority: Priority
    category: Category
    suggested_action: str | None = None  # e.g. "Reply with confirmation"
    deadline: str | None = None  # ISO date (YYYY-MM-DD) or null


class ActionItem(BaseModel):
    description: str
    deadline: str | None  # ISO date (YYYY-MM-DD) or null
    source_message_id: str
    priority: Priority | None = None
    related_person: str | None = None  # e.g. "Dr. Sharma" — who this is with/for


class SummaryOutput(BaseModel):
    summary: str
    triage: list[EmailTriageItem]  # one entry per email in the batch, no exceptions
    action_items: list[ActionItem]


INSTRUCTIONS = (
    "You are an expert AI Email Assistant. Your job is to triage, categorize, "
    "and summarize a batch of emails for a busy user.\n\n"
    "--- CRITICAL ANTI-HALLUCINATION & NO-MIXING RULE ---\n"
    "Evaluate EACH email message STRICTLY INDEPENDENTLY. NEVER combine, blend, or cross-contaminate facts, rupee/dollar amounts, dates, or subject details between different emails in the batch. "
    "For example, if Email A is a bank debit of INR 70.00 and Email B is a Pingala portal notice, DO NOT combine them into 'Pingala dues of INR 70.00'. Extract facts, amounts, and descriptions ONLY from that specific email's body text.\n\n"
    "STEP 1 — TRIAGE & CATEGORIZATION:\n"
    "For EVERY single email in the batch, generate a triage entry referencing its message_id. "
    "Assign exactly one Priority AND exactly one Category.\n\n"
    "--- CATEGORY CLASSIFICATION RULES (CRITICAL) ---\n"
    "Examine the sender, subject line, and body carefully to choose the SINGLE best category matching these definitions:\n\n"
    "1. 'security' — Account security & authentication:\n"
    "   • Signals: Password reset, 2FA/MFA verification codes, new sign-in alerts, device authorization, breach alerts, access requests.\n"
    "   • Rule: Any authentication or security notice MUST be 'security', regardless of the sender or product.\n\n"
    "2. 'billing' — Financial transactions & money owed/paid:\n"
    "   • Signals: Invoices, payment receipts, subscription renewals, charge notices, bank statements, tax documents, refunds, pay stubs.\n"
    "   • Disambiguation: A receipt for a purchase is 'billing'. An invitation to buy or upgrade is 'promotional'.\n\n"
    "3. 'shipping' — Physical orders & logistics:\n"
    "   • Signals: Package tracking, order shipped, delivery confirmation, courier updates (Amazon, FedEx, UPS, DHL, USPS).\n\n"
    "4. 'travel' — Travel bookings & itineraries:\n"
    "   • Signals: Flight tickets, hotel reservations, Airbnb bookings, Uber/Lyft trip receipts, car rentals, boarding passes.\n\n"
    "5. 'promotional' — Sales, feature announcements, & marketing offers:\n"
    "   • Signals: Discounts, coupon codes, product launches, tool integration announcements (e.g. Claude/SaaS feature updates), seasonal sales, limited-time offers, promotional ads.\n\n"
    "6. 'newsletter' — Editorial & subscribed content:\n"
    "   • Signals: Substack posts, Medium digests, tech blogs, industry news updates, weekly roundups, curated reading lists, platform activity digests.\n"
    "   • LinkedIn & Social Media Rule: Automated network digests, connection updates, job alerts, and platform notifications from LinkedIn, Twitter/X, GitHub, or Glassdoor MUST be 'newsletter' or 'other'. ONLY classify as 'work' if it is a direct 1-on-1 personal message or recruiter email addressed directly to the user.\n\n"
    "7. 'spam' — Phishing & unsolicited junk:\n"
    "   • Signals: Suspicious links, unsolicited cold sales pitches from strangers, fake prize wins, scam emails.\n\n"
    "8. 'personal' — Non-work 1-on-1 human correspondence:\n"
    "   • Signals: Direct messages from friends, family, or personal acquaintances discussing non-work topics.\n\n"
    "9. 'work' — Professional, corporate, & academic business:\n"
    "   • Signals: Project updates, team communications, meeting invites, client emails, recruiter outreach, workplace notifications.\n\n"
    "10. 'other' — Miscellaneous notifications, streak reminders, & gamification alerts:\n"
    "    • Signals: Platform streak reminders ('keep your streak going!'), badge notifications, automated system alerts not fitting above.\n\n"
    "--- PRIORITY CLASSIFICATION RULES ---\n"
    "Apply these checks IN ORDER and stop at the first match:\n"
    "1. 'urgent' — User owes an immediate personal action (reply/submit/pay) with tight time pressure (deadline < 48h or high risk).\n"
    "2. 'action' — User owes a personal action (reply/review/submit), but no immediate time emergency.\n"
    "3. 'important' — No action required, but contains key substantive information/updates the user must know.\n"
    "4. 'informational' — Automated routine notices (calendar accepts, shipping updates, receipts, product feature announcements).\n"
    "5. 'newsletter' — Subscribed reading material or marketing broadcasts.\n"
    "6. 'low' — Minor notifications, streak/gamification reminders ('keep your streak going!'), promotional ads, low-value notices.\n\n"
    "For 'urgent' and 'action' emails, provide a concise suggested_action and ISO deadline (YYYY-MM-DD) if applicable.\n\n"
    "STEP 2 — SUMMARY: Write a concise executive summary as 2-4 bullet points (starting each line with '• ') highlighting key updates concisely.\n\n"
    "STEP 3 — ACTION ITEMS: List concrete to-do tasks required of the user, linking each to its source_message_id, priority, deadline, and related_person."
)

summarizer_agent = Agent(
    name="Email Summarizer",
    instructions=INSTRUCTIONS,
    output_type=SummaryOutput,
    model="gpt-5-nano",
)


def format_email_batch(emails: list[dict]) -> str:
    blocks = []
    for email in emails:
        date_str = email["date"].isoformat() if email.get("date") else "unknown date"
        blocks.append(
            f"Message ID: {email['message_id']}\n"
            f"From: {email['sender']}\n"
            f"Date: {date_str}\n"
            f"Subject: {email['subject']}\n"
            f"Body:\n{email['body']}"
        )
    return "\n\n---\n\n".join(blocks)


def summarize_emails(emails: list[dict]) -> SummaryOutput:
    result = Runner.run_sync(summarizer_agent, format_email_batch(emails))
    return result.final_output
