# Gmail Agent — Production Deployment & Backend Refactor Plan

## Purpose

This document is the implementation specification for the coding agent working on this repository.

The goal is to make the Gmail Agent deployable as:

```text
Google OAuth
     |
     v
FastAPI backend on Render
     |
     +---- owns SQLite database on Render Persistent Disk
     |
     +---- owns Gmail synchronization / APScheduler
     |
     v
Streamlit UI on Streamlit Community Cloud
```

The Streamlit UI must **not directly open the Render SQLite database**.

This architecture is intentionally similar to the user's existing `Legal-Contract-RAG` project, where the API layer owns the persistent data/index layer and the UI communicates with the API.

---

# 1. Current repository findings

The current repository contains:

```text
app/
├── agents/
├── auth/
│   ├── google_oauth.py
│   └── session.py
├── db/
│   ├── crud.py
│   ├── models.py
│   └── session.py
├── gmail/
├── ui/
│   ├── gradio_app.py
│   └── streamlit_app.py
├── config.py
├── main.py
└── scheduler.py

run.py
render.yaml
streamlit_app.py
requirements.txt
```

The current application already has a good FastAPI backend:

- `app/main.py`
- `app/auth/google_oauth.py`
- `app/auth/session.py`
- `app/scheduler.py`
- `app/db/*`

The local launcher `run.py` starts:

```text
FastAPI :7860
Streamlit :8501
```

This explains why local OAuth works with:

```text
http://localhost:7860/auth/callback
```

The production problem occurred because only Streamlit was deployed, while Google was redirected to:

```text
https://ai-gmail-assistant.streamlit.app/auth/callback
```

The OAuth callback actually belongs to FastAPI.

---

# 2. Target production architecture

Use this architecture:

```text
                           Google
                             |
                             | OAuth 2.0 + PKCE
                             v
                  +-----------------------+
                  | FastAPI on Render     |
                  |                       |
                  | /auth/login           |
                  | /auth/callback        |
                  | /auth/logout          |
                  | API endpoints         |
                  | Gmail sync             |
                  | APScheduler            |
                  +-----------+-----------+
                              |
                              v
                    /data/gmail.db
                    Render Persistent Disk
                              ^
                              |
                              | HTTP API
                              |
                  +-----------+-----------+
                  | Streamlit Cloud       |
                  |                       |
                  | Dashboard/UI           |
                  +-----------------------+
```

The production services are:

```text
FastAPI:
https://<actual-render-service>.onrender.com

Streamlit:
https://ai-gmail-assistant.streamlit.app
```

The exact Render URL is unknown until the service is created.

---

# 3. Database decision

## Use SQLite for now

Do NOT introduce PostgreSQL unless required later.

The repository currently uses:

```text
DATABASE_URL=sqlite:///./gmail.db
```

Locally, this is fine because FastAPI and Streamlit run on the same machine.

For Render production, use:

```text
DATABASE_URL=sqlite:////data/gmail.db
```

with a Render Persistent Disk mounted at:

```text
/data
```

The database must therefore be owned by the FastAPI service.

## Important

Do NOT make Streamlit Cloud directly execute:

```python
get_session()
```

against the production SQLite database.

Streamlit Cloud and Render are different machines/services.

The Streamlit UI must retrieve application data through FastAPI endpoints.

---

# 4. OAuth architecture

## FastAPI owns OAuth

`app/auth/google_oauth.py` already contains the correct OAuth implementation:

```text
/auth/login
/auth/callback
/auth/logout
```

Keep this implementation.

It already uses:

- Google OAuth 2.0
- PKCE
- OAuth state cookie
- code-verifier cookie
- encrypted refresh tokens
- session token creation

Do not create a second OAuth implementation in Streamlit.

## Production flow

The final flow must be:

```text
User
  |
  v
Streamlit
  |
  | GET https://<backend>/auth/login
  v
FastAPI
  |
  v
Google
  |
  | redirect
  v
FastAPI /auth/callback
  |
  +-- verify state
  +-- verify PKCE
  +-- exchange authorization code
  +-- verify ID token
  +-- upsert user in SQLite
  +-- create signed session token
  |
  v
Streamlit ?session=<signed-token>
```

Google must NEVER redirect to:

```text
https://ai-gmail-assistant.streamlit.app/auth/callback
```

in the final architecture.

---

# 5. Required OAuth configuration

## Local

Keep:

```text
GOOGLE_REDIRECT_URI=http://localhost:7860/auth/callback
```

Google Cloud Authorized Redirect URI:

