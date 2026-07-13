---
name: demo-data-care
description: Use before ANY operation that touches the local database contents, the postgres-local directory, uploaded contracts, or user accounts. Triggers - "reset the db", "delete contracts", "clean up data", re-seeding, or bulk updates.
---

# Demo Data Care

## What is precious
The live DB (`postgres-local/`) holds **hand-curated demo data** that is
expensive to recreate:
- 4 contracts for customer **Vista Equity Partners**, with accurate
  Claude-authored analysis (populated via a scratchpad `populate_analysis.py`
  that no longer exists on disk):
  - Oak3 Executive Search (ca21fa8d…)
  - Poppulo MSA (b1d42305…)
  - Replit Order Form $165K (265a9516…)
  - Replit Commercial Agreement/MSA (3861cc1a…)
- Login: admin@admin.com / admin
- Clause embeddings are intentionally NULL (no OpenAI key at seed time).

## STOP AND ASK before
- Deleting/overwriting `postgres-local/` or dropping the database
- Bulk DELETE/UPDATE on contracts, clauses, events
- Deleting or modifying the admin user
(File deletion and schema changes are on the repo-wide stop-and-ask list.)

## Safe patterns
- Read-access via psql:
  ```bash
  backend/venv/lib/python3.12/site-packages/pgserver/pginstall/bin/psql \
    -h /tmp -p 5432 -U postgres -d contractspulse -c "SELECT id, filename, status FROM contracts;"
  ```
- Need scratch data? Create NEW rows (fresh upload or a second user) rather
  than mutating the demo contracts.
- Before any approved destructive step, dump first:
  ```bash
  backend/venv/lib/python3.12/site-packages/pgserver/pginstall/bin/pg_dump \
    -h /tmp -p 5432 -U postgres contractspulse > /tmp/contractspulse-backup.sql
  ```

## When NOT to use
- Pure schema DDL questions → `schema-change`.
- Test fixtures inside pytest (they don't touch the live DB) — no approval needed.

## Quality bar
Demo contracts still render fully in the UI (clauses, risks, redlines) after
your operation; admin login still works.

## Verification checklist
- [ ] Backup taken before any approved destructive change
- [ ] `SELECT count(*) FROM contracts` unchanged (or change explained)
- [ ] Spot-checked one demo contract page at http://localhost:5173/contracts

## Common mistakes
- "Resetting" the DB to fix an unrelated bug — the demo data cannot be
  regenerated without a real OpenAI key and manual curation.
- Re-running seed-like scripts that duplicate or clobber contracts.

## Report back
What was touched, backup location if taken, row-count before/after, UI spot-check result.
