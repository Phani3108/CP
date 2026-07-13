<div align="center">

<img src="frontend/static/favicon.svg" alt="ContractsPulse logo" width="80" height="80" />

# ContractsPulse · Jaggaer Labs

**AI contract intelligence — powered by Google Gemini.**

Upload a contract, get clause-by-clause risk scores, ready-to-send redlines, and a portfolio-wide command center in minutes.

</div>

---

## ⚡ What it does

- 📄 **Ingest** — drag-and-drop PDFs (or paste text); automatic parsing, metadata & clause extraction
- 🎯 **Risk scoring** — every clause rated **Critical → Low** across 7 legal dimensions, with plain-English reasoning
- ✍️ **Redlines** — concrete replacement language for risky clauses, viewable side-by-side
- 📧 **Vendor emails** — AI-drafted negotiation emails that quote your requested redlines
- 💬 **Ask AI** — RAG chat over any contract (pgvector semantic search) + a portfolio-wide assistant
- 📊 **Dashboard** — portfolio health index, risk heatmap, top risk categories
- 📅 **Calendar** — expiries, auto-renewal opt-out deadlines, and reminders
- 🏢 **Vendors** — exposure grouped by counterparty, with version tracking & redline verification

## 🧱 Stack

- **AI**: Gemini `gemini-3.5-flash` (agents & chat) + `gemini-embedding-001` (semantic search), via Gemini's OpenAI-compatible endpoint
- **Backend**: FastAPI · pydantic-ai · SQLAlchemy
- **Frontend**: SvelteKit 5 (runes) · TypeScript · Vite
- **Data**: PostgreSQL 16 + pgvector
- **Extras**: CLI client (`cli/contractpulse.py`) · Docker Compose

## 🚀 Quick start

- `cp .env.example .env` and set your **Gemini API key** (`OPENAI_API_KEY=<your key>`)
- **Docker**: `docker compose up -d --build`
- **Manual**: start PostgreSQL 16 (+`pgvector`), then
  - backend → `cd backend && pip install -r requirements.txt && uvicorn app.main:app --port 9432`
  - frontend → `cd frontend && npm install && npm run dev`
- Open **http://localhost:5173** → log in with `admin@admin.com` / `admin` (change it!)
- API docs live at **http://localhost:9432/docs**

## 🔑 Configuration highlights (`.env`)

- `OPENAI_API_KEY` — your Gemini API key
- `OPENAI_BASE_URL` — `https://generativelanguage.googleapis.com/v1beta/openai/`
- `OPENAI_MODEL_EXTRACTOR` / `OPENAI_MODEL_RISK` — pydantic-ai model ids (`openai:gemini-3.5-flash`)
- `OPENAI_EMBEDDING_MODEL` + `OPENAI_EMBEDDING_DIMENSIONS` — must stay at **1536** dims (pgvector column)
- `CONTRACT_ANALYSIS_TIMEOUT_S` — LLM budget before deterministic heuristic fallback kicks in
- `DISABLE_SIGNUP` — lock registration for private instances

## 📁 Layout

- `backend/` — FastAPI app: API, AI agents, models, ingestion
- `frontend/` — SvelteKit UI: dashboard, contracts, risk inbox, vendors, calendar
- `cli/` — terminal client: analyze, report (PDF export), feedback
- `test/` — sample contracts for a quick spin

## 📝 Notes

- ⚠️ Never commit `.env` — it holds your live API key (already gitignored)
- No key? Analysis degrades gracefully to a deterministic heuristic pipeline
- MIT licensed