```text
http://localhost:7860/auth/callback
```

## Production

After Render creates the backend, suppose it gives:

```text
https://gmail-agent.onrender.com
```

Then:

```text
GOOGLE_REDIRECT_URI=https://gmail-agent.onrender.com/auth/callback
```

Google Cloud Authorized Redirect URI:

```text
https://gmail-agent.onrender.com/auth/callback
```

The exact Render URL must be used.

Do not invent or hardcode the URL before deployment.

---

# 6. Required Streamlit authentication changes

File:

```text
app/ui/streamlit_app.py
```

## Remove the duplicate Streamlit OAuth implementation

Delete the entire block that:

- checks `if "code" in st.query_params`
- creates a `google_auth_oauthlib.flow.Flow` directly in Streamlit
- calls `flow.fetch_token()`
- verifies Google ID tokens directly in Streamlit
- calls `upsert_user()` directly from Streamlit
- creates a session after direct OAuth processing
- contains `_get_effective_redirect_uri()`

Streamlit must not own the Google OAuth callback.

## Replace `_get_login_url()`

It should be:

```python
def _get_login_url() -> str:
    return f"{settings.fastapi_backend_url.rstrip('/')}/auth/login"
```

No OAuth library should be needed in Streamlit for login.

---

# 7. Remove insecure authentication fallback

The current Streamlit authentication code contains a dangerous fallback equivalent to:

```python
db.query(User).first()
```

This must be removed.

Never authenticate an unauthenticated visitor as the first user in the database.

The only valid authentication sources should be:

1. a valid signed session token, or
2. a session state established from a valid session token.

The logic should be approximately:

```python
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

    return st.session_state.get("user_id")
```

Do not query `User.first()` as an authentication fallback.

---

# 8. Database ownership refactor

This is the largest required code change.

Currently `app/ui/streamlit_app.py` directly imports and uses:

```python
get_session
get_user_by_id
get_daily_briefing_data
get_latest_summary
get_sync_state
get_user_contacts
get_recent_triage
get_action_items
set_action_item_status
acknowledge_triage_item
create_draft_email
update_draft_email
set_draft_status
count_sent_drafts_since
```

This is fine locally but not when Streamlit and FastAPI are deployed separately.

Move these operations behind FastAPI endpoints.

---

# 9. Create a FastAPI API router

Create a new file:

```text
app/api/__init__.py
app/api/dashboard.py
```

Or, if preferred, use a single:

```text
app/api.py
```

Do not duplicate business logic.

The API layer should call the existing CRUD functions.

The existing `app/db/crud.py` should remain the source of database operations.

---

# 10. Authentication for API endpoints

Every protected API endpoint must identify the current user from the signed session.

Preferred mechanism:

```text
HTTP-only session cookie
```

The existing FastAPI OAuth callback already creates:

```text
session
```

as an HTTP-only cookie.

Use:

```python
get_user_id_from_request(request)
```

from:

```text
app/auth/session.py
```

for FastAPI requests.

Do not trust a user ID supplied directly by Streamlit.

Bad:

```text
GET /api/briefing?user_id=123
```

Good:

```text
GET /api/briefing
```

with the authenticated session cookie.

---

# 11. Session-token bridge for Streamlit

The current OAuth callback also redirects to:

```text
https://ai-gmail-assistant.streamlit.app/?session=<token>
```

This is useful because Streamlit is on a separate domain.

Keep this bridge.

However, make sure the session token is:

- signed
- time-limited
- never logged
- never persisted in plaintext in the database
- removed from the URL after Streamlit consumes it

The existing `itsdangerous.URLSafeTimedSerializer` approach is appropriate.

---

# 12. Important cross-domain session issue

Because Streamlit and FastAPI are on different domains, a FastAPI HTTP-only cookie cannot automatically be used by Streamlit's browser requests to a different origin.

Therefore the current:

```text
?session=<signed-token>
```

bridge is acceptable for this architecture.

Streamlit should:

1. read `st.query_params["session"]`
2. validate the signed token
3. store the resulting user ID in `st.session_state`
4. remove the `session` query parameter
5. use an authenticated API mechanism for subsequent requests

Do not expose raw database IDs as authentication credentials.

---

# 13. Recommended API endpoints

Implement these endpoints based on the operations already present in `app/db/crud.py`.

## Authentication

Already exists:

```text
GET /auth/login
GET /auth/callback
GET /auth/logout
```

Keep them.

## Current user

Add:

```text
GET /api/me
```

Response:

