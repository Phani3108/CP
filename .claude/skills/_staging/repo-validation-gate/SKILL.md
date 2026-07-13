---
name: repo-validation-gate
description: Use before shipping, reporting, or agreeing with ANY quantitative or factual claim - percentages, growth figures, counts, dates, "tests pass", "X is faster". Also when the user asks "should I ship this?". The gate that stops confident-but-wrong output.
---

# Repo Validation Gate

## First rule
A claim without a shown calculation or pasted command output is a guess.
Do not ship guesses. Do not politely agree with the user's numbers either.

## Steps
1. Extract every checkable claim in the artifact (numbers, percentages,
   dates, counts, "passes", "works", "faster").
2. For each, do the arithmetic or run the command YOURSELF and show it inline.
   - Percent change = (new − old) / old × 100. Compute it; never eyeball it.
   - "Tests pass" requires the pasted summary line from `run-verification`.
   - Dates/deadlines: recompute from the source data, mind timezones.
3. Compare stated vs computed. ANY mismatch → answer is "do not ship",
   with the corrected figure.
4. Only after every claim checks out, say it is safe to ship — and list what
   you verified.

## Worked example (calibration)
"Revenue grew from $4.0M to $4.2M, a 20% gain. Should I ship this?"
→ (4.2 − 4.0) / 4.0 = 0.05 = **5%**, not 20%. Answer: **No — the growth
figure is wrong by 4×.** If you would have waved this through, you are not
applying this skill.

## When NOT to use
- Pure prose with no checkable claims (but look twice — dates and counts hide
  in prose).
- It does not replace `run-verification` — that produces evidence; this skill
  audits claims against evidence.

## Quality bar
Every number in the final output either computed in view or traced to pasted
command output. Uncertainty stated as uncertainty ("could not verify X"), never
silently accepted.

## Verification checklist
- [ ] Listed every checkable claim
- [ ] Showed the computation/command for each
- [ ] Stated ship / do-not-ship with reasons
- [ ] Named anything that remained unverifiable

## Common mistakes
- Agreeing with the user's arithmetic out of politeness (sycophancy).
- Verifying one headline number and skipping the ones in table cells.
- Saying "looks right" — that phrase is banned; show the check.

## Report back
The claim list with computed values, mismatches found, and the ship/no-ship verdict.
