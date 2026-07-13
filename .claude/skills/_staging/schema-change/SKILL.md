---
name: schema-change
description: Use when adding columns, tables, or indexes to the ContractsPulse database. Triggers - "add a column", "new table", "store X on contracts". STOP AND ASK the user before any schema change - this is a listed stop-and-ask category.
---

# Schema Changes

## STOP AND ASK FIRST
Database schema changes are on this repo's stop-and-ask list. Describe the
intended change and get explicit approval before editing.

## How schema evolution actually works here (no migrations)
Alembic is in requirements but **unused**. The live pattern is:
1. Add/adjust the model in `backend/app/models.py`.
2. `Base.metadata.create_all` in `startup_event` creates NEW tables.
3. For new columns on EXISTING tables, add an idempotent catch-up statement to
   the startup block in `backend/app/main.py` (search for
   "Lightweight schema catch-up"):
   ```python
   conn.execute(text(
       "ALTER TABLE <table> ADD COLUMN IF NOT EXISTS <col> <type> DEFAULT ...;"
   ))
   ```
   Always `IF NOT EXISTS` / `IF EXISTS` — the block runs on every startup.

## Known landmine — fresh-DB startup crash
The startup event runs `ALTER TABLE contracts ...` BEFORE `create_all`
(`backend/app/main.py`, `startup_event`). On a brand-new empty database this
crashes the app. Workaround (run once from `backend/`, env sourced):
```bash
venv/bin/python -c "from app.database import engine; from app.models import Base; Base.metadata.create_all(engine)"
```
Do not "fix" the ordering as a drive-by; it's a known issue — fix only if asked.

## Steps
1. Get approval (above).
2. Model change in `models.py` + catch-up ALTER in `main.py` if needed.
3. Restart backend (see `stack-restart`); watch startup logs for SQL errors.
4. Confirm the column exists:
   ```bash
   backend/venv/lib/python3.12/site-packages/pgserver/pginstall/bin/psql \
     -h /tmp -p 5432 -U postgres -d contractspulse -c "\d <table>"
   ```
5. Run `run-verification`.

## When NOT to use
- Never write destructive DDL (DROP TABLE/COLUMN, mass UPDATE/DELETE) — the
  live DB holds curated demo data (see `demo-data-care`).
- Do not introduce Alembic migrations — that changes the project's operating
  model and needs the owner's decision.

## Quality bar
Idempotent DDL, model and DB agree, backend restarts cleanly, suite green.

## Verification checklist
- [ ] User approved the change
- [ ] ALTER uses IF NOT EXISTS
- [ ] Backend restarted with no startup errors
- [ ] `\d <table>` output pasted
- [ ] Tests pass

## Common mistakes
- Writing an Alembic migration nobody will ever run.
- Non-idempotent DDL that crashes the second startup.
- Testing only against the already-migrated live DB and shipping the fresh-DB crash to others.

## Report back
Exact DDL added, where, psql `\d` proof, restart log status, test summary.