```json
{
  "id": 1,
  "email": "user@example.com",
  "name": "User"
}
```

## Daily briefing

Add:

```text
GET /api/briefing
```

Return the information currently assembled by:

```python
get_daily_briefing_data()
get_latest_summary()
get_sync_state()
```

## Inbox

Add:

```text
GET /api/inbox
```

Optional query parameters:

```text
priority
search
```

Return the triage rows required by the existing UI.

## Tasks

Add:

```text
GET /api/tasks
```

Optional:

```text
include_done=true|false
search=...
```

## Task status

Add:

```text
PATCH /api/tasks/{task_id}
```

Body:

```json
{
  "status": "done"
}
```

## Triage acknowledgement

Add:

```text
PATCH /api/inbox/{triage_id}/acknowledge
```

This should call the existing:

```python
acknowledge_triage_item()
```

## Sync

Add:

```text
POST /api/sync
```

This should run the existing:

```python
run_sync_for_user()
```

for the authenticated user.

Do not create a second implementation of the sync logic.

## Contacts

Add:

```text
GET /api/contacts
```

Use:

```python
get_user_contacts()
```

## Drafts

Add:

```text
POST /api/drafts
GET /api/drafts/{draft_id}
PATCH /api/drafts/{draft_id}
POST /api/drafts/{draft_id}/send
POST /api/drafts/{draft_id}/discard
```

Map these to the existing CRUD and Gmail client functions.

Do not put Gmail sending logic into Streamlit.

---

# 14. API client for Streamlit

Create:

```text
app/api/client.py
```

This should contain functions such as:

```python
get_current_user()
get_briefing()
get_inbox()
get_tasks()
update_task()
acknowledge_triage()
sync_now()
get_contacts()
create_draft()
update_draft()
send_draft()
discard_draft()
```

The Streamlit UI should call these functions.

Do not put raw `requests.get()` calls throughout the UI file.

---

# 15. Session handling in the API client

The API client needs a way to authenticate.

Recommended approach:

- Streamlit stores the signed session token in `st.session_state`.
- API requests send the token in a secure request header.
- FastAPI validates the signed token using the existing session serializer.

For example:

```text
Authorization: Bearer <signed-session-token>
```

Add a helper in `app/auth/session.py`:

```python
def get_user_id_from_authorization_header(
    authorization: str | None,
) -> int | None:
    ...
```

Only accept:

```text
Bearer <token>
```

and pass the token through the existing:

```python
get_user_id_from_token()
```

Do not create a second signing system.

---

# 16. Do NOT send Google refresh tokens to Streamlit

The Google refresh token is stored encrypted in the database.

Only FastAPI/backend code should decrypt it.

The Streamlit UI must never receive:

```text
encrypted_refresh_token
```

or:

```text
Google refresh token
```

The Gmail client remains backend-only.

---

# 17. Keep Gmail synchronization on FastAPI

`app/scheduler.py` already owns:

```python
start_scheduler()
run_sync_for_all_users()
run_sync_for_user()
```

Keep it there.

FastAPI should start the scheduler during lifespan.

This is already implemented in:

```text
app/main.py
```

Do not start the scheduler inside Streamlit.

This avoids duplicate Gmail polling.

---

# 18. Render deployment

The repository already contains:

```text
render.yaml
```

and it is already close to the desired deployment.

Keep:

```yaml
buildCommand: pip install -r requirements.txt
startCommand: uvicorn app.main:app --host 0.0.0.0 --port $PORT
```

Keep:

```yaml
plan: starter
```

if using the in-process APScheduler.

Keep:

```yaml
disk:
  name: gmail-agent-data
  mountPath: /data
  sizeGB: 1
```

Keep:

```yaml
DATABASE_URL=sqlite:////data/app.db
```

The Render service must be the only service directly accessing this SQLite file.

---

# 19. Render environment variables

Set:

```text
OPENAI_API_KEY=<secret>
GOOGLE_CLIENT_ID=<secret>
GOOGLE_CLIENT_SECRET=<secret>

GOOGLE_REDIRECT_URI=https://<actual-render-url>.onrender.com/auth/callback

FERNET_SECRET_KEY=<secret>
SESSION_SECRET=<secret>

DATABASE_URL=sqlite:////data/app.db

STREAMLIT_URL=https://ai-gmail-assistant.streamlit.app

SYNC_INTERVAL_SECONDS=600
UI_REFRESH_SECONDS=20
ENV=production
```

Do not commit secret values.

---

# 20. Streamlit Cloud configuration

