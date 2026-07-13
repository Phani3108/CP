---
name: backend-feature
description: Use when adding or changing a backend API route, endpoint behavior, or business logic in ContractsPulse. Triggers - "add an endpoint", "change the API", "new backend feature".
---

# Backend Feature Work

## Architecture in 30 seconds
- `backend/app/main.py` (~2,330 lines) — ALL routes live in this single file.
  This is deliberate; do not split it into routers without explicit permission.
- `backend/app/models.py` — SQLAlchemy models (User, Contract, ContractClause,
  ContractEvent, ClauseFeedback, ContractReminder, ContractTemplate).
- `backend/app/agents.py` — pydantic-ai LLM agents + heuristic fallbacks.
- `backend/app/parser.py` — PDF/text extraction, hashing, metadata.
- `backend/app/chat_service.py` — chat/assistant plumbing.
- No Alembic migrations in practice — schema changes use the startup catch-up
  pattern (see `schema-change` skill).

## Repo conventions (unstated but consistent — follow them)
1. Routes are `@app.<verb>("/api/v1/...")` functions directly in `main.py`,
   grouped near related routes. Match neighboring code style.
2. Every user-facing route takes the auth dependency and scopes queries by the
   authenticated user's id — copy the pattern from an adjacent route like
   `GET /api/v1/contracts`. NEVER return another user's contracts.
3. Status/risk values come from the enums in `models.py`
   (`ContractStatus`, `RiskLevel`) — never raw strings.
4. Long work (analysis) runs as a background task updating `Contract.status`;
   endpoints return immediately with a status message.
5. LLM calls must have a heuristic fallback path (see `llm-pipeline`); assume
   the OpenAI key may be absent or invalid.

## Steps
1. Read the 2–3 existing routes closest to what you're adding.
2. Restate the task boundary in one sentence before editing.
3. Implement in `main.py` next to its neighbors; reuse helpers.
4. Add/extend a test in the matching `backend/test_*.py` file.
5. Verify via `run-verification` (backend suite + `/health` smoke).

## When NOT to use
- Schema/column changes → `schema-change` first.
- Pure LLM prompt/agent changes → `llm-pipeline`.
- Do not refactor `main.py` structure, auth, or password handling as a side
  effect — those need explicit user approval.

## Quality bar
New route is user-scoped, enum-typed, has a test, and the full backend suite
still passes (95+ tests).

## Verification checklist
- [ ] Auth dependency present and query filtered by user id
- [ ] Test added and passing
- [ ] `PYTHONPATH=backend ... pytest -q backend/ tests/ cli/tests/` all green
- [ ] If route is consumed by the frontend, `frontend/src/lib/api.ts` updated too

## Common mistakes
- Creating a new router module (breaks the single-file convention).
- Forgetting user scoping — data leaks across accounts.
- Testing only the happy path with the LLM assumed available.
- Adding a dependency with pip (venv has none — use uv, see `run-verification`).

## Report back
Route(s) added (path + verb), where in `main.py`, tests added, pasted suite
summary, and any frontend follow-up needed.
