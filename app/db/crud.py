import re
from datetime import datetime, timedelta, timezone

from sqlalchemy.orm import Session

from app.db.models import (
    ActionItem,
    ActionItemStatus,
    DraftEmail,
    DraftStatus,
    EmailSummary,
    EmailTriage,
    SyncState,
    TriagePriority,
    User,
)


def get_user_by_id(db: Session, user_id: int) -> User | None:
    return db.get(User, user_id)


def get_all_users(db: Session) -> list[User]:
    return db.query(User).all()


def get_user_by_google_sub(db: Session, google_sub: str) -> User | None:
    return db.query(User).filter(User.google_sub == google_sub).first()


def upsert_user(
    db: Session,
    *,
    google_sub: str,
    email: str,
    name: str | None,
    encrypted_refresh_token: str,
) -> User:
    user = get_user_by_google_sub(db, google_sub)
    if user is None:
        user = User(
            google_sub=google_sub,
            email=email,
            name=name,
            encrypted_refresh_token=encrypted_refresh_token,
        )
        db.add(user)
    else:
        user.email = email
        user.name = name
        # Google only returns a refresh token on the first consent (or when
        # prompt=consent forces re-consent). Only overwrite if we got a new one.
        if encrypted_refresh_token:
            user.encrypted_refresh_token = encrypted_refresh_token

    db.commit()
    db.refresh(user)
    return user


def get_or_create_sync_state(db: Session, user_id: int) -> SyncState:
    sync_state = db.query(SyncState).filter(SyncState.user_id == user_id).first()
    if sync_state is None:
        sync_state = SyncState(user_id=user_id)
        db.add(sync_state)
        db.commit()
        db.refresh(sync_state)
    return sync_state


def update_sync_state(
    db: Session,
    sync_state: SyncState,
    *,
    last_history_id: str | None,
    last_synced_at: datetime,
) -> SyncState:
    sync_state.last_history_id = last_history_id
    sync_state.last_synced_at = last_synced_at
    db.commit()
    db.refresh(sync_state)
    return sync_state


def _parse_deadline(deadline: str | None) -> datetime | None:
    if not deadline:
        return None
    try:
        return datetime.fromisoformat(deadline)
    except ValueError:
        return None


def create_email_summary(
    db: Session,
    *,
    user_id: int,
    summary_text: str,
    source_message_ids: list[str],
) -> EmailSummary:
    summary = EmailSummary(
        user_id=user_id,
        summary_text=summary_text,
        source_message_ids=source_message_ids,
    )
    db.add(summary)
    db.commit()
    db.refresh(summary)
    return summary


def create_action_items(
    db: Session,
    *,
    user_id: int,
    summary_id: int,
    items: list[dict],
) -> list[ActionItem]:
    existing_items = get_open_action_items(db, user_id)
    existing_descs = {
        re.sub(r"\W+", "", item.description.lower()) for item in existing_items
    }

    rows = []
    seen_batch = set()
    for item in items:
        norm_desc = re.sub(r"\W+", "", item["description"].lower())
        if norm_desc in existing_descs or norm_desc in seen_batch:
            continue
        seen_batch.add(norm_desc)
        rows.append(
            ActionItem(
                user_id=user_id,
                summary_id=summary_id,
                description=item["description"],
                deadline=_parse_deadline(item.get("deadline")),
                source_message_id=item["source_message_id"],
                source_sender=item.get("source_sender"),
                priority=item.get("priority"),
                related_person=item.get("related_person"),
            )
        )

    if rows:
        db.add_all(rows)
        db.commit()
        for row in rows:
            db.refresh(row)
    return rows


def create_email_triage(
    db: Session,
    *,
    user_id: int,
    summary_id: int,
    items: list[dict],
    email_dates: dict[str, datetime] | None = None,
) -> list[EmailTriage]:
    dates_map = email_dates or {}
    rows = [
        EmailTriage(
            user_id=user_id,
            summary_id=summary_id,
            message_id=item["message_id"],
            sender=item.get("sender"),
            subject=item.get("subject"),
            priority=item["priority"],
            category=item.get("category"),
            suggested_action=item.get("suggested_action"),
            deadline=_parse_deadline(item.get("deadline")),
            email_date=_to_naive(dates_map.get(item["message_id"])),
        )
        for item in items
    ]
    db.add_all(rows)
    db.commit()
    for row in rows:
        db.refresh(row)
    return rows


