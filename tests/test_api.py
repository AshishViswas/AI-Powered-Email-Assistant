import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

import app.db.session as db_session_module
from app.api.routes import get_db
from app.auth.session import create_session_token
from app.db.crud import create_action_items, create_email_summary, upsert_user
from app.db.models import Base
from app.main import app

# Create in-memory SQLite test database with StaticPool for thread-sharing
TEST_DATABASE_URL = "sqlite:///:memory:"
test_engine = create_engine(
    TEST_DATABASE_URL,
    connect_args={"check_same_thread": False},
    poolclass=StaticPool,
)
TestingSessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=test_engine)


def _override_get_db():
    db = TestingSessionLocal()
    try:
        yield db
    finally:
        db.close()


@pytest.fixture(autouse=True)
def setup_database(monkeypatch):
    Base.metadata.create_all(bind=test_engine)
    monkeypatch.setattr(db_session_module, "engine", test_engine)
    monkeypatch.setattr(db_session_module, "SessionLocal", TestingSessionLocal)
    monkeypatch.setattr(db_session_module, "get_session", lambda: TestingSessionLocal())
    app.dependency_overrides[get_db] = _override_get_db
    yield
    app.dependency_overrides.clear()
    Base.metadata.drop_all(bind=test_engine)


@pytest.fixture
def client():
    return TestClient(app)


def test_auth_login_redirect(client):
    response = client.get("/auth/login", follow_redirects=False)
    assert response.status_code in (302, 307, 303, 308)
    assert "accounts.google.com" in response.headers.get("location", "")


def test_health_endpoint(client):
    response = client.get("/api/health")
    assert response.status_code == 200
    assert response.json() == {"status": "ok", "service": "gmail-agent-api"}


def test_unauthenticated_api_access_returns_401(client):
    response = client.get("/api/me")
    assert response.status_code == 401
    assert response.json()["detail"] == "Session token missing or invalid; please log in."


def test_invalid_session_token_returns_401(client):
    headers = {"Authorization": "Bearer invalid_token_123"}
    response = client.get("/api/me", headers=headers)
    assert response.status_code == 401


def test_authenticated_api_me(client):
    db = TestingSessionLocal()
    user = upsert_user(
        db,
        google_sub="test_sub_123",
        email="testuser@example.com",
        name="Test User",
        encrypted_refresh_token="mock_enc_token",
    )
    user_id = user.id
    db.close()

    token = create_session_token(user_id)
    headers = {"Authorization": f"Bearer {token}"}

    response = client.get("/api/me", headers=headers)
    assert response.status_code == 200
    data = response.json()
    assert data["id"] == user_id
    assert data["email"] == "testuser@example.com"
    assert data["name"] == "Test User"


def test_api_briefing_and_tasks(client):
    db = TestingSessionLocal()
    user = upsert_user(
        db,
        google_sub="sub_briefing",
        email="briefing@example.com",
        name="Briefing User",
        encrypted_refresh_token="mock_token",
    )
    user_id = user.id
    summary = create_email_summary(
        db, user_id=user_id, summary_text="Executive Summary Point 1", source_message_ids=["m1"]
    )
    action_items = create_action_items(
        db,
        user_id=user_id,
        summary_id=summary.id,
        items=[
            {
                "description": "Review quarterly budget",
                "source_message_id": "m1",
                "priority": "urgent",
            }
        ],
    )
    db.close()

    token = create_session_token(user_id)
    headers = {"Authorization": f"Bearer {token}"}

    # Test GET /api/briefing
    briefing_resp = client.get("/api/briefing", headers=headers)
    assert briefing_resp.status_code == 200
    briefing_data = briefing_resp.json()
    assert briefing_data["latest_summary"]["summary_text"] == "Executive Summary Point 1"
    assert len(briefing_data["suggested_actions"]) == 1
    assert briefing_data["suggested_actions"][0]["description"] == "Review quarterly budget"

    # Test GET /api/tasks
    tasks_resp = client.get("/api/tasks", headers=headers)
    assert tasks_resp.status_code == 200
    tasks = tasks_resp.json()
    assert len(tasks) == 1
    task_id = tasks[0]["id"]
    assert tasks[0]["status"] == "open"

    # Test PATCH /api/tasks/{task_id}
    patch_resp = client.patch(f"/api/tasks/{task_id}", json={"status": "done"}, headers=headers)
    assert patch_resp.status_code == 200
    assert patch_resp.json()["status"] == "done"


def test_user_isolation_security(client):
    db = TestingSessionLocal()
    user1 = upsert_user(
        db, google_sub="sub_user1", email="user1@example.com", name="User 1", encrypted_refresh_token="tok1"
    )
    user2 = upsert_user(
        db, google_sub="sub_user2", email="user2@example.com", name="User 2", encrypted_refresh_token="tok2"
    )
    user1_id = user1.id
    user2_id = user2.id
    summary = create_email_summary(
        db, user_id=user1_id, summary_text="User 1 Summary", source_message_ids=["m1"]
    )
    items = create_action_items(
        db,
        user_id=user1_id,
        summary_id=summary.id,
        items=[{"description": "User 1 private task", "source_message_id": "m1"}],
    )
    user1_task_id = items[0].id
    db.close()

    # User 2 token attempting to modify User 1 task
    token_user2 = create_session_token(user2_id)
    headers_user2 = {"Authorization": f"Bearer {token_user2}"}

    patch_resp = client.patch(f"/api/tasks/{user1_task_id}", json={"status": "done"}, headers=headers_user2)
    assert patch_resp.status_code == 404