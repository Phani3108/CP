---
name: llm-pipeline
description: Use when working on contract analysis quality, prompts, pydantic-ai agents, embeddings, chat/assistant behavior, or anything in backend/app/agents.py or chat_service.py. Also when diagnosing why analysis output looks wrong or generic.
---

# LLM Pipeline

## How it works
- `backend/app/agents.py` defines pydantic-ai agents: `extractor_agent`
  (clause extraction), `risk_agent` (risk scoring), `redline_verifier_agent`,
  `obligation_agent`. Models set via env `OPENAI_MODEL_EXTRACTOR` /
  `OPENAI_MODEL_RISK` (pydantic-ai ids like `openai:gpt-4.1-mini`).
- Every agent has a HEURISTIC twin (`_heuristic_segment_clauses`,
  `_heuristic_risk`, `_heuristic_redline`, `_heuristic_verify_redline`,
  `process_contract_text_fallback`). The product must work key-less.
- Embeddings (1536-dim, pgvector on `contract_clauses.embedding`) power
  semantic clause search, with keyword fallback when NULL.

## Environment reality on THIS machine (critical)
- `.env` has a **placeholder OpenAI key** → all LLM calls 401.
- Known bug: fallback engages on TIMEOUT but not on auth errors, so uploads
  end **FAILED** instead of falling back. Don't chase ghosts — check the key first.
- All demo-contract embeddings are NULL; semantic search silently degrades to
  keyword matching. This is expected, not a regression.

## Steps
1. Say which path you're changing: LLM path, heuristic path, or both.
2. Prompt/model changes: edit the agent's system prompt in `agents.py`; keep
   output schemas (the pydantic `BaseModel`s) in sync with `models.py` fields
   and with `main.py` consumers.
3. Any LLM-path change needs the matching heuristic path checked — behavior
   with no key must stay sensible.
4. Test WITHOUT a real key first (heuristics are deterministic and covered by
   `backend/test_agents.py`). Only test the live path if the user provides a key.
5. Run `run-verification` (agents tests live in the backend suite).

## When NOT to use
- Adding endpoints that merely CALL the pipeline → `backend-feature`.
- Do not switch LLM provider/SDK or add new model dependencies — owner decision.
- Do not burn user money on live-API test loops without asking.

## Quality bar
Schema-valid outputs on both paths, heuristic parity preserved, suite green,
no silent behavior change to existing analysis fields.

## Verification checklist
- [ ] `pytest -q backend/test_agents.py` (from root, PYTHONPATH=backend, env sourced) passes
- [ ] Heuristic path exercised explicitly for any LLM-path change
- [ ] Output pydantic schemas still match what `main.py` persists

## Common mistakes
- Diagnosing FAILED uploads as pipeline bugs when it's the placeholder key.
- Editing the LLM prompt and forgetting the heuristic twin now disagrees.
- Assuming embeddings exist when testing search features.

## Report back
Which agents/prompts changed, both-path test evidence, and whether live-key
verification is still pending.
