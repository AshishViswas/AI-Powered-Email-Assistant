from datetime import datetime
from pydantic import BaseModel, ConfigDict
from app.db.models import ActionItemStatus, DraftStatus, EmailCategory, TriagePriority


class UserSchema(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    email: str
    name: str | None = None


class ActionItemSchema(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    summary_id: int
    description: str
    deadline: datetime | None = None
    source_message_id: str
    source_sender: str | None = None
    priority: TriagePriority | str | None = None
    related_person: str | None = None
    status: ActionItemStatus | str
    created_at: datetime | None = None


class TriageSchema(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    summary_id: int
    message_id: str
    sender: str | None = None
    subject: str | None = None
    priority: TriagePriority | str
    category: EmailCategory | str | None = None
    suggested_action: str | None = None
    deadline: datetime | None = None
    email_date: datetime | None = None
    created_at: datetime


class EmailSummarySchema(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    summary_text: str
    source_message_ids: list[str]
    run_at: datetime


class SyncStateSchema(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    last_history_id: str | None = None
    last_synced_at: datetime | None = None


class BriefingResponseSchema(BaseModel):
    new_email_count: int
    total_email_count: int
    priority_counts: dict[str, int]
    action_required_count: int
    overdue: list[ActionItemSchema]
    todays_deadlines: list[ActionItemSchema]
    suggested_actions: list[ActionItemSchema]
    latest_summary: EmailSummarySchema | None = None
    sync_state: SyncStateSchema | None = None


class UpdateTaskStatusSchema(BaseModel):
    status: ActionItemStatus


class ComposeDraftRequest(BaseModel):
    instructions: str
    target_email: str = ""
    subject: str = ""


class ComposeReplyRequest(BaseModel):
    message_id: str
    prompt: str


class RefineDraftRequest(BaseModel):
    feedback: str


class SendDraftRequest(BaseModel):
    to_addr: str


class DraftSchema(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    user_id: int
    to_addr: str
    subject: str
    body: str
    status: DraftStatus | str
    created_at: datetime
    updated_at: datetime


class SyncResponseSchema(BaseModel):
    synced: bool
    summary_id: int | None = None
    action_items_count: int = 0
    triaged_count: int = 0
    message: str
