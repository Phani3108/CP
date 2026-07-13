---
name: run-verification
description: Use after ANY code change, before claiming work is done. Also when asked "do the tests pass", "verify this", or before a commit/handoff. These are the only commands that count as proof in this repo.
---

# Run Verification

## When NOT to use
- Not for diagnosing a failure you already have (use `repo-debugging-playbook`).
- Doc-only changes (README, comments) need no test run — say so explicitly instead.

## The canonical commands (run from repo root)

### Backend + CLI tests (95 tests as of 2026-07-11)
```bash
cd /Users/phanitejamarpaka/Downloads/ContractsPulse-Aayush
set -a && source .env && set +a
PYTHONPATH=backend backend/venv/bin/python -m pytest -q backend/ tests/ cli/tests/
```
Non-negotiable details:
- Must run from **repo root**: `backend/test_agents.py` imports `backend.app.*`
  while the other tests import `app.*` — only root + `PYTHONPATH=backend` satisfies both.
- `.env` must be sourced first or collection dies with `ValueError: JWT_SECRET`.
- The venv has no pip. To add a dev dependency:
  `VIRTUAL_ENV=$PWD/backend/venv ~/.local/bin/uv pip install <pkg>`

### Frontend tests (34 tests) + type/lint check
```bash
export PATH="$HOME/.local/node20/bin:$PATH"
cd /Users/phanitejamarpaka/Downloads/ContractsPulse-Aayush/frontend
npm test          # vitest run
npm run check     # svelte-check: 0 errors required; 11 pre-existing warnings are OK
```

### App-level smoke (when a change touches routes or startup)
```bash
curl -s http://127.0.0.1:9432/health   # {"status":"ok"}
```

## Quality bar
- Backend: `95 passed` (or more, never fewer without an explained removal).
- Frontend: `34 passed` and svelte-check `0 ERRORS`. The 11 warnings
  (`state_referenced_locally` in vendors page etc.) are pre-existing — do not
  count new warnings as OK.

## Verification checklist
- [ ] Ran the suites relevant to what changed (backend change → backend suite at minimum)
- [ ] Pasted the actual summary lines into the report
- [ ] Any new/failed test explained, not hand-waved

## Common mistakes
- Running pytest from `backend/` → `ModuleNotFoundError: No module named 'backend'`.
- Forgetting `.env` → JWT_SECRET ValueError, misdiagnosed as a code bug.
- Claiming success from a partial run (`pytest test_parser.py` only) after a
  change that touches `main.py`.
- Treating svelte-check warnings-count changes as noise — new warnings mean review.

## Report back
The pasted summary lines (e.g. "95 passed", "34 passed", "0 ERRORS 11 WARNINGS"),
which suites you did NOT run and why.
