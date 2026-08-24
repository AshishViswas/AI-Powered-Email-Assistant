import enum
from datetime import datetime

from sqlalchemy import JSON, Boolean, DateTime, Enum, ForeignKey, Integer, String, Text, UniqueConstraint
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column, relationship


class Base(DeclarativeBase):
    pass


class DraftStatus(str, enum.Enum):
    pending = "pending"
    sent = "sent"
    discarded = "discarded"


class ActionItemStatus(str, enum.Enum):
    open = "open"
    done = "done"
    dismissed = "dismissed"


class TriagePriority(str, enum.Enum):
    urgent = "urgent"
    action = "action"
    important = "important"
    informational = "informational"
    newsletter = "newsletter"
    low = "low"


class EmailCategory(str, enum.Enum):
    security = "security"
    billing = "billing"
    shipping = "shipping"
    travel = "travel"
    promotional = "promotional"
    newsletter = "newsletter"
    spam = "spam"
    personal = "personal"
    work = "work"
    other = "other"


class User(Base):
    __tablename__ = "users"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    email: Mapped[str] = mapped_column(String, unique=True, nullable=False)
    google_sub: Mapped[str] = mapped_column(String, unique=True, nullable=False)
    name: Mapped[str | None] = mapped_column(String, nullable=True)
    encrypted_refresh_token: Mapped[str] = mapped_column(Text, nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)

    sync_state: Mapped["SyncState"] = relationship(back_populates="user", uselist=False)


class SyncState(Base):
    __tablename__ = "sync_state"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id"), unique=True, nullable=False)
    last_history_id: Mapped[str | None] = mapped_column(String, nullable=True)
    last_synced_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)

    user: Mapped["User"] = relationship(back_populates="sync_state")


class EmailSummary(Base):
    __tablename__ = "email_summaries"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id"), nullable=False)
    run_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)
    summary_text: Mapped[str] = mapped_column(Text, nullable=False)
    source_message_ids: Mapped[list] = mapped_column(JSON, nullable=False)


class ActionItem(Base):
    __tablename__ = "action_items"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id"), nullable=False)
    summary_id: Mapped[int] = mapped_column(ForeignKey("email_summaries.id"), nullable=False)
    description: Mapped[str] = mapped_column(Text, nullable=False)
    deadline: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    source_message_id: Mapped[str] = mapped_column(String, nullable=False)
    source_sender: Mapped[str | None] = mapped_column(String, nullable=True)
    priority: Mapped[TriagePriority | None] = mapped_column(Enum(TriagePriority), nullable=True)
    related_person: Mapped[str | None] = mapped_column(String, nullable=True)
    status: Mapped[ActionItemStatus] = mapped_column(
        Enum(ActionItemStatus), default=ActionItemStatus.open, nullable=False
    )


class EmailTriage(Base):
    """One row per fetched email (whether actionable or not), classified into
    a priority bucket — the 'Smart Email Triage' feature. Distinct from
    ActionItem, which only covers emails that need the user to personally do
    something; this table covers the whole inbox for the triage view."""

    __tablename__ = "email_triage"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id"), nullable=False)
    summary_id: Mapped[int] = mapped_column(ForeignKey("email_summaries.id"), nullable=False)
    message_id: Mapped[str] = mapped_column(String, nullable=False)
    sender: Mapped[str | None] = mapped_column(String, nullable=True)
    subject: Mapped[str | None] = mapped_column(String, nullable=True)
    priority: Mapped[TriagePriority] = mapped_column(Enum(TriagePriority), nullable=False)
    category: Mapped[EmailCategory | None] = mapped_column(Enum(EmailCategory), nullable=True)
    suggested_action: Mapped[str | None] = mapped_column(Text, nullable=True)
    deadline: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    email_date: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)


class DraftEmail(Base):
    __tablename__ = "draft_emails"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id"), nullable=False)
    to_addr: Mapped[str] = mapped_column(String, nullable=False)
    subject: Mapped[str] = mapped_column(String, nullable=False)
    body: Mapped[str] = mapped_column(Text, nullable=False)
    status: Mapped[DraftStatus] = mapped_column(Enum(DraftStatus), default=DraftStatus.pending, nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)
    updated_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)
