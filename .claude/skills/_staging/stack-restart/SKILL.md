---
name: stack-restart
description: Use when the ContractsPulse app must be started, stopped, restarted, or its health checked on THIS machine (no Docker). Triggers - "start the app", "restart the backend", "is the server running", port 9432/5173 unreachable, connection refused errors.
---

# Stack Restart (no-Docker, user-space)

## First rule
This machine has NO Docker, no Homebrew, no system Node or Postgres.
`./restart.sh` and `docker-compose` DO NOT WORK here. Never run them.

## When NOT to use
- Do not use for test failures (use `run-verification` / `repo-debugging-playbook`).
- Do not use on a machine that has Docker — there, `./restart.sh` is correct.

## The three services

### 1. Postgres 16 + pgvector (pgserver's bundled binaries, data in `postgres-local/`)
```bash
cd /Users/phanitejamarpaka/Downloads/ContractsPulse-Aayush
backend/venv/lib/python3.12/site-packages/pgserver/pginstall/bin/pg_ctl \
  -D postgres-local -l postgres-local/pg.log -o "-p 5432 -k /tmp" start
# status / stop: same command with `status` / `stop`
```

### 2. Backend — FastAPI on port 9432 (NOT 8000)
```bash
cd /Users/phanitejamarpaka/Downloads/ContractsPulse-Aayush/backend
set -a && source ../.env && set +a
venv/bin/uvicorn app.main:app --host 127.0.0.1 --port 9432
```
Run in background for long sessions. `.env` MUST be sourced — JWT_SECRET is
mandatory at import time.

### 3. Frontend — SvelteKit dev server on port 5173
```bash
export PATH="$HOME/.local/node20/bin:$PATH"
cd /Users/phanitejamarpaka/Downloads/ContractsPulse-Aayush/frontend
npm run dev
```
Node 20 lives at `~/.local/node20` — plain `node`/`npm` are not on the default PATH.

Redis is intentionally SKIPPED. The app never uses it despite docker-compose
listing it. Do not try to install or start Redis.

## Verification checklist
- [ ] `pg_ctl ... status` prints "server is running"
- [ ] `curl -s http://127.0.0.1:9432/health` returns `{"status":"ok"}`
- [ ] `curl -s -o /dev/null -w '%{http_code}' http://localhost:5173` returns 200
- [ ] Login works: admin@admin.com / admin

## Common mistakes
- Running `./restart.sh` → docker-compose error, wasted turn.
- Starting uvicorn without sourcing `.env` → `ValueError: JWT_SECRET...` crash.
- Using port 8000 for the backend — the frontend expects 9432.
- Starting Postgres against a fresh/empty data dir — see `schema-change` for
  the fresh-DB startup crash before doing that.

## Quality bar
All three health checks green, stated with pasted command output — not "should be running now".

## Report back
Which services you started, the health-check outputs, and any log lines from
`postgres-local/pg.log` or uvicorn if something failed.
