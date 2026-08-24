from typing import Generator
from fastapi import APIRouter, Depends, HTTPException, Query, Request, status
from sqlalchemy.orm import Session

from app.agents.compose_agent import compose_draft, compose_reply, refine_draft
from app.auth.session import get_user_id_from_request
from app.db.crud import (
    acknowledge_triage_item,
    get_action_items,
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
from app.db.models import ActionItemStatus, DraftStatus, User
from app.db.session import get_session
from app.gmail.client import get_message, send_email
from app.scheduler import run_sync_for_user

from app.api.schemas import (
    ActionItemSchema,
    BriefingResponseSchema,
    ComposeDraftRequest,
    ComposeReplyRequest,
    DraftSchema,
    EmailSummarySchema,
    RefineDraftRequest,
    SendDraftRequest,
    SyncResponseSchema,
    SyncStateSchema,
    TriageSchema,
    UpdateTaskStatusSchema,
    UserSchema,
)

router = APIRouter(prefix="/api", tags=["api"])


def get_db() -> Generator[Session, None, None]:
    db = app.db.session.get_session()
    try:
        yield db
    finally:
        db.close()


def get_current_user(request: Request, db: Session = Depends(get_db)) -> User:
    user_id = get_user_id_from_request(request)
    if not user_id:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Session token missing or invalid; please log in.",
        )
    user = get_user_by_id(db, user_id)
    if not user:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Authenticated user not found.",
        )
    return user


@router.get("/me", response_model=UserSchema)
def get_me(current_user: User = Depends(get_current_user)):
    return current_user


