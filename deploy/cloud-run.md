# Deploying ContractsPulse (CI-style split)

The proven architecture from the CI sibling project: **frontend on Vercel, backend
container on Cloud Run, managed Postgres**. The FastAPI backend cannot run on Vercel
serverless (pgvector database, 240s background analysis tasks, startup DDL).

```
Browser ── https://<app>.vercel.app ──► Vercel (SvelteKit frontend)
                    │  rewrite /api/*
                    ▼
        Cloud Run: contractspulse-api (FastAPI + Gemini)
                    │
                    ▼
        Neon Postgres (pgvector, sslmode=require)
```

## 1. Database — Neon (2 minutes)

1. Create a free project at https://neon.tech (any region near `us-central1`).
2. In the Neon SQL editor run: `CREATE EXTENSION IF NOT EXISTS vector;`
3. Copy the connection string — it must end with `?sslmode=require`.

The backend creates all tables and seeds `admin@admin.com` / `admin` on first boot
(the fresh-DB startup bug is fixed — `create_all` now runs before the ALTER fix-ups).
Change that password immediately (`./change-password.sh`) once deployed.

## 2. Backend — Cloud Run (one command)

```bash
export DATABASE_URL='postgresql://USER:PASS@HOST/neondb?sslmode=require'
export GEMINI_API_KEY='AQ....'          # Jaggaer Labs hackathon key
./deploy/deploy-backend.sh              # runs gcloud run deploy with all env vars
```

Notes:
- Project defaults to `278128424691` (the same GCP project as the Gemini key); override with `GCP_PROJECT`.
- `--min-instances 1 --no-cpu-throttling --timeout 300` keep background contract
  analysis alive (FastAPI BackgroundTasks need CPU after the response returns).
- Redis is intentionally absent — the app never uses it.

## ⚠️ If Vercel says "bundle size exceeds 500 MB"

That means Vercel tried to build the **backend** as a Python serverless
function. Don't fight the limit — the backend cannot run on Vercel at all
(FastAPI background analysis is killed after the response in serverless,
and it needs persistent pgvector Postgres). Vercel must only build the
frontend:

- **Vercel dashboard**: Project → Settings → General → **Root Directory =
  `frontend`** (then redeploy), or
- **CLI**: `vercel --cwd frontend`

Never import the repo root or a multi-service config that includes
`backend/` as a Vercel service.

## 3. Frontend — Vercel

1. Put the Cloud Run URL into `frontend/vercel.json` (replace
   `REPLACE-WITH-CLOUD-RUN-URL.run.app`).
2. Deploy `frontend/` to Vercel (team `chalkboard1`) — via the Vercel integration,
   `vercel --cwd frontend`, or a GitHub import. SvelteKit's `adapter-auto` detects
   Vercel automatically.
3. Done. The `/api/:path*` rewrite makes the API same-origin, so `src/lib/api.ts`
   needs no changes and there is no CORS to configure.

## Verify

```bash
curl https://<your-app>.vercel.app/api/v1/auth/login \
  -H 'Content-Type: application/json' \
  -d '{"email":"admin@admin.com","password":"admin"}'
```

Then log in, upload a PDF, watch the Gemini pipeline run, and check the
Templates + Template Deviation tabs.

## Plan B — single Docker host

`docker compose up -d --build` still works for a self-hosted box (README Quick Start);
compose runs its own Postgres, so only `OPENAI_API_KEY`/Gemini vars are needed.