Streamlit Cloud should NOT contain the Render SQLite database URL.

It should not use:

```text
DATABASE_URL=sqlite:////data/app.db
```

for the production UI.

Instead, configure:

```text
FASTAPI_BACKEND_URL=https://<actual-render-url>.onrender.com
STREAMLIT_URL=https://ai-gmail-assistant.streamlit.app
```

and whatever public configuration is necessary for the API client.

The backend database credentials/secrets remain on Render.

---

# 21. Config changes

File:

```text
app/config.py
```

Add an explicit:

```python
FASTAPI_BACKEND_URL: str = "http://localhost:7860"
```

Do not derive the backend URL from `GOOGLE_REDIRECT_URI`.

Current behavior:

```python
@property
def fastapi_backend_url(self):
    ...
```

should be replaced by explicit configuration.

Why:

- `GOOGLE_REDIRECT_URI` describes where Google redirects.
- `FASTAPI_BACKEND_URL` describes where the API lives.

They happen to be related, but they are different concepts.

Use:

```text
Local:
FASTAPI_BACKEND_URL=http://localhost:7860
GOOGLE_REDIRECT_URI=http://localhost:7860/auth/callback

Production:
FASTAPI_BACKEND_URL=https://gmail-agent.onrender.com
GOOGLE_REDIRECT_URI=https://gmail-agent.onrender.com/auth/callback
```

---

# 22. Database session optimization

Current:

```python
def get_session():
    init_db()
    return SessionLocal()
```

calls database initialization on every request.

Once the deployment architecture is stable, change this so:

```python
init_db()
```

runs once during FastAPI startup, not for every CRUD operation.

Keep SQLite's:

```python
check_same_thread=False
```

because the scheduler/API may use different threads.

Do not introduce async SQLAlchemy unless necessary.

---

# 23. SQLite concurrency considerations

Because APScheduler and API requests can access the same SQLite database:

- keep transactions short
- always close sessions
- do not hold sessions across network/LLM calls
- do not keep a global SQLAlchemy Session
- commit explicitly
- use WAL mode if appropriate

Consider enabling:

```sql
PRAGMA journal_mode=WAL;
```

during SQLite initialization.

This can improve concurrent read/write behavior.

Do not over-engineer this.

If the application eventually becomes multi-instance/high traffic, migrate to PostgreSQL.

---

# 24. UI refactor strategy

Do NOT rewrite the visual UI.

Keep:

- current Streamlit layout
- tabs
- styling
- HTML
- buttons
- labels
- agent prompts
- Gmail parser
- existing CRUD logic

Only replace the data access boundary.

Current:

```text
Streamlit
  -> SQLAlchemy CRUD
```

Target:

```text
Streamlit
  -> API client
  -> FastAPI
  -> SQLAlchemy CRUD
```

This should be a transport-layer refactor, not a UI redesign.

---

# 25. Gradio

`app/ui/gradio_app.py` also directly accesses the database.

For the primary deployment described in this document, Streamlit is the target UI.

Do not spend time rewriting Gradio unless it is intended to remain a production UI.

If it remains local-only, it may continue using direct DB access.

If it will also be deployed separately, apply the same API architecture.

---

# 26. run.py

Keep `run.py` for local development.

It should continue to launch:

```text
FastAPI :7860
Streamlit :8501
```

This is useful because local development should continue to work exactly as before.

Do not use:

```text
python run.py
```

as the Render production start command.

Render should start only:

```bash
uvicorn app.main:app --host 0.0.0.0 --port $PORT
```

---

# 27. Root Streamlit entrypoint

Keep:

```text
streamlit_app.py
```

as the Streamlit Community Cloud entrypoint.

It should continue importing:

```python
import app.ui.streamlit_app
```

No FastAPI process should be launched from Streamlit Cloud.

Remove any `_ensure_backend_running()` logic from the Streamlit UI if it attempts to spawn Uvicorn automatically in production.

Local FastAPI is already started by `run.py`.

---

# 28. Environment files

Update:

```text
.env.example
```

to clearly separate local and production examples.

Recommended:

```text
# Local
GOOGLE_REDIRECT_URI=http://localhost:7860/auth/callback
FASTAPI_BACKEND_URL=http://localhost:7860
STREAMLIT_URL=http://localhost:8501
DATABASE_URL=sqlite:///./gmail.db
ENV=development

# Production values are set by the deployment platform:
# GOOGLE_REDIRECT_URI=https://<render-service>.onrender.com/auth/callback
# FASTAPI_BACKEND_URL=https://<render-service>.onrender.com
# STREAMLIT_URL=https://ai-gmail-assistant.streamlit.app
# DATABASE_URL=sqlite:////data/gmail.db
```