@router.get("/briefing", response_model=BriefingResponseSchema)
def get_briefing(
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    briefing_data = get_daily_briefing_data(db, current_user.id)
    latest_summary = get_latest_summary(db, current_user.id)
    sync_state = get_sync_state(db, current_user.id)

    return BriefingResponseSchema(
        new_email_count=briefing_data["new_email_count"],
        total_email_count=briefing_data.get("total_email_count", briefing_data["new_email_count"]),
        priority_counts=briefing_data["priority_counts"],
        action_required_count=briefing_data["action_required_count"],
        overdue=[ActionItemSchema.model_validate(item) for item in briefing_data["overdue"]],
        todays_deadlines=[ActionItemSchema.model_validate(item) for item in briefing_data["todays_deadlines"]],
        suggested_actions=[ActionItemSchema.model_validate(item) for item in briefing_data["suggested_actions"]],
        latest_summary=EmailSummarySchema.model_validate(latest_summary) if latest_summary else None,
        sync_state=SyncStateSchema.model_validate(sync_state) if sync_state else None,
    )


@router.get("/inbox", response_model=list[TriageSchema])
def get_inbox(
    priority: str | None = Query(None),
    search: str | None = Query(None),
    limit: int = Query(200, ge=1, le=500),
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    recent_triages = get_recent_triage(db, current_user.id, limit=limit)

    if priority and priority != "All":
        recent_triages = [
            t
            for t in recent_triages
            if (t.priority.value if hasattr(t.priority, "value") else str(t.priority)) == priority
        ]

    if search and search.strip():
        sq = search.strip().lower()
        recent_triages = [
            t
            for t in recent_triages
            if sq in (t.subject or "").lower()
            or sq in (t.sender or "").lower()
            or sq in (t.category.value if hasattr(t.category, "value") else str(t.category or "")).lower()
        ]

    return [TriageSchema.model_validate(t) for t in recent_triages]


@router.get("/inbox/message/{message_id}")
def get_inbox_message(
    message_id: str,
    current_user: User = Depends(get_current_user),
):
    try:
        return get_message(current_user, message_id)
    except Exception as exc:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"Failed to retrieve email message: {exc}",
        )


@router.patch("/inbox/{triage_id}/acknowledge", response_model=TriageSchema)
def acknowledge_triage(
    triage_id: int,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    item = acknowledge_triage_item(db, current_user.id, triage_id)
    if not item:
        raise HTTPException(status_code=404, detail="Triage item not found.")
    return TriageSchema.model_validate(item)


@router.get("/tasks", response_model=list[ActionItemSchema])
def get_tasks(
    include_done: bool = Query(False),
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    tasks = get_action_items(db, current_user.id, include_done=include_done)
    return [ActionItemSchema.model_validate(task) for task in tasks]


@router.patch("/tasks/{task_id}", response_model=ActionItemSchema)
def update_task_status(
    task_id: int,
    payload: UpdateTaskStatusSchema,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    updated = set_action_item_status(db, current_user.id, task_id, payload.status)
    if not updated:
        raise HTTPException(status_code=404, detail="Action item not found.")
    return ActionItemSchema.model_validate(updated)


@router.post("/sync", response_model=SyncResponseSchema)
def trigger_sync(
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    try:
        res = run_sync_for_user(db, current_user)
        if res:
            summary_row, action_items, triage_rows = res
            return SyncResponseSchema(
                synced=True,
                summary_id=summary_row.id,
                action_items_count=len(action_items),
                triaged_count=len(triage_rows),
                message=f"Synced {len(triage_rows)} new emails!",
            )
        else:
            return SyncResponseSchema(
                synced=False,
                message="Inbox up to date — no new emails found.",
            )
    except Exception as exc:
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Sync failed: {exc}",
        )


@router.get("/contacts", response_model=list[str])
def get_contacts(
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    return get_user_contacts(db, current_user.id)


@router.post("/drafts/compose", response_model=DraftSchema)
def create_composed_draft(
    payload: ComposeDraftRequest,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    if not payload.instructions.strip():
        raise HTTPException(status_code=400, detail="Prompt instructions cannot be empty.")

    full_instructions = payload.instructions
    if payload.target_email.strip():
        full_instructions = f"Send to: {payload.target_email}\nSubject: {payload.subject}\nInstructions: {payload.instructions}"
    elif payload.subject.strip():
        full_instructions = f"Subject: {payload.subject}\nInstructions: {payload.instructions}"

    try:
        draft_id = compose_draft(db, current_user.id, full_instructions)
        draft = get_draft_by_id(db, current_user.id, draft_id)
        if not draft:
            raise HTTPException(status_code=500, detail="Failed to locate created draft.")
        return DraftSchema.model_validate(draft)
    except Exception as exc:
        raise HTTPException(status_code=500, detail=f"Error composing draft: {exc}")


@router.post("/drafts/reply", response_model=DraftSchema)
def create_reply_draft(
    payload: ComposeReplyRequest,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    try:
        draft_id = compose_reply(current_user, db, payload.message_id, payload.prompt)
        draft = get_draft_by_id(db, current_user.id, draft_id)
        if not draft:
            raise HTTPException(status_code=500, detail="Failed to locate created reply draft.")
        return DraftSchema.model_validate(draft)
    except Exception as exc:
        raise HTTPException(status_code=500, detail=f"Error composing reply: {exc}")


@router.get("/drafts/{draft_id}", response_model=DraftSchema)
def get_draft(
    draft_id: int,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    draft = get_draft_by_id(db, current_user.id, draft_id)
    if not draft:
        raise HTTPException(status_code=404, detail="Draft not found.")
    return DraftSchema.model_validate(draft)


@router.post("/drafts/{draft_id}/refine", response_model=DraftSchema)
def refine_existing_draft(
    draft_id: int,
    payload: RefineDraftRequest,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    if not payload.feedback.strip():
        raise HTTPException(status_code=400, detail="Refinement feedback cannot be empty.")
    try:
        updated = refine_draft(db, current_user.id, draft_id, payload.feedback)
        if not updated:
            raise HTTPException(status_code=404, detail="Draft not found.")
        return DraftSchema.model_validate(updated)
    except Exception as exc:
        raise HTTPException(status_code=500, detail=f"Draft refinement error: {exc}")


@router.post("/drafts/{draft_id}/send")
def send_existing_draft(
    draft_id: int,
    payload: SendDraftRequest,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    draft = get_draft_by_id(db, current_user.id, draft_id)
    if not draft:
        raise HTTPException(status_code=404, detail="Draft not found.")

    to_addr = payload.to_addr.strip() or draft.to_addr
    if not to_addr:
        raise HTTPException(status_code=400, detail="Recipient email address ('to') is required.")

    try:
        msg_id = send_email(
            current_user,
            to_addr=to_addr,
            subject=draft.subject,
            body=draft.body,
        )
        set_draft_status(db, current_user.id, draft.id, DraftStatus.sent)
        return {"message_id": msg_id, "status": "sent"}
    except Exception as exc:
        raise HTTPException(status_code=500, detail=f"Send failed: {exc}")


@router.post("/drafts/{draft_id}/discard", response_model=DraftSchema)
def discard_existing_draft(
    draft_id: int,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    draft = set_draft_status(db, current_user.id, draft_id, DraftStatus.discarded)
    if not draft:
        raise HTTPException(status_code=404, detail="Draft not found.")
    return DraftSchema.model_validate(draft)
