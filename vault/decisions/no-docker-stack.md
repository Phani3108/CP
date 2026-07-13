# Decision: user-space stack instead of Docker (2026-07-10)

This machine has no Docker, Homebrew, system Node, or system Postgres — only
system Python 3.9. Rather than install Docker, the stack was assembled
user-space:

- **Python 3.12 venv** via `uv` at `backend/venv` (no pip inside — use
  `~/.local/bin/uv pip install` with `VIRTUAL_ENV` set).
- **Node 20** unpacked at `~/.local/node20` (prefix PATH with
  `~/.local/node20/bin`).
- **Postgres 16 + pgvector** using the `pgserver` pip package's bundled
  binaries (`backend/venv/lib/python3.12/site-packages/pgserver/pginstall/bin/`),
  data dir `postgres-local/`, socket `-k /tmp`, port 5432.
- **Redis skipped** — docker-compose lists it but no code path uses it.
- Backend on port **9432** (frontend's api.ts expects this), frontend on 5173.

Start commands live in the `stack-restart` skill. `./restart.sh` remains in the
repo for Docker-equipped machines only.

Consequence: anything that assumes Docker networking (`db:5432`, `redis:6379`
hostnames from `.env.example`) must be replaced by localhost values — the live
`.env` already does this.