Do not put real credentials in this file.

---

# 29. README changes

Update the README architecture from:

```text
Streamlit -> SQLAlchemy DB
FastAPI -> SQLAlchemy DB
```

to:

```text
Streamlit Cloud
      |
      | HTTP API
      v
FastAPI / Render
      |
      v
SQLite /data/gmail.db
```

Add separate deployment instructions for:

1. Local development
2. FastAPI on Render
3. Streamlit on Streamlit Community Cloud
4. Google OAuth configuration
5. Render persistent disk
6. Production environment variables

Explicitly explain:

```text
Google callback belongs to FastAPI.
Streamlit is the UI only.
SQLite belongs to FastAPI.
```

---

# 30. Google Cloud configuration

Authorized redirect URIs:

```text
http://localhost:7860/auth/callback
https://<actual-render-service>.onrender.com/auth/callback
```

Remove:

```text
https://ai-gmail-assistant.streamlit.app/auth/callback
```

The production Streamlit URL is a frontend URL, not the OAuth callback URL.

---

# 31. Security requirements

The coding agent MUST NOT:

- log OAuth authorization codes
- log access tokens
- log refresh tokens
- log `GOOGLE_CLIENT_SECRET`
- log `FERNET_SECRET_KEY`
- log `SESSION_SECRET`
- store refresh tokens in Streamlit session state
- accept arbitrary `user_id` from the UI as authentication
- authenticate using `User.first()`
- bypass OAuth for production
- disable state validation
- disable PKCE
- use `OAUTHLIB_INSECURE_TRANSPORT=1` in production

`OAUTHLIB_INSECURE_TRANSPORT` is only allowed for local development.

---

# 32. CORS

Because Streamlit will call FastAPI from a browser-side context or through Streamlit's Python process, determine whether CORS is actually required.

Do not add permissive:

```python
allow_origins=["*"]
```

unless the implementation genuinely requires browser cross-origin calls.

If CORS is required, allow only:

```text
https://ai-gmail-assistant.streamlit.app
```

and local:

```text
http://localhost:8501
```

Do not allow all origins in production.

---

# 33. API error handling

The API client must handle:

- 401 Unauthorized
- 403 Forbidden
- 404 Not Found
- 500 Internal Server Error
- backend unavailable
- request timeout

For a 401:

```text
clear local Streamlit authentication state
show "Session expired; please sign in again"
```

Do not silently authenticate another user.

For backend unavailable:

```text
show a clear error
```

rather than displaying an empty dashboard.

---

# 34. API response models

Prefer Pydantic response models in FastAPI.

Examples:

```python
class UserResponse(BaseModel):
    id: int
    email: str
    name: str | None
```

and similar models for:

- briefing
- inbox rows
- tasks
- sync status
- contacts
- drafts

Do not return raw SQLAlchemy objects directly.

---

# 35. Tests to add

Add API tests for:

```text
GET /api/me
GET /api/briefing
GET /api/inbox
GET /api/tasks
PATCH /api/tasks/{id}
POST /api/sync
GET /api/contacts
```

Test:

- authenticated user
- unauthenticated request
- wrong user cannot access another user's task
- invalid session token
- expired session token

Add authentication tests:

```text
/auth/login
/auth/callback
```

At minimum test helper/session behavior without requiring live Google OAuth.

---

# 36. Manual deployment test checklist

## Backend

```text
[ ] Render service is deployed
[ ] /docs loads
[ ] / returns/redirects correctly
[ ] Render persistent disk is mounted at /data
[ ] /data/app.db is created
[ ] FastAPI logs show scheduler started
```

## OAuth

```text
[ ] Streamlit login button points to Render /auth/login
[ ] Google login page opens
[ ] Google redirects to Render /auth/callback
[ ] state cookie exists
[ ] PKCE verifier cookie exists
[ ] callback succeeds
[ ] user is inserted/updated in SQLite
[ ] signed session token is generated
[ ] browser returns to Streamlit
```

## Streamlit

```text
[ ] Streamlit receives session token
[ ] session token is consumed
[ ] authenticated user appears
[ ] dashboard loads
[ ] no direct production SQLite access occurs
```

## Gmail

```text
[ ] scheduler runs
[ ] Gmail refresh token remains encrypted
[ ] manual sync works
[ ] background sync works
[ ] summaries persist
[ ] triage persists
[ ] action items persist
[ ] drafts persist
```

