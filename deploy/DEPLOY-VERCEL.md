# Deploying to Vercel — the correct setup (and why the monorepo config fails)

## TL;DR
**Vercel builds the FRONTEND ONLY.** The FastAPI backend **cannot run on Vercel** — put it on
Cloud Run (see `deploy/cloud-run.md`) and let the frontend rewrite `/api/*` to it.

## Why the "services" config you tried does not work

```jsonc
// ❌ This routes /api to a "backend" service that Vercel tries to build & run.
{
  "services": {
    "frontend": { "root": "frontend", "framework": "sveltekit-1" },
    "backend":  { "root": "backend" }          // <-- FastAPI on Vercel: impossible
  },
  "rewrites": [
    { "source": "/api(/.*)?", "destination": { "type": "service", "service": "backend" } },
    { "source": "/(.*)",      "destination": { "type": "service", "service": "frontend" } }
  ]
}
```

The `backend` service makes Vercel build `backend/` as Python **serverless functions**. This app
can't run that way:
- It needs **pgvector Postgres** and a **persistent connection** — serverless has neither.
- Contract analysis runs as **FastAPI BackgroundTasks (up to 240s)** — serverless kills the
  process the moment the HTTP response returns, so analysis never finishes.
- **Startup DDL / table creation** runs on boot — serverless has no persistent boot.
- The dependency tree exceeds Vercel's function bundle limit (the "500 MB / 250 MB" error).

So: **do not deploy `backend/` to Vercel.** Delete the `backend` service from any Vercel config.

## The correct architecture

```
Browser ──► https://<your-domain>          (Vercel: SvelteKit frontend only)
                  │  rewrite /api/* (frontend/vercel.json)
                  ▼
        Cloud Run: contractspulse-api        (FastAPI + Gemini, deploy/deploy-backend.sh)
                  │
                  ▼
        Neon Postgres (pgvector)
```

- `frontend/svelte.config.js` uses **`@sveltejs/adapter-vercel`** (explicit — no adapter-auto guesswork).
- `frontend/src/lib/api.ts` calls the API **same-origin** (`/api/...`) in production, so
  `frontend/vercel.json` rewrites `/api/*` to the backend. **No CORS to configure.**

## Steps (frontend on Vercel, your account)

1. **Backend first** — deploy it to Cloud Run and copy its URL (`https://…run.app`):
   see `deploy/cloud-run.md` → `deploy/deploy-backend.sh` (+ `deploy/migrate-to-neon.sh` to bring
   your existing data across). *The frontend is only useful once the backend URL exists.*

2. **Point the frontend at it** — in `frontend/vercel.json` replace the placeholder:
   ```json
   { "source": "/api/:path*", "destination": "https://<YOUR-CLOUD-RUN-URL>.run.app/api/:path*" }
   ```

3. **Import the repo into Vercel** (github.com/Phani3108/CP):
   - **Settings → General → Root Directory = `frontend`**  ← this is what keeps Vercel from ever
     touching `backend/` (no 500 MB error, no backend build).
   - Framework auto-detects SvelteKit. Build command / output are default.

4. **Deploy**, then **add your domain** (Settings → Domains) and set it live.

## Verify
```bash
curl https://<your-domain>/api/v1/auth/login \
  -H 'Content-Type: application/json' \
  -d '{"email":"admin@admin.com","password":"admin"}'
```
Then log in, upload a PDF, and check the cockpit / Changes tab.

> Temporary option before Cloud Run: you *can* point the `/api` rewrite at the local Cloudflare
> tunnel URL to smoke-test a Vercel deploy, but the tunnel is unstable and its URL rotates — use
> Cloud Run for anything you demo or make live.
