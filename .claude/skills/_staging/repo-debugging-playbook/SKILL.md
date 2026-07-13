---
name: repo-debugging-playbook
description: Use when a bug, failed test, broken route, runtime error, upload stuck/FAILED, or regression appears in ContractsPulse. Do not use for new feature planning.
---

# Repo Debugging Playbook

## First rule
Do not guess from the error message alone. Reproduce first. Several of this
repo's scariest-looking errors are environment quirks with known one-line causes.

## Check the known-failure table BEFORE reading source

| Symptom | Real cause | Fix |
|---|---|---|
| Upload marks contract **FAILED**, logs show OpenAI 401 | `.env` OPENAI_API_KEY is a placeholder | Real key, or accept heuristic-only. The LLM fallback triggers on *timeout*, NOT on auth errors (`backend/app/agents.py`, `process_contract_text_fallback`) — that asymmetry is a real app bug, not your change. |
| `ValueError: JWT_SECRET environment variable must...` at import/test time | `.env` not sourced | `set -a && source .env && set +a` |
| `ModuleNotFoundError: No module named 'backend'` in pytest | Ran from `backend/` dir | Run from repo root with `PYTHONPATH=backend` |
| Backend crashes on startup with a fresh/empty database | `ALTER TABLE` catch-up block runs before tables exist (`backend/app/main.py` startup_event, ~line 239) | Pre-create tables once: `python -c "from app.database import engine; from app.models import Base; Base.metadata.create_all(engine)"` (from `backend/`, env sourced) |
| Semantic clause search returns odd/keyword-ish results | Clause `embedding` columns are NULL (no OpenAI key at ingest) | Expected — falls back to keyword matching |
| `docker-compose`/`./restart.sh` fails | No Docker on this machine | Use `stack-restart` skill |
| `venv/bin/pip` not found | venv built by uv, no pip | `VIRTUAL_ENV=$PWD/backend/venv ~/.local/bin/uv pip install <pkg>` |

## Steps
1. Read the failing output completely. Match against the table above first.
2. Find the smallest command that reproduces it (one pytest file, one curl).
3. Check what recently changed in the failing area (no git here — check file
   mtimes: `ls -lt backend/app/`, and ask the user what was touched last).
4. Inspect config/env (`.env`, ports, PATH) before editing source.
5. Look at logs: uvicorn console output; `postgres-local/pg.log` for DB issues.
6. Make ONE change at a time; re-run the reproducing command after each.
7. Finish with the full relevant suite via `run-verification`.

## When NOT to use
- Planning features (`backend-feature` / `frontend-feature`).
- "App won't start" with no error yet — run `stack-restart` health checks first.

## Quality bar / Done means
- bug reproduced, cause stated, fix applied, test or command output pasted,
  remaining risk named.

## Common mistakes
- Editing `agents.py` retry logic when the actual problem is the placeholder API key.
- "Fixing" the JWT_SECRET error by hardcoding a secret in code.
- Restarting services in a loop hoping the error changes.
- Making 3 speculative edits before re-running the reproduction.

## Report back
Reproducing command, root cause (file:line), the single change made, pasted
passing output, and anything still uncertain.
