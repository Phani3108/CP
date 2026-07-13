# ContractsPulse — working agreement (keep this file small; it is the always-paid tax)

AI contract-analysis app: FastAPI (`backend/app/main.py`, single-file routes,
port **9432**) + SvelteKit 5 runes (`frontend/`, port 5173) + Postgres 16 +
pgvector (`postgres-local/`). Login: admin@admin.com / admin.

## Hard machine facts (this Mac — trumps README)
- **No Docker, no Homebrew, no system Node.** `./restart.sh` and
  `docker-compose` DO NOT work. Node 20: `~/.local/node20/bin`. Backend venv:
  uv-built, **no pip** (`~/.local/bin/uv pip install ...`). Redis: unused, skip.
- `.env` must be sourced (`set -a && source .env && set +a`) before running
  backend or tests — JWT_SECRET is mandatory at import.
- `.env` OpenAI key is a **placeholder**: LLM calls 401, uploads mark FAILED
  (fallback only fires on timeout — known bug), embeddings NULL.
- Live DB holds 4 hand-curated Vista Equity demo contracts — treat as
  irreplaceable (see demo-data-care skill).

## Proof commands (the only "done" that counts)
```bash
# backend + cli (from repo root):
set -a && source .env && set +a && PYTHONPATH=backend \
  backend/venv/bin/python -m pytest -q backend/ tests/ cli/tests/   # 95 passed
# frontend:
export PATH="$HOME/.local/node20/bin:$PATH" && cd frontend && npm test && npm run check
curl -s http://127.0.0.1:9432/health                                # {"status":"ok"}
```

## Operating layer — load only what the task earns
- Complex/multi-step work: read `.claude/operating/fable-to-opus.md` first.
  Simple edits: do not load it.
- Skills (currently STAGED at `.claude/skills/_staging/` pending owner review;
  after approval: `mv .claude/skills/_staging/* .claude/skills/`):
  - `stack-restart` — start/stop/health of the no-Docker stack
  - `run-verification` — canonical test commands and quality bars
  - `repo-debugging-playbook` — known-failure table; reproduce before editing
  - `backend-feature` — route conventions (user scoping, enums, single-file main.py)
  - `schema-change` — no migrations; startup catch-up pattern; ASK FIRST
  - `frontend-feature` — Svelte 5 runes idiom; api.ts is the only network boundary
  - `llm-pipeline` — agents + heuristic twins; placeholder-key reality
  - `demo-data-care` — protect the demo DB; backup before destructive ops
  - `repo-validation-gate` — check every number/claim before shipping
- Project history/decisions: `vault/INDEX.md` (grep before opening pages).

## Stop and ask before
deleting files · database schema changes · rewriting auth · payment code ·
changing public API behavior.

## Done means
bug reproduced · cause stated · fix applied · test/command output pasted ·
remaining risk named.