---

# 37. Important implementation order

The coding agent should implement in this order.

## Phase 1 — Remove incorrect OAuth code

Modify:

```text
app/ui/streamlit_app.py
```

Remove direct Google OAuth and `User.first()` fallback.

Make login use:

```text
FASTAPI_BACKEND_URL/auth/login
```

---

## Phase 2 — Add explicit backend configuration

Modify:

```text
app/config.py
.env.example
```

Add:

```text
FASTAPI_BACKEND_URL
```

Do not derive it from `GOOGLE_REDIRECT_URI`.

---

## Phase 3 — Add authenticated API layer

Create:

```text
app/api/__init__.py
app/api/routes.py
app/api/client.py
```

or a clean equivalent.

Move database-facing Streamlit operations behind FastAPI.

Reuse `app/db/crud.py`.

Do not duplicate business logic.

---

## Phase 4 — Refactor Streamlit

Modify:

```text
app/ui/streamlit_app.py
```

Replace direct DB calls with API client calls.

Keep UI unchanged.

---

## Phase 5 — Harden session handling

Modify:

```text
app/auth/session.py
```

Add support for validating the session token from the API authorization header.

Keep existing signed token implementation.

---

## Phase 6 — Optimize DB initialization

Modify:

```text
app/db/session.py
app/main.py
```

Initialize DB once at FastAPI startup.

Keep sessions short-lived.

Optionally enable SQLite WAL.

---

## Phase 7 — Deployment configuration

Modify:

```text
render.yaml
.env.example
README.md
```

Render:

```text
FastAPI
Persistent Disk /data
SQLite /data/app.db
```

Streamlit Cloud:

```text
UI only
```

---

# 38. Files expected to change

## Definitely modify

```text
app/config.py
app/ui/streamlit_app.py
app/auth/session.py
app/db/session.py
.env.example
README.md
render.yaml
```

## Add

```text
app/api/__init__.py
app/api/routes.py
app/api/client.py
```

Names can be adjusted if the agent has a cleaner existing convention.

## Probably modify

```text
app/main.py
```

to include the new API router.

## Keep mostly unchanged

```text
app/auth/google_oauth.py
app/db/models.py
app/db/crud.py
app/gmail/client.py
app/gmail/parser.py
app/scheduler.py
app/agents/*
run.py
```

Only modify these if required to support the API boundary.

---

# 39. Do not make these changes

Do NOT:

```text
- replace SQLite with PostgreSQL
- rewrite the Gmail client
- rewrite the agents
- redesign the Streamlit UI
- remove FastAPI
- implement OAuth directly in Streamlit
- deploy Streamlit as the OAuth callback
- run FastAPI from Streamlit Cloud in production
- create a second database abstraction
- duplicate CRUD logic
```

The objective is a focused architecture correction, not a complete rewrite.

---

# 40. Definition of done

The implementation is complete only when:

```text
LOCAL

Streamlit :8501
    |
    v
FastAPI :7860
    |
    v
./gmail.db

Google -> localhost:7860/auth/callback
```

and:

```text
PRODUCTION

Streamlit Cloud
    |
    | API requests
    v
FastAPI Render
    |
    v
/data/gmail.db
Render Persistent Disk

Google -> Render /auth/callback
```

and:

```text
Google OAuth code is never sent to:
https://ai-gmail-assistant.streamlit.app/auth/callback
```

The Streamlit UI must not directly query the production SQLite database.

---

# 41. Critical note for the coding agent

Before modifying code, inspect the existing repository and preserve all existing functionality.

Do not blindly implement every endpoint listed above if an equivalent endpoint already exists.

First search for:

```text
@app.get
@app.post
@app.patch
@router.get
@router.post
@router.patch
```

and reuse existing backend logic.

Similarly search for all:

```text
get_session(
```

inside:

```text
app/ui/
```

and identify every direct DB operation that needs to cross the service boundary.

The final implementation should minimize duplicated code and preserve the current UI behavior.

---

# 42. Final target

The final application should behave like the user's existing Legal-Contract-RAG architecture:

```text
                  Streamlit UI
                       |
                       | HTTP API
                       v
                  FastAPI API
                       |
          +------------+------------+
          |            |            |
       OAuth       Gmail sync     CRUD
          |            |            |
          +------------+------------+
                       |
                       v
                Persistent SQLite
                 /data/gmail.db
```

This is the desired architecture for the Gmail Agent.
