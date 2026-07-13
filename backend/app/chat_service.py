import os
import re
import json
import uuid
import openai
from fastapi import HTTPException
from sqlalchemy.orm import Session
from .models import Contract, ContractClause

# Since ChatMessageIn and ChatResponseOut are defined in main.py,
# it might cause circular dependency if we import them from main.
# We will just accept the necessary fields or the payload and return dictionary.

def _openai_chat_model() -> str:
    # Copied from main.py
    return (os.getenv("OPENAI_MODEL_CHAT") or "gpt-5.4").strip()


# ---------------------------------------------------------------------------
# CI-style chat contract helpers (shared by contract chat + global assistant)
# ---------------------------------------------------------------------------

FOLLOWUP_INSTRUCTION = (
    "\n\nFormatting: write the answer in clean Markdown — short paragraphs, bold for key terms, "
    "GFM tables when comparing values, no top-level headers. "
    'Then on the very LAST line output exactly: FOLLOWUPS: ["q1", "q2", "q3"] '
    "— three short follow-up questions the user might naturally ask next, answerable from the "
    "user's contracts. Output nothing after that line."
)


def extract_followups(answer: str) -> tuple[str, list[str]]:
    """Split the model's trailing FOLLOWUPS: [...] line off the answer body."""
    text = (answer or "").strip()
    idx = text.rfind("FOLLOWUPS:")
    if idx == -1:
        return text, []
    tail = text[idx + len("FOLLOWUPS:"):].strip()
    body = text[:idx].rstrip()
    followups: list[str] = []
    m = re.search(r"\[.*?\]", tail, re.S)
    if m:
        try:
            arr = json.loads(m.group(0))
            followups = [str(q).strip() for q in arr if str(q).strip()][:3]
        except Exception:
            followups = []
    return (body or text), followups


def new_session_id(existing: str | None = None) -> str:
    return existing or str(uuid.uuid4())


EMAIL_INTENT_RE = re.compile(r"\b(email|draft|negotiat|send to (the )?vendor|write to)\b", re.I)