def get_recent_triage(db: Session, user_id: int, *, limit: int = 200) -> list[EmailTriage]:
    from sqlalchemy import nullslast
    return (
        db.query(EmailTriage)
        .filter(EmailTriage.user_id == user_id)
        .order_by(nullslast(EmailTriage.email_date.desc()), EmailTriage.id.desc())
        .limit(limit)
        .all()
    )


def get_latest_summary(db: Session, user_id: int) -> EmailSummary | None:
    return (
        db.query(EmailSummary)
        .filter(EmailSummary.user_id == user_id)
        .order_by(EmailSummary.run_at.desc())
        .first()
    )


def _deduplicate_action_items(items: list[ActionItem]) -> list[ActionItem]:
    unique = []
    seen_keys = set()
    for item in items:
        person = (item.related_person or item.source_sender or "").lower().strip()
        desc = item.description.lower()

        # If a person/sender name is present, key on person to prevent duplicate invites/requests
        if person and len(person) > 3:
            key = f"person:{person}"
        else:
            # Strip common variation words like request, invitation, invite, reminder, notice
            simplified = re.sub(r"\b(request|invitation|invite|reminder|notice|taken|document)\b", "", desc)
            key = f"desc:{re.sub(r'\W+', '', simplified)}"

        if key not in seen_keys:
            seen_keys.add(key)
            unique.append(item)
    return unique


def get_open_action_items(db: Session, user_id: int) -> list[ActionItem]:
    """All not-yet-resolved action items for a user, across every past sync —
    the persistent to-do list the dashboard shows, not just the latest batch."""
    raw = (
        db.query(ActionItem)
        .filter(ActionItem.user_id == user_id, ActionItem.status == ActionItemStatus.open)
        .order_by(ActionItem.deadline.is_(None), ActionItem.deadline, ActionItem.id)
        .all()
    )
    return _deduplicate_action_items(raw)


def get_action_items(db: Session, user_id: int, *, include_done: bool = False) -> list[ActionItem]:
    """Open items, optionally including completed ones (for the 'show done' toggle)."""
    query = db.query(ActionItem).filter(ActionItem.user_id == user_id)
    if not include_done:
        query = query.filter(ActionItem.status == ActionItemStatus.open)
    else:
        query = query.filter(ActionItem.status != ActionItemStatus.dismissed)
    raw = query.order_by(ActionItem.deadline.is_(None), ActionItem.deadline, ActionItem.id).all()
    return _deduplicate_action_items(raw)


def set_action_item_status(
    db: Session, user_id: int, action_item_id: int, status: ActionItemStatus
) -> ActionItem | None:
    item = (
        db.query(ActionItem)
        .filter(ActionItem.id == action_item_id, ActionItem.user_id == user_id)
        .first()
    )
    if item is None:
        return None
    item.status = status
    db.commit()
    db.refresh(item)
    return item


def get_sync_state(db: Session, user_id: int) -> SyncState | None:
    return db.query(SyncState).filter(SyncState.user_id == user_id).first()


def get_recent_sent_drafts_to(
    db: Session, user_id: int, to_addr: str, *, limit: int = 3
) -> list[DraftEmail]:
    """Past sent drafts to the same recipient, most recent first — used to give
    the compose/reply agent (and the user) context on prior correspondence."""
    if not to_addr:
        return []
    return (
        db.query(DraftEmail)
        .filter(
            DraftEmail.user_id == user_id,
            DraftEmail.status == DraftStatus.sent,
            DraftEmail.to_addr == to_addr,
        )
        .order_by(DraftEmail.updated_at.desc())
        .limit(limit)
        .all()
    )


def create_draft_email(
    db: Session, *, user_id: int, to_addr: str, subject: str, body: str
) -> DraftEmail:
    draft = DraftEmail(user_id=user_id, to_addr=to_addr, subject=subject, body=body)
    db.add(draft)
    db.commit()
    db.refresh(draft)
    return draft


def get_draft_by_id(db: Session, user_id: int, draft_id: int) -> DraftEmail | None:
    return (
        db.query(DraftEmail)
        .filter(DraftEmail.id == draft_id, DraftEmail.user_id == user_id)
        .first()
    )


def update_draft_email(
    db: Session, user_id: int, draft_id: int, *, to_addr: str, subject: str, body: str
) -> DraftEmail | None:
    draft = (
        db.query(DraftEmail)
        .filter(DraftEmail.id == draft_id, DraftEmail.user_id == user_id)
        .first()
    )
    if draft is None:
        return None
    draft.to_addr = to_addr
    draft.subject = subject
    draft.body = body
    db.commit()
    db.refresh(draft)
    return draft


