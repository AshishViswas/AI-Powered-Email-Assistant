# 📬 Gmail Agent — AI-Powered Executive Dashboard & Inbox Manager

Gmail Agent is an intelligent executive email assistant built with **FastAPI**, **Streamlit**, **SQLAlchemy**, **OpenAI AI Agents**, and **Google OAuth 2.0**. It automatically synchronizes, triages, categorizes, and summarizes your Gmail inbox while providing a human-in-the-loop AI draft composer and interactive executive dashboard.

---

## ✨ Features

- **🌅 Daily Executive Briefings:** Real-time dashboard summarizing incoming emails into bulleted executive summaries, focus deadlines, priority alerts, and actionable next steps.
- **📥 Smart Inbox Triage (Newest First):** Automatically categorizes emails (`security`, `billing`, `shipping`, `travel`, `promotional`, `newsletter`, `work`, `personal`, `other`) and assigns priority levels (`urgent`, `action`, `important`, `informational`, `newsletter`, `low`), sorted strictly by newest email date (`email_date DESC`).
- **🔄 Instant Manual & Background Sync:** Trigger instant on-demand email sync via the **`🔄 Sync Latest Emails`** button on both the Daily Briefing and Smart Inbox headers, alongside background interval polling.
- **📖 Rich HTML Email Viewer:** Renders rich HTML email bodies exactly as displayed inside Gmail (tables, logos, styling) with zero whitespace waste.
- **📋 Action Tasks & One-Click Resolution:** Task management interface for tracking urgent/overdue action items. Mark emails as **"Read"** and tasks as **"Done"** with real-time database state synchronization.
- **✏️ Context-First AI Email Composer:** Generate email drafts from simple natural-language prompts without requiring a recipient email address upfront. Includes draft preview, refinement prompts, and pre-send recipient validation.
- **📇 Deduplicated Clean Contact List:** Smart address harvester extracts clean, lowercase email addresses for seamless draft composition.
- **🔒 Production Architecture (Decoupled Frontend & Backend):** 
  - **FastAPI Backend (Render):** Owns Google OAuth 2.0 PKCE authentication, database CRUD operations (`/data/app.db` on persistent disk), background email polling, and REST API routes (`/api/*`).
  - **Streamlit Frontend (Streamlit Cloud):** Pure presentation UI that consumes the backend API via session token authorization, ensuring database isolation and maximum deployment flexibility.

---

## 🏗️ Architecture Overview

```
                          ┌───────────────────────────┐
                          │    Google Cloud Console   │
                          │   Gmail API + OAuth 2.0   │
                          └─────────────┬─────────────┘
                                        │
                                        ▼
┌───────────────────────────┐   ┌───────────────────────────┐
│   Streamlit Cloud (UI)    ├──►│  FastAPI Backend (Render) │
│   (Pure Frontend Client)  │   │  (OAuth, REST API, DB)    │
└───────────────────────────┘   └─────────────┬─────────────┘
                                              │
                                              ▼
                                ┌───────────────────────────┐
                                │ Render Persistent Disk    │
                                │ (/data/app.db SQLite WAL) │
                                └───────────────────────────┘
```

---

## 📦 Project Structure

```
Gmail_Agent/
├── app/
├── app/
│   ├── agents/            # AI Agent logic (summarizer_agent, compose_agent)
│   ├── api/               # FastAPI REST API schemas (`schemas.py`), routes (`routes.py`), and client (`client.py`)
│   ├── auth/              # PKCE Google OAuth 2.0 authentication & session security
│   ├── db/                # SQLAlchemy database models, CRUD operations, & datetime utilities
│   ├── gmail/             # Gmail API client, message fetching, and HTML parser
│   ├── ui/                # Modern Streamlit executive dashboard interface
│   ├── config.py          # Pydantic configuration & environment settings
│   ├── main.py            # FastAPI backend server & CORS configuration
│   └── scheduler.py       # APScheduler background sync job
├── tests/                 # Comprehensive unit & API integration test suite (pytest)
├── .env.example           # Environment variable template
├── .gitignore             # Git exclusion rules
├── LICENSE                # MIT License
├── README.md              # Project documentation
├── render.yaml            # Render deployment configuration
├── requirements.txt       # Python package dependencies
├── run.py                 # Unified dual-service launcher (FastAPI + Streamlit)
└── streamlit_app.py       # Streamlit Community Cloud deployment entrypoint
```

---

## 🚀 Getting Started

### 1. Prerequisites

- Python **3.10+** installed
- A Google Cloud Project with the **Gmail API** enabled and **OAuth 2.0 Client ID** credentials.
- An **OpenAI API Key** (or compatible LLM provider credentials).

### 2. Installation

1. **Clone the Repository:**
   ```bash
   git clone https://github.com/your-username/Gmail_Agent.git
   cd Gmail_Agent
   ```

2. **Create & Activate Virtual Environment:**
   ```bash
   python -m venv .venv
   source .venv/bin/activate    # On Windows: .venv\Scripts\activate
   ```

3. **Install Dependencies:**
   ```bash
   pip install -r requirements.txt
   ```

---

## ⚙️ Configuration

1. Copy the example environment file:
   ```bash
   cp .env.example .env
   ```

2. Open `.env` and fill in your credentials:
   ```ini
   OPENAI_API_KEY=sk-proj-your-openai-key
   GOOGLE_CLIENT_ID=your-google-client-id.apps.googleusercontent.com
   GOOGLE_CLIENT_SECRET=your-google-client-secret
   GOOGLE_REDIRECT_URI=http://localhost:7860/auth/callback
   FASTAPI_BACKEND_URL=http://localhost:7860
   STREAMLIT_URL=http://localhost:8501
   FERNET_SECRET_KEY=your-generated-fernet-key
   SESSION_SECRET=your-random-session-secret
   DATABASE_URL=sqlite:///./app.db
   SYNC_INTERVAL_SECONDS=600
   UI_REFRESH_SECONDS=20
   ENV=development
   ```

> **Generating a Fernet Key:**
> ```python
> python -c "from cryptography.fernet import Fernet; print(Fernet.generate_key().decode())"
> ```

---

## 🏃 Running the Application

Launch both the **FastAPI Backend** (`http://localhost:7860`) and the **Streamlit Dashboard** (`http://localhost:8501`) simultaneously with a single launcher command:

```bash
python run.py
```

1. Open **[http://localhost:8501](http://localhost:8501)** in your browser.
2. Click **🔑 Sign in with Google Account**.
3. Authorize your Gmail permissions to access your AI Executive Dashboard!

---

## 🧪 Running Tests

Run the complete unit and API test suite using `pytest`:

```bash
PYTHONPATH=. pytest
```

---

## 🌐 Production Deployment (Render + Streamlit Cloud)

1. **Deploy Backend on Render:**
   - Create a Web Service from this repo on Render using `render.yaml`.
   - Set environment variables in Render Dashboard (`GOOGLE_REDIRECT_URI=https://<your-render-app>.onrender.com/auth/callback`, `DATABASE_URL=sqlite:////data/app.db`, etc.).
   - Attach persistent disk mounted at `/data`.

2. **Deploy UI on Streamlit Community Cloud:**
   - Connect repository in Streamlit Cloud pointing to `streamlit_app.py`.
   - Set Secrets in Streamlit Cloud: `FASTAPI_BACKEND_URL=https://<your-render-app>.onrender.com`.

---

## 📄 License

MIT License. See `LICENSE` for details.
