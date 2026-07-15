#!/usr/bin/env bash
# Migrate the local ContractsPulse Postgres → Neon, so the cloud deploy starts with all your
# existing data (users, demo + uploaded contracts, clauses, embeddings, original PDF blobs,
# conversations, approvals, everything) instead of an empty DB.
#
# Run this AFTER you've created the Neon project (the backend will also create tables on first
# boot, but this brings your DATA across).
#
#   export NEON_URL='postgresql://USER:PASS@HOST/neondb?sslmode=require'
#   ./deploy/migrate-to-neon.sh
#
# Local Postgres defaults match this machine's user-space pgserver install; override via
# LOCAL_PG_HOST / LOCAL_PG_PORT / LOCAL_PG_DB / LOCAL_PG_USER if needed.
set -euo pipefail

: "${NEON_URL:?Set NEON_URL to your Neon connection string (must end with ?sslmode=require)}"

# pgserver bundles pg_dump/psql next to pg_ctl.
PGBIN="backend/venv/lib/python3.12/site-packages/pgserver/pginstall/bin"
[ -x "$PGBIN/pg_dump" ] || { echo "pg_dump not found at $PGBIN — run from the repo root."; exit 1; }

LOCAL_HOST="${LOCAL_PG_HOST:-127.0.0.1}"
LOCAL_PORT="${LOCAL_PG_PORT:-5432}"
LOCAL_DB="${LOCAL_PG_DB:-contractspulse}"
LOCAL_USER="${LOCAL_PG_USER:-postgres}"
DUMP="${DUMP_FILE:-/tmp/contractspulse_dump.sql}"

echo "1/4  Enabling pgvector on Neon…"
"$PGBIN/psql" "$NEON_URL" -c "CREATE EXTENSION IF NOT EXISTS vector;"

echo "2/4  Dumping local ${LOCAL_DB}…"
# --no-owner/--no-privileges so it restores cleanly under the Neon role.
"$PGBIN/pg_dump" -h "$LOCAL_HOST" -p "$LOCAL_PORT" -U "$LOCAL_USER" -d "$LOCAL_DB" \
  --no-owner --no-privileges --no-acl --if-exists --clean -f "$DUMP"
echo "     dump size: $(wc -c < "$DUMP") bytes"

echo "3/4  Restoring into Neon (extension-already-exists noise is expected)…"
"$PGBIN/psql" "$NEON_URL" -v ON_ERROR_STOP=0 -f "$DUMP" >/tmp/neon_restore.log 2>&1 || true
tail -3 /tmp/neon_restore.log || true

echo "4/4  Verifying row counts on Neon…"
"$PGBIN/psql" "$NEON_URL" -c "SELECT 'contracts' t, count(*) FROM contracts UNION ALL SELECT 'contract_clauses', count(*) FROM contract_clauses UNION ALL SELECT 'contract_files', count(*) FROM contract_files UNION ALL SELECT 'users', count(*) FROM users;"

rm -f "$DUMP"
echo "✔ Migration complete. Point the Cloud Run DATABASE_URL at this Neon URL."