async def generate_chat_response(
    contract: Contract,
    question: str,
    history: list[dict],
    db: Session,
    session_id: str | None = None,
):
    # Fetch all clauses for this contract
    clauses = db.query(ContractClause).filter(ContractClause.contract_id == contract.id).all()

    # Prefer semantic retrieval using pgvector embeddings stored on ContractClause.
    # If embeddings are empty, fall back to keyword overlapping, and finally raw_text.
    top_clauses: list[ContractClause] = []

    has_embeddings = any(getattr(c, "embedding", None) is not None for c in clauses)

    client = openai.AsyncOpenAI(api_key=os.getenv("OPENAI_API_KEY"))

    if has_embeddings:
        try:
            emb = await client.embeddings.create(
                model=os.getenv("OPENAI_EMBEDDING_MODEL", "text-embedding-3-small").strip(),
                input=question,
                dimensions=int(os.getenv("OPENAI_EMBEDDING_DIMENSIONS", "1536")),
            )
            q_embedding = emb.data[0].embedding

            top_clauses = (
                db.query(ContractClause)
                .filter(ContractClause.contract_id == contract.id, ContractClause.embedding.isnot(None))
                .order_by(ContractClause.embedding.op('<=>')(q_embedding))
                .limit(5)
                .all()
            )
        except Exception as e:
            print(f"Semantic search failed, falling back: {e}")
            has_embeddings = False

    if not has_embeddings and clauses:
        # Degrade to simple keyword overlap if no embeddings or pgvector query fails.
        q_lower = question.lower()
        q_words = set(w for w in q_lower.split() if len(w) > 3)

        def clause_relevance(c: ContractClause) -> float:
            text = ((c.clause_type or "") + " " + (c.text_content or "")).lower()
            hits = sum(1 for w in q_words if w in text)
            risk_boost = {"CRITICAL": 2.0, "HIGH": 1.5, "MEDIUM": 1.1, "LOW": 1.0}.get(
                c.risk_level.value if c.risk_level else "LOW", 1.0
            )
            return hits * risk_boost

        scored = sorted(clauses, key=clause_relevance, reverse=True)
        top_clauses = scored[:5]

    # Build clause context string (if available)
    clause_context = ""
    if top_clauses:
        clause_context_parts = []
        for c in top_clauses:
            risk = c.risk_level.value if c.risk_level else "LOW"
            clause_context_parts.append(
                f"[{c.clause_type} | Risk: {risk}]\n{(c.text_content or '')[:900]}"
                + (f"\nAI Risk Note: {c.risk_reasoning}" if c.risk_reasoning else "")
            )
        clause_context = "\n\n---\n\n".join(clause_context_parts)

    # Also include contract metadata
    meta = contract.metadata_json or {}
    meta_summary = (
        f"Contract: {contract.filename}\n"
        f"Type: {meta.get('contract_type', 'Unknown')}\n"
        f"Counterparty: {meta.get('company', 'Unknown')}\n"
        f"Date: {meta.get('contract_date', 'Unknown')}\n"
        f"Expiry: {meta.get('expiry_date', 'Not detected')}\n"
        f"Overall Risk Counts: {meta.get('risk_counts', {})}"
    )

    # Build conversation history for multi-turn
    history_messages = []
    for h in (history or [])[-6:]:  # last 6 turns max
        role = h.get("role", "user")
        content = h.get("content", "")
        if role in {"user", "assistant"} and content:
            history_messages.append({"role": role, "content": content})

    system_prompt = (
        "You are Jaggaer Assist, a senior procurement legal assistant embedded in ContractsPulse. "
        "Answer questions about the contract based ONLY on the provided context. "
        "Be concise, precise, and procurement-focused. "
        "If the answer cannot be found in the provided context, say so clearly. "
        "Do NOT make up contract terms. When quoting contract language, be exact."
        + FOLLOWUP_INSTRUCTION
    )

    raw_text_fallback = ""
    if not top_clauses:
        raw_text_fallback = ((contract.metadata_json or {}).get("raw_text") or "")[:12000]

    user_message = (
        f"Contract Metadata:\n{meta_summary}\n\n"
        f"Relevant Contract Clauses:\n{clause_context or '(none)'}\n\n"
        f"Raw Contract Text Fallback:\n{raw_text_fallback or '(not available)'}\n\n"
        f"Question: {question}"
    )

    messages = [
        {"role": "system", "content": system_prompt},
        *history_messages,
        {"role": "user", "content": user_message},
    ]

    try:
        primary_model = _openai_chat_model()
        try:
            response = await client.chat.completions.create(
                model=primary_model,
                messages=messages,
                max_tokens=4096,
                temperature=0.1,
            )
        except Exception as inner:
            # Model availability can vary by account; fall back to a known baseline.
            fallback_model = os.getenv("OPENAI_MODEL_CHAT_FALLBACK", "gpt-4.1").strip()
            response = await client.chat.completions.create(
                model=fallback_model,
                messages=messages,
                max_tokens=4096,
                temperature=0.1,
            )
        answer = response.choices[0].message.content or "I could not generate an answer."
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Chat AI failed: {str(e)}")

    answer, followups = extract_followups(answer)

    contract_name = contract.filename
    sources = []
    for c in top_clauses:
        sources.append({
            "contract_id": str(contract.id),
            "contract_name": contract_name,
            "clause_id": str(c.id),
            "clause_type": c.clause_type,
            "risk_level": c.risk_level.value if c.risk_level else "LOW",
            "text_excerpt": (c.text_content or "")[:220],
        })

    # In-chat quick actions
    actions: list[dict] = []
    if top_clauses:
        top = top_clauses[0]
        actions.append({
            "type": "view_clause",
            "label": f"View {top.clause_type}",
            "contract_id": str(contract.id),
            "clause_type": top.clause_type,
        })
    redlined = next((c for c in top_clauses if (c.redline_suggestion or "").strip()), None)
    if redlined:
        actions.append({
            "type": "copy_redline",
            "label": f"Copy redline — {redlined.clause_type}",
            "contract_id": str(contract.id),
            "clause_type": redlined.clause_type,
            "text": redlined.redline_suggestion,
        })
    if (contract.metadata_json or {}).get("deviation_analysis"):
        actions.append({
            "type": "view_deviations",
            "label": "View template deviations",
            "contract_id": str(contract.id),
        })
    if EMAIL_INTENT_RE.search(question):
        actions.append({
            "type": "draft_email",
            "label": "Draft vendor email",
            "contract_id": str(contract.id),
        })

    return {
        "answer": answer,
        "sources": sources,
        "actions": actions,
        "suggested_questions": followups,
        "route": "rag" if has_embeddings else "keyword",
        "query_scope": contract_name,
        "conversation_mode": "multi_turn" if (history or []) else "single_turn",
        "session_id": new_session_id(session_id),
    }
