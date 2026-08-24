from sqlalchemy import create_engine, inspect, text
from sqlalchemy.orm import Session, sessionmaker

from app.config import settings
from app.db.models import Base

connect_args = {"check_same_thread": False} if settings.DATABASE_URL.startswith("sqlite") else {}
engine = create_engine(settings.DATABASE_URL, connect_args=connect_args)
SessionLocal = sessionmaker(bind=engine, autoflush=False, autocommit=False)


def _add_missing_nullable_columns() -> None:
    """MVP-friendly stand-in for a real migration tool: create_all() only adds
    missing tables, not missing columns on tables that already exist. This
    adds any new *nullable* columns the models define but an existing SQLite
    file predates, so local DBs don't need to be deleted after a schema
    change.
    """
    inspector = inspect(engine)
    for table in Base.metadata.sorted_tables:
        if not inspector.has_table(table.name):
            continue
        existing_columns = {col["name"] for col in inspector.get_columns(table.name)}
        for column in table.columns:
            if column.name in existing_columns or not column.nullable:
                continue
            column_type = column.type.compile(dialect=engine.dialect)
            with engine.begin() as conn:
                conn.execute(text(f"ALTER TABLE {table.name} ADD COLUMN {column.name} {column_type}"))


def init_db() -> None:
    Base.metadata.create_all(bind=engine)
    _add_missing_nullable_columns()
    if settings.DATABASE_URL.startswith("sqlite"):
        try:
            with engine.begin() as conn:
                conn.execute(text("PRAGMA journal_mode=WAL;"))
        except Exception:
            pass


def get_session() -> Session:
    return SessionLocal()
