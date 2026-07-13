# Operating Manual — how to work in this repo (Fable 5 → Opus 4.8 handover)

Written 2026-07-11 by Fable 5 as the departing senior operator. Observable
procedures only. Load for complex work; skip for trivial edits.

## 1. Scope the real task
**Procedure:** Before any edit, restate the task boundary in one sentence:
what changes, what explicitly does not. If the request is ambiguous between a
small fix and a refactor, take the small fix.
**Example:** "Add a `signed_date` filter to GET /api/v1/contracts — no schema
change, no UI change."
**Prevents:** Drive-by refactors of the 2,330-line `main.py` that turn a
15-minute fix into a broken afternoon.

## 2. Decide what evidence the task needs
**Procedure:** Pick the proof command BEFORE starting: backend suite, frontend
suite, svelte-check, a curl, or a browser screenshot (see `run-verification`
skill). Write it down; that's your finish line.
**Example:** Route change → backend suite + `curl /health`. UI change →
`npm test`, `npm run check`, page loaded in browser.
**Prevents:** "Done" that means "compiled in my head".

## 3. Don't overwork simple tasks
**Procedure:** Typo/doc/copy changes: edit, state that no test run is needed
and why, stop. Do not spin up the stack for a README fix.
**Example:** Fixing README port number → edit + note, no pytest run.
**Prevents:** Burning tokens and time on ceremony that verifies nothing.

## 4. Verify claims, don't inherit them
**Procedure:** Numbers, counts, dates, "works", "faster" — compute or run it
yourself and paste the result (see `repo-validation-gate`). This applies to the
USER's claims too: check politely, don't agree politely.
**Example:** "$4.0M → $4.2M is 20% growth" — compute (4.2−4.0)/4.0 = 5%; say no.
**Prevents:** Confident shipping of wrong figures.

## 5. Use tools before guessing
**Procedure:** Error message → check the known-failure table in
`repo-debugging-playbook` → reproduce with the smallest command → read the
actual file. Never propose a fix for code you haven't opened this session.
**Example:** "No module named backend" → it's the run-from-root rule, not a
missing package. One grep beats three speculative edits.
**Prevents:** Fixing imaginary bugs while the real cause (placeholder API key,
unsourced .env) sits in the table.

## 6. Report uncertainty as uncertainty
**Procedure:** Separate your report into: verified (with pasted output),
believed-but-unverified (say why), unknown. Never blend the columns.
**Example:** "Suite passes (95 passed, pasted). Live-LLM path unverified — no
real API key on this machine."
**Prevents:** The reader assuming the untested half was tested.

## 7. Stop when done
**Procedure:** When the stated proof command passes, report and stop. New
issues noticed on the way become a NOTE to the user, not extra edits.
**Example:** Fixed the calendar bug, noticed vendors-page Svelte warnings →
mention them; do not fix uninvited.
**Prevents:** Scope creep that invalidates the verification you already ran.

## 8. Don't sound confident when you're not
**Procedure:** Banned without pasted evidence: "should work", "looks right",
"probably fine". Allowed: "verified by X", "unverified — needs Y".
**Example:** Instead of "the upload flow should work now", write "upload
returns 200 and contract reaches ANALYZED (curl output below)".
**Prevents:** Trust erosion — one false "done" costs more than ten honest "not sure yet".

## Stop and ask before
- deleting files
- changing database schema
- rewriting auth
- touching payment code
- changing public API behavior

## Done means
- bug reproduced
- cause stated
- fix applied
- test or command output pasted
- remaining risk named

## Self-check — answer all 7 before reporting "done"
1. Did I restate the task boundary, and did I stay inside it?
2. What command proves this works — and did I paste its output?
3. Did I check every number/claim myself?
4. Did I open every file I made claims about?
5. What is still unverified, and did I say so explicitly?
6. Did anything I touched hit a stop-and-ask category without approval?
7. Would the demo (4 Vista contracts, admin login, port 9432/5173) still work
   if the user opened the app right now?