def set_draft_status(db: Session, user_id: int, draft_id: int, status: DraftStatus) -> DraftEmail | None:
    draft = (
        db.query(DraftEmail)
        .filter(DraftEmail.id == draft_id, DraftEmail.user_id == user_id)
        .first()
    )
    if draft is None:
        return None
    draft.status = status
    db.commit()
    db.refresh(draft)
    return draft


def acknowledge_triage_item(db: Session, user_id: int, triage_id: int) -> EmailTriage | None:
    item = (
        db.query(EmailTriage)
        .filter(EmailTriage.id == triage_id, EmailTriage.user_id == user_id)
        .first()
    )
    if item is None:
        return None
    item.priority = TriagePriority.informational
    
    # Also mark any associated action items as done
    action_items = (
        db.query(ActionItem)
        .filter(ActionItem.user_id == user_id, ActionItem.source_message_id == item.message_id)
        .all()
    )
    for act in action_items:
        act.status = ActionItemStatus.done

    db.commit()
    db.refresh(item)
    return item


def _to_naive(dt: datetime | None) -> datetime | None:
    if dt is None:
        return None
    if dt.tzinfo is not None:
        return dt.astimezone(timezone.utc).replace(tzinfo=None)
    return dt


def get_daily_briefing_data(db: Session, user_id: int) -> dict:
    """Aggregates everything the 'Daily Briefing' view needs from data
    already persisted by the regular sync — no extra LLM call required."""
    now = datetime.utcnow()
    since_24h = now - timedelta(hours=24)
    today = now.date()

    all_triage = db.query(EmailTriage).filter(EmailTriage.user_id == user_id).all()
    recent_24h_triage = [
        row for row in all_triage
        if row.created_at and _to_naive(row.created_at) >= since_24h
    ]

    counts: dict[str, int] = {p.value: 0 for p in TriagePriority}
    for row in all_triage:
        p_val = row.priority.value if hasattr(row.priority, "value") else row.priority
        counts[p_val] = counts.get(p_val, 0) + 1

    open_items = get_action_items(db, user_id, include_done=False)

    # Deduplicate open items using smart matching
    unique_open = _deduplicate_action_items(open_items)

    overdue = sorted(
        (item for item in unique_open if item.deadline and _to_naive(item.deadline).date() < today),
        key=lambda item: _to_naive(item.deadline),
    )
    todays_deadlines = sorted(
        (item for item in unique_open if item.deadline and _to_naive(item.deadline).date() == today),
        key=lambda item: _to_naive(item.deadline),
    )
    suggested = sorted(
        unique_open,
        key=lambda item: (item.deadline is None, _to_naive(item.deadline) or now),
    )[:10]

    return {
        "new_email_count": len(recent_24h_triage),
        "total_email_count": len(all_triage),
        "priority_counts": counts,
        "action_required_count": len(unique_open),
        "overdue": overdue,
        "todays_deadlines": todays_deadlines,
        "suggested_actions": suggested,
    }


def count_sent_drafts_since(db: Session, user_id: int, since: datetime) -> int:
    return (
        db.query(DraftEmail)
        .filter(
            DraftEmail.user_id == user_id,
            DraftEmail.status == DraftStatus.sent,
            DraftEmail.updated_at >= since,
        )
        .count()
    )


def get_user_contacts(db: Session, user_id: int) -> list[str]:
    """Harvests unique email contacts from past triaged senders and draft recipients.
    Extracts and stores ONLY clean email addresses (no names), ensuring no duplication."""
    from email.utils import parseaddr

    contacts_set = set()
    triage_senders = (
        db.query(EmailTriage.sender)
        .filter(EmailTriage.user_id == user_id)
        .distinct()
        .all()
    )
    for (sender,) in triage_senders:
        if sender and sender.strip():
            _, addr = parseaddr(sender)
            addr_clean = addr.strip().lower() if addr else sender.strip().lower()
            if addr_clean and "@" in addr_clean:
                contacts_set.add(addr_clean)

    draft_recipients = (
        db.query(DraftEmail.to_addr)
        .filter(DraftEmail.user_id == user_id)
        .distinct()
        .all()
    )
    for (to_addr,) in draft_recipients:
        if to_addr and to_addr.strip():
            _, addr = parseaddr(to_addr)
            addr_clean = addr.strip().lower() if addr else to_addr.strip().lower()
            if addr_clean and "@" in addr_clean:
                contacts_set.add(addr_clean)

    return sorted(list(contacts_set))