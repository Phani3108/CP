---
name: frontend-feature
description: Use when adding or changing ContractsPulse UI - pages, components, styling, API wiring. Triggers - "change the dashboard", "add a page", "fix the UI", anything under frontend/src.
---

# Frontend Feature Work

## Architecture in 30 seconds
- SvelteKit 2 + **Svelte 5 with runes** (`$state`, `$derived`, `$effect`),
  TypeScript, Vite. Do NOT write Svelte 4 patterns (`export let`, `$:` labels,
  writable stores for component state).
- Routes: `frontend/src/routes/` — `+page.svelte` per page: `/` dashboard,
  `contracts`, `risk`, `vendors`, `calendar`, `traces`.
- Shared code: `frontend/src/lib/` — `api.ts` (ALL backend calls go through
  this client), `auth.svelte.ts` (runes-based auth state), `poller.ts`,
  `toastStore.ts`. Tests are colocated `*.test.ts` files.
- Backend base URL/port 9432 is wired in `api.ts` — don't hardcode URLs in pages.

## Steps
1. Read the page/component you're changing plus one similar page for idiom.
2. New backend data? Add the typed function to `src/lib/api.ts` — never `fetch`
   directly in a component.
3. Implement with runes; match the existing component style and CSS approach
   used by neighboring pages.
4. Add/extend a colocated `*.test.ts` when you touch `lib/` logic.
5. Verify:
   ```bash
   export PATH="$HOME/.local/node20/bin:$PATH"
   cd /Users/phanitejamarpaka/Downloads/ContractsPulse-Aayush/frontend
   npm test && npm run check
   ```
6. For visual changes, load http://localhost:5173 (login admin@admin.com/admin)
   and confirm the page renders — dev server picks changes up live.

## When NOT to use
- Backend behavior changes → `backend-feature`.
- Do not upgrade Svelte/Kit/Vite majors or swap the adapter as a side effect.
- Do not rewrite `auth.svelte.ts` (auth is a stop-and-ask category).

## Quality bar
Runes idiom, api.ts as the only network boundary, `npm test` 34+ passing,
`npm run check` 0 errors (11 pre-existing warnings allowed; no NEW warnings).

## Verification checklist
- [ ] `npm test` summary pasted
- [ ] `npm run check` shows 0 ERRORS and warning count did not grow
- [ ] Page actually loaded in the running app for UI changes

## Common mistakes
- Svelte 4 syntax (`export let data`, `$:` reactivity) — svelte-check may pass
  it but it breaks the codebase's consistency.
- Bypassing `api.ts` with raw fetch → loses auth header handling.
- Hardcoding `localhost:9432` in a component.
- Calling `npm`/`node` without the `~/.local/node20/bin` PATH prefix.

## Report back
Files touched, new api.ts functions, test/check summaries, whether you visually
verified in the browser.
