from dataclasses import dataclass
from email.utils import parseaddr

from sqlalchemy.orm import Session

from agents import Agent, RunContextWrapper, Runner, function_tool
from app.db.crud import create_draft_email, get_draft_by_id, update_draft_email
from app.db.models import User
from app.gmail.client import get_message


@dataclass
class ComposeContext:
    db: Session
    user_id: int
    draft_id: int | None = None


@function_tool
def create_draft(ctx: RunContextWrapper[ComposeContext], to: str = "", subject: str = "", body: str = "") -> str:
    """Save an email draft. This NEVER sends anything — it only writes a
    pending draft that a human must explicitly review and send from the UI.

    Args:
        to: The recipient's email address (optional, empty string if unknown).
        subject: The email subject line.
        body: The full email body text.
    """
    context = ctx.context
    to_val = to or ""
    subj_val = subject or "Email Draft"
    body_val = body or ""
    if context.draft_id is not None:
        draft = update_draft_email(
            context.db, context.user_id, context.draft_id, to_addr=to_val, subject=subj_val, body=body_val
        )
    else:
        draft = create_draft_email(
            context.db, user_id=context.user_id, to_addr=to_val, subject=subj_val, body=body_val
        )
        context.draft_id = draft.id
    return f"Draft {draft.id} saved as pending."


INSTRUCTIONS = (
    "You help a user draft emails from natural-language requests. Call the "
    "create_draft tool exactly once with a subject line, body text, and recipient email if provided. "
    "If no recipient email is specified by the user, pass an empty string '' for the 'to' parameter. "
    "Never claim the email has been sent — you only ever create or update a pending draft."
)

compose_agent = Agent[ComposeContext](
    name="Email Composer",
    instructions=INSTRUCTIONS,
    tools=[create_draft],
    model="gpt-5-nano",
)


def compose_draft(db: Session, user_id: int, request_text: str) -> int:
    context = ComposeContext(db=db, user_id=user_id)
    res = Runner.run_sync(compose_agent, request_text, context=context)
    if context.draft_id is None:
        text = str(res.final_output) if (res and res.final_output) else "Draft content"
        draft = create_draft_email(
            db, user_id=user_id, to_addr="", subject="Email Draft", body=text
        )
        return draft.id
    return context.draft_id


def refine_draft(db: Session, user_id: int, draft_id: int, feedback: str) -> int:
    draft = get_draft_by_id(db, user_id, draft_id)
    if draft is None:
        raise ValueError(f"Draft {draft_id} not found.")

    context = ComposeContext(db=db, user_id=user_id, draft_id=draft_id)
    refine_input = (
        "Here is the existing draft:\n"
        f"To: {draft.to_addr}\n"
        f"Subject: {draft.subject}\n"
        f"Body:\n{draft.body}\n\n"
        f"User feedback: {feedback}\n\n"
        "Call create_draft again with the revised to/subject/body reflecting this feedback."
    )
    Runner.run_sync(compose_agent, refine_input, context=context)
    return draft_id


def compose_reply(db: Session, user: User, source_message_id: str, guidance: str) -> int:
    original = get_message(user, source_message_id)
    _, sender_email = parseaddr(original["sender"])
    if not sender_email:
        raise ValueError(f"Could not determine a reply address from '{original['sender']}'.")

    subject = original["subject"] or ""
    reply_subject = subject if subject.lower().startswith("re:") else f"Re: {subject}"

    guidance_line = (
        f"What the user wants to say in the reply: {guidance}\n"
        if guidance.strip()
        else "Write a brief, appropriate reply acknowledging the email.\n"
    )
    reply_input = (
        "Draft a reply to the following email.\n\n"
        f"Original From: {original['sender']}\n"
        f"Original Subject: {original['subject']}\n"
        f"Original Body:\n{original['body']}\n\n"
        f"You MUST call create_draft with to set to exactly: {sender_email}\n"
        f"Use this exact subject: {reply_subject}\n"
        f"{guidance_line}"
    )

    context = ComposeContext(db=db, user_id=user.id)
    Runner.run_sync(compose_agent, reply_input, context=context)
    if context.draft_id is None:
        raise RuntimeError("The compose agent did not produce a reply draft.")
    return context.draft_id