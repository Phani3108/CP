# ContractsPulse — run it locally in 3 steps

**You need two things: Docker Desktop, and a Google Gemini API key.** Nothing else — no Python,
no Node, no Postgres install. Docker brings all of it.

---

## 1. Get the code

```bash
git clone https://github.com/Phani3108/CP.git
cd CP
```

## 2. Create your `.env`

```bash
cp .env.example .env
```

Open `.env` and put your Gemini API key in **both** of these lines (same key in both):

```
OPENAI_API_KEY=<your-gemini-key>
GEMINI_API_KEY=<your-gemini-key>
```

> Get a free key at https://aistudio.google.com/apikey.
> Leave every other value as-is — `DATABASE_URL` already points at the Docker database.
> The app talks to Gemini through its OpenAI-compatible endpoint, which is why the key goes in
> the `OPENAI_*` variable too. That is not a mistake.

## 3. Start it

```bash
./restart.sh
```
(or plain `docker compose up -d --build`)

First run takes a few minutes — it builds the API image and installs frontend packages.

**Open → http://localhost:5173**
**Login → `admin@admin.com` / `admin`**

That's it.

---

## What happens automatically (nothing to do)
- Postgres 16 **with pgvector** starts in a container.
- On first boot the API creates the `vector` extension, creates every table, and seeds the
  `admin@admin.com / admin` user plus the org and business units.
- The database persists in `./postgres/` — it survives restarts. Delete that folder to start clean.

## Useful commands
```bash
docker compose logs -f api      # watch the backend / AI pipeline
docker compose logs -f frontend # watch the UI build
docker compose down             # stop
./restart.sh                    # rebuild + restart
```

## Try it
1. Log in, click **Upload**, drop in any contract PDF.
2. Watch the AI pipeline run (clauses → risk → obligations → metadata) — takes ~1-2 min.
3. Open the contract: **Original** document view, **Key Risks**, **Smart Clauses**,
   **Obligations**, and the risk-methodology footer.
4. Open **Jaggaer Assist** (bottom-right) and ask anything about the open contract — it reads
   the full document.
5. Upload a **revision** of the same contract (Upload Revision) to see the **Changes** tab:
   word-level redlines of exactly what changed between versions.

## Troubleshooting
| Problem | Fix |
|---|---|
| Port 5432 / 5173 / 9432 already in use | Stop the other service, or change the left-hand port in `docker-compose.yml`. |
| Uploads land as **FAILED**, or chat errors | Your Gemini key is wrong/missing/out of quota. Check `docker compose logs -f api`. |
| `db` container won't start | You have a stale `./postgres/` from a different Postgres version — delete the folder and restart. |
| Want a totally clean slate | `docker compose down && rm -rf postgres/ && ./restart.sh` |

## Notes
- The database starts **empty** (no contracts) — just upload a PDF to populate it.
- **Redis is not used** by this app; it is intentionally absent.
- Cloud deployment (Cloud Run + Neon + Vercel) is a separate path — see `deploy/DEPLOY-VERCEL.md`
  and `deploy/cloud-run.md`. You do not need any of that to run locally.
