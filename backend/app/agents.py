import os
from pydantic import BaseModel, Field
from typing import List
from pydantic_ai import Agent
import re

# ---------------------------------------------------------
# Pydantic Schemas for Structured Output
# ---------------------------------------------------------

class ExtractedClause(BaseModel):
    clause_type: str = Field(description="The category of the clause. e.g., 'Indemnification', 'Termination', 'Payment Terms'")
    text_content: str = Field(description="The exact raw text of the clause extracted from the document.")

class ContractExtractionResult(BaseModel):
    clauses: List[ExtractedClause] = Field(description="A comprehensive list of all critical legal clauses found in the document.")

class RiskAnalysisResult(BaseModel):
    risk_level: str = Field(description="Must be exactly one of: LOW, MEDIUM, HIGH, CRITICAL")
    risk_reasoning: str = Field(description="A concise explanation of why this risk level was chosen based on standard legal risk tolerances. For HIGH/CRITICAL, this must be ONE sentence and act as a copy/paste-ready negotiation rationale.")
    redline_suggestion: str | None = Field(default=None, description="If risk is HIGH or CRITICAL, provide suggested replacement legal text that is fair and market-standard.")
    # Story 007: per-dimension technical breakdown (0..1). Use 0 if not applicable.
    termination_risk: float = Field(default=0.0, ge=0.0, le=1.0, description="Risk of termination terms being unfavorable to the signer.")
    payment_risk: float = Field(default=0.0, ge=0.0, le=1.0, description="Risk of payment/fees being unfavorable or ambiguous.")
    liability_risk: float = Field(default=0.0, ge=0.0, le=1.0, description="Risk due to liability caps/exclusions/damages.")
    indemnification_risk: float = Field(default=0.0, ge=0.0, le=1.0, description="Risk due to indemnity/defense obligations.")
    ip_risk: float = Field(default=0.0, ge=0.0, le=1.0, description="Risk related to IP ownership, assignment, or licensing.")
    confidentiality_risk: float = Field(default=0.0, ge=0.0, le=1.0, description="Risk related to confidentiality obligations and carveouts.")
    dispute_risk: float = Field(default=0.0, ge=0.0, le=1.0, description="Risk related to governing law, venue, arbitration, or dispute procedures.")
    confidence: float = Field(default=0.6, ge=0.0, le=1.0, description="Model confidence in the assigned risk level and scoring (0..1).")
    # Enriched by the app (not the LLM) at runtime.
    debug_json: dict = Field(default_factory=dict, description="Technical debug metadata (model id, latency, composite score).")

# ---------------------------------------------------------
# The Agents
# ---------------------------------------------------------

# Agent 1: The Extractor
# Responsible for taking unstructured text and breaking it into logical clauses.
extractor_agent = Agent(
    os.getenv("OPENAI_MODEL_EXTRACTOR", "openai:gpt-4.1-mini"),
    output_type=ContractExtractionResult,
    retries=3,
    system_prompt=(
        "You are an expert legal AI assistant. Your task is to analyze the provided raw contract text "
        "and segment it into distinct, logical clauses. Ensure no critical text is missed. "
        "Categorize each clause accurately."
    )
)

# Agent 2: The Risk Assessor
# Responsible for evaluating a single clause for potential liabilities.
risk_agent = Agent(
    os.getenv("OPENAI_MODEL_RISK", "openai:gpt-4.1"),
    output_type=RiskAnalysisResult,
    retries=3,
    system_prompt=(
        "You are a Senior Legal Counsel. Analyze the provided legal clause and assess its risk level "
        "from the perspective of the party signing the contract. "
        "Look for hidden liabilities, uncapped indemnifications, unusual termination clauses, or aggressive payment terms. "
        "IMPORTANT — IP Assignment clauses: if the clause assigns ownership of 'all work', 'all deliverables', or "
        "'anything created during the engagement/term', treat this as HIGH risk and explain in plain English that "
        "'this means the client owns anything you create while under this contract — including side projects and "
        "personal tools'. The redline must narrow scope to work created specifically for deliverables under the agreement. "
        "Provide a concrete reasoning for your assigned risk level. "
        "If the risk level is HIGH or CRITICAL: "
        "(1) provide ONE sentence of rationale in risk_reasoning that explains why the suggested change protects the signer "
        "— write it in plain English so a non-lawyer can use it verbatim to explain the change to their client, "
        "(2) provide a market-standard suggested replacement in redline_suggestion written in clean professional legal "
        "language (not casual) that is scoped narrowly enough that a reasonable counterparty would accept it. "
        "Also provide 0..1 scores for each of these dimensions: termination_risk, payment_risk, liability_risk, "
        "indemnification_risk, ip_risk, confidentiality_risk, dispute_risk, and provide a confidence score (0..1). "
        "Use 0 for dimensions that are not applicable to this clause."
    )
)

# Agent 3: The Redline Verifier
# Responsible for comparing original clause, suggested redline, and counterparty's updated language.
class RedlineVerification(BaseModel):
    status: str = Field(description="Must be exactly: RESOLVED, PARTIALLY_RESOLVED, or UNRESOLVED")
    new_risk_level: str = Field(description="Must be exactly one of: LOW, MEDIUM, HIGH, CRITICAL")
    verification_details: str = Field(description="A concise plain-English explanation of why this status was chosen. Be specific about what was changed or what risks remain.")

redline_verifier_agent = Agent(
    os.getenv("OPENAI_MODEL_RISK", "openai:gpt-4.1"),
    output_type=RedlineVerification,
    retries=3,
    system_prompt=(
        "You are a Senior Legal Counsel specializing in contract negotiations and redline validation. "
        "Your task is to compare the original (parent) contract clause text, the suggested redline recommendation, "
        "and the new (child) updated contract clause text. "
        "Analyze whether the counterparty successfully resolved the original legal concern and "
        "implemented the spirit of the redline suggestion. "
        "Assign a status (RESOLVED, PARTIALLY_RESOLVED, or UNRESOLVED), evaluate the new risk level "
        "(LOW, MEDIUM, HIGH, or CRITICAL), and provide clear, professional explanation details about "
        "the changes made or remaining liabilities. Speak directly, and be extremely objective."
    )
)

# Grounding checker: does the assistant's answer actually follow from the cited excerpts?
# (T16 — explainable AI. A single pass over the already-retrieved excerpts, not a per-claim
# fan-out, with a heuristic token-overlap fallback so a model outage degrades gracefully.)
class GroundingCheck(BaseModel):
    grounding_score: float = Field(ge=0.0, le=1.0, description="Fraction (0..1) of the answer's factual claims directly supported by the provided source excerpts. Conversational framing does not count against it.")
    grounded: bool = Field(description="True if the answer is well-supported by the sources with no material unsupported claim.")
    unsupported: str = Field(default="", description="Brief note of any claim NOT supported by the sources; empty if all supported.")

grounding_checker_agent = Agent(
    os.getenv("OPENAI_MODEL_ASSISTANT", os.getenv("OPENAI_MODEL_RISK", "openai:gpt-4.1")),
    output_type=GroundingCheck,
    retries=2,
    system_prompt=(
        "You are a citation-grounding checker. You receive an AI assistant's ANSWER and the SOURCE "
        "excerpts it was supposed to rely on. Judge how well the answer is supported by ONLY those "
        "excerpts, using no outside knowledge. Score grounding_score as the fraction of the answer's "
        "factual claims that are directly supported by the excerpts (general conversational framing, "
        "hedges, and follow-up questions do not count against it). Set grounded=false only if the "
        "answer makes a material factual claim the excerpts do not support. Be strict but fair."
    )
)


def _heuristic_grounding(answer: str, sources: list[dict]) -> dict:
    """Token-overlap fallback: fraction of the answer's significant words present in the cited
    excerpts. Answers paraphrase, so the ratio is scaled up modestly."""
    ans_words = {w for w in re.findall(r"[a-z]{4,}", (answer or "").lower())}
    if not ans_words:
        return {"grounding_score": None, "grounded": None, "method": "heuristic"}
    src_text = " ".join((s.get("text_excerpt") or "") for s in (sources or [])).lower()
    src_words = set(re.findall(r"[a-z]{4,}", src_text))
    overlap = len(ans_words & src_words) / max(len(ans_words), 1)
    score = round(min(1.0, overlap * 1.6), 2)
    return {"grounding_score": score, "grounded": score >= 0.4, "method": "heuristic"}


async def check_grounding(answer: str, sources: list[dict], use_llm: bool = True) -> dict:
    """Return {grounding_score: 0..1|None, grounded: bool|None, method}. `sources` are the
    retrieved clause excerpts with a `text_excerpt` field."""
    if not answer or not sources:
        return {"grounding_score": None, "grounded": None, "method": "none"}
    if use_llm:
        try:
            excerpts = "\n\n".join(
                f"[{i + 1}] {s.get('contract_name', '')} · {s.get('clause_type', '')}: {s.get('text_excerpt', '')}"
                for i, s in enumerate(sources)
            )
            run = await grounding_checker_agent.run(f"ANSWER:\n{answer}\n\nSOURCE EXCERPTS:\n{excerpts}")
            out = run.output
            return {
                "grounding_score": round(float(out.grounding_score), 2),
                "grounded": bool(out.grounded),
                "unsupported": out.unsupported,
                "method": "llm",
            }
        except Exception as e:
            print(f"Grounding check failed, using heuristic: {e}")
    return _heuristic_grounding(answer, sources)


# Agent 4: The Obligation Extractor
# Extracts actionable procurement obligations from the full contract text.
class ObligationItem(BaseModel):
    title: str = Field(description="Short, action-oriented title. e.g. 'Submit Monthly Invoice', 'Provide 30-Day Termination Notice'")
    description: str = Field(description="Concise description of the obligation in plain English.")
    party_responsible: str = Field(description="Which party must fulfill this: 'Vendor', 'Customer', 'Both', or 'Either'")
    due_trigger: str = Field(description="When is this due? e.g. 'Net 30 after invoice', 'Upon contract expiry', '30 days before renewal', 'Monthly', 'Upon delivery'")
    category: str = Field(description="One of: payment, delivery, notice, reporting, compliance, renewal, confidentiality, other")

class ObligationExtractionResult(BaseModel):
    obligations: List[ObligationItem] = Field(description="All actionable obligations extracted from the contract. Focus on concrete, time-bound, or triggered duties.")

obligation_agent = Agent(
    os.getenv("OPENAI_MODEL_EXTRACTOR", "openai:gpt-4.1-mini"),
    output_type=ObligationExtractionResult,
    retries=2,
    system_prompt=(
        "You are an expert procurement manager and legal analyst. "
        "Extract all actionable obligations from the provided contract text. "
        "Focus on concrete duties that require action: payment deadlines, delivery requirements, "
        "notice obligations, reporting requirements, renewal deadlines, and compliance obligations. "
        "For each obligation, identify WHO must do WHAT, and WHEN (triggered by what event or date). "
        "Skip vague or aspirational language. Only extract obligations with a clear responsible party and trigger. "
        "Limit to the 15 most important and actionable obligations."
    )
)

async def extract_contract_obligations(raw_text: str) -> ObligationExtractionResult:
    """Run the obligation extractor on the full contract text."""
    try:
        run = await obligation_agent.run(raw_text[:12000])  # bound context
        return run.output
    except Exception as e:
        print(f"Obligation extraction failed: {e}")
        return ObligationExtractionResult(obligations=[])


# ---------------------------------------------------------
# Orchestration Example
# ---------------------------------------------------------


async def process_contract_text(raw_text: str, update_status_callback=None):
    """
    Orchestrates the agent workflow: Extract -> Assess -> Save (pseudo-code for saving)
    """
    if update_status_callback:
        await update_status_callback("Segmenting document into logical clauses...")
    print("Starting Extraction Phase...")
    # 1. Run Extraction
    try:
        extraction_run = await extractor_agent.run(raw_text)
        extracted_clauses: ContractExtractionResult = extraction_run.output
    except Exception as e:
        print(f"FAILED during extraction with error: {str(e)}")
        import traceback
        traceback.print_exc()
        if update_status_callback:
            await update_status_callback(f"Failed during extraction: {str(e)}")
        raise e
    
    print(f"Extracted {len(extracted_clauses.clauses)} clauses. Moving to Risk Analysis...")
    
    analyzed_clauses = []
    
    # 2. Run Risk Analysis (This can be parallelized in production)
    total_clauses = len(extracted_clauses.clauses)
    for i, clause in enumerate(extracted_clauses.clauses):
        if update_status_callback:
            await update_status_callback(f"Analyzing risk: Clause {i+1} of {total_clauses}...")
            
        # We pass the clause text to the risk agent
        import time
        t0 = time.perf_counter()
        risk_run = await risk_agent.run(f"Clause Type: {clause.clause_type}\nText: {clause.text_content}")
        latency_ms = int((time.perf_counter() - t0) * 1000)
        risk_result: RiskAnalysisResult = risk_run.output

        dims = {
            "termination_risk": float(risk_result.termination_risk or 0.0),
            "payment_risk": float(risk_result.payment_risk or 0.0),
            "liability_risk": float(risk_result.liability_risk or 0.0),
            "indemnification_risk": float(risk_result.indemnification_risk or 0.0),
            "ip_risk": float(risk_result.ip_risk or 0.0),
            "confidentiality_risk": float(risk_result.confidentiality_risk or 0.0),
            "dispute_risk": float(risk_result.dispute_risk or 0.0),
        }
        composite = max(dims.values()) if dims else 0.0
        risk_result.debug_json = {
            "model": os.getenv("OPENAI_MODEL_RISK", "openai:gpt-4.1"),
            "latency_ms": latency_ms,
            "dimensions": dims,
            "composite_score": round(composite, 4),
            "confidence": float(risk_result.confidence or 0.0),
        }
        
        analyzed_clauses.append({
            "clause": clause,
            "analysis": risk_result
        })
        
        print(f"Analyzed {clause.clause_type} -> Risk: {risk_result.risk_level}")
        
    if update_status_callback:
        await update_status_callback("Saving results to database...")
        
    return analyzed_clauses


def _heuristic_segment_clauses(raw_text: str) -> List[ExtractedClause]:
    """
    Deterministic fallback segmenter used when the LLM is unavailable/hanging.
    Tries to split on numbered headings and common clause headings.
    """
    text = (raw_text or "").strip()
    if not text:
        return []

    # Normalize whitespace a bit for splitting.
    text = re.sub(r"\r\n?", "\n", text)
    text = re.sub(r"[ \t]+", " ", text)

    # Split on numbered section headers like "1. DUTIES." or "12) TERM".
    # We escape the hyphen in the character class to avoid range parsing.
    parts = re.split(r"\n(?=(?:\d+\.|\d+\))\s*[A-Z][A-Z /\-]{2,80})", text)
    chunks: List[str] = []
    for p in parts:
        s = p.strip()
        if len(s) < 80:
            continue
        chunks.append(s)

    # If the split didn't work (some PDFs flatten headings), fall back to coarse chunks.
    if not chunks:
        para_parts = re.split(r"\n{2,}", text)
        chunks = [p.strip() for p in para_parts if len(p.strip()) >= 120]

    clauses: List[ExtractedClause] = []
    for c in chunks[:60]:  # bound work; this is a fallback
        first_line = c.split("\n", 1)[0].strip()
        # Best-effort clause type from the heading.
        m = re.match(r"^(?:\d+\.|\d+\))\s*([A-Z][A-Z /\-]{2,80})", first_line)
        clause_type = (m.group(1).strip().title() if m else "General")
        clauses.append(ExtractedClause(clause_type=clause_type, text_content=c[:4000]))
    return clauses


def _first_sentence(text: str) -> str:
    """
    Best-effort single-sentence extraction for UI copy/paste.
    """
    t = (text or "").strip()
    if not t:
        return ""
    # Normalize whitespace and trim.
    t = re.sub(r"\s+", " ", t).strip()
    # Prefer splitting on sentence boundaries.
    m = re.search(r"([.!?])\s", t)
    if m:
        end = m.end(1)
        return t[:end].strip()
    # Fallback: clamp.
    return t[:220].rstrip(" ,;:-") + ("" if len(t) <= 220 else ".")


def _heuristic_redline(clause_type: str, clause_text: str, risk_level: str) -> str | None:
    """
    Deterministic redline suggestions for common risky clause patterns.
    Only used for HIGH/CRITICAL (or when LLM omitted the suggestion).
    """
    t = (clause_text or "").lower()
    ct = (clause_type or "").lower()

    # Auto-renewal: ensure explicit opt-out window + no silent renewal.
    if any(k in t for k in ["auto-renew", "auto renewal", "automatic renewal", "automatically renew"]):
        return (
            "Auto-Renewal. The Agreement will renew for successive one (1) year terms only if Customer "
            "provides written confirmation of renewal. Customer may elect not to renew by providing written notice "
            "at least thirty (30) days prior to the end of the then-current term. No fees will be due for any renewal "
            "term unless expressly agreed in writing by Customer."
        )

    # Limitation of liability / damages
    if "limitation of liability" in ct or "limit" in ct or any(k in t for k in ["limitation of liability", "consequential", "indirect", "punitive"]):
        return (
            "Limitation of Liability. Except for a party’s indemnification obligations, breach of confidentiality, "
            "or infringement of the other party’s intellectual property rights, each party’s aggregate liability "
            "arising out of or relating to this Agreement will not exceed the fees paid or payable by Customer to Vendor "
            "under this Agreement in the twelve (12) months preceding the event giving rise to the claim. In no event will "
            "either party be liable for any indirect, incidental, special, consequential, or punitive damages."
        )

    # Indemnification
    if "indemn" in ct or "hold harmless" in t or "indemnif" in t:
        return (
            "Indemnification. Each party will indemnify, defend, and hold harmless the other party from and against "
            "any third-party claims, damages, and reasonable costs (including attorneys’ fees) arising from the indemnifying "
            "party’s (a) gross negligence or willful misconduct, or (b) breach of this Agreement. Indemnification obligations "
            "will be subject to reasonable notice, control of the defense, and cooperation requirements."
        )

    # IP ownership / assignment — scope to deliverables only (Story 009)
    if any(k in t for k in ["intellectual property", "ip", "work product", "assignment", "assigns"]):
        return (
            "Intellectual Property. Customer will own all work product and deliverables specifically created for "
            "Customer pursuant to this Agreement upon receipt of full payment. For the avoidance of doubt, this "
            "assignment excludes: (a) Vendor’s pre-existing intellectual property, tools, frameworks, libraries, "
            "and general know-how; (b) any work created by Vendor outside the scope of this Agreement or not "
            "specifically commissioned hereunder, including any side projects, personal tools, or independently "
            "developed software; and (c) any open-source components subject to their respective licenses. Vendor "
            "is granted a limited, non-exclusive, royalty-free license to use Customer materials solely to perform "
            "the Services during the term of this Agreement."
        )

    # Termination for convenience / early termination
    if "terminate for convenience" in t or "termination" in ct:
        return (
            "Termination. Customer may terminate this Agreement for convenience upon thirty (30) days’ prior written notice. "
            "Upon termination, Customer will pay only for Services performed through the effective termination date, and Vendor "
            "will promptly refund any prepaid fees for Services not performed."
        )

    # General safety-net fallback when we have HIGH/CRITICAL but no recognized pattern.
    if risk_level in {"HIGH", "CRITICAL"}:
        return (
            "Proposed Revision. The parties will modify this clause to be mutual, commercially reasonable, and limited in scope, "
            "including appropriate caps on liability, clear notice/cure periods, and exclusions for indirect damages, consistent "
            "with market-standard terms for agreements of this type."
        )
    return None


def _heuristic_risk(clause_type: str, clause_text: str) -> RiskAnalysisResult:
    t = (clause_text or "").lower()

    has_auto_renewal = any(
        k in t
        for k in [
            "auto-renew",
            "auto renewal",
            "automatically renew",
            "automatic renewal",
            "renewal term",
            "non-renewal",
        ]
    )

    # Very rough keyword scoring. Goal is "not empty" and "directionally useful" when LLM is down.
    critical = [
        "unlimited liability",
        "uncapped",
        "without limitation",
        "indemnif",
        "hold harmless",
        "consequential",
        "punitive",
        "liquidated damages",
    ]
    high = [
        "terminate for convenience",
        "automatic renewal",
        "audit",
        "assignment",
        "governing law",
        "warranty",
        "confidential",
        "ip ownership",
    ]
    medium = [
        "payment",
        "invoice",
        "net ",
        "interest",
        "late fee",
        "insurance",
        "subcontract",
    ]

    score = 0
    score += 3 * sum(1 for k in critical if k in t)
    score += 2 * sum(1 for k in high if k in t)
    score += 1 * sum(1 for k in medium if k in t)

    if score >= 6:
        level = "CRITICAL"
        reasoning = "Heuristic: multiple high-liability keywords detected (e.g., indemnity/uncapped liability)."
    elif score >= 3:
        level = "HIGH"
        reasoning = "Heuristic: elevated-risk clause keywords detected (e.g., termination/assignment/warranty)."
    elif score >= 1:
        level = "MEDIUM"
        reasoning = "Heuristic: standard commercial risk keywords detected (e.g., payment/insurance)."
    else:
        level = "LOW"
        reasoning = "Heuristic: no obvious high-risk keywords detected."

    # Per product requirements, auto-renewal should never be silently LOW.
    if has_auto_renewal and level == "LOW":
        level = "MEDIUM"
        reasoning = "Heuristic: auto-renewal clause detected; confirm opt-out deadline to avoid unwanted renewals."

    redline = _heuristic_redline(clause_type, clause_text, level) if level in {"HIGH", "CRITICAL"} else None
    # Keep fallback rationale copy-friendly for HIGH/CRITICAL.
    if level in {"HIGH", "CRITICAL"}:
        reasoning = _first_sentence(
            reasoning
            if reasoning and reasoning.endswith(".")
            else (reasoning + ".")
        )

    # Dimension scores: coarse mapping from detected keywords.
    dims = {
        "termination_risk": 1.0 if "terminat" in t else 0.0,
        "payment_risk": 1.0 if any(k in t for k in ["payment", "invoice", "late fee", "interest"]) else 0.0,
        "liability_risk": 1.0 if any(k in t for k in ["limitation of liability", "consequential", "punitive", "without limitation", "uncapped"]) else 0.0,
        "indemnification_risk": 1.0 if "indemn" in t or "hold harmless" in t else 0.0,
        "ip_risk": 1.0 if any(k in t for k in ["intellectual property", "ip", "work product", "assignment"]) else 0.0,
        "confidentiality_risk": 1.0 if "confidential" in t else 0.0,
        "dispute_risk": 1.0 if any(k in t for k in ["governing law", "venue", "arbitration", "jurisdiction"]) else 0.0,
    }
    composite = max(dims.values()) if dims else 0.0

    rr = RiskAnalysisResult(
        risk_level=level,
        risk_reasoning=reasoning,
        redline_suggestion=redline,
        termination_risk=dims["termination_risk"],
        payment_risk=dims["payment_risk"],
        liability_risk=dims["liability_risk"],
        indemnification_risk=dims["indemnification_risk"],
        ip_risk=dims["ip_risk"],
        confidentiality_risk=dims["confidentiality_risk"],
        dispute_risk=dims["dispute_risk"],
        confidence=0.35,
    )
    rr.debug_json = {
        "model": "heuristic_fallback",
        "latency_ms": 0,
        "dimensions": dims,
        "composite_score": round(composite, 4),
        "confidence": 0.35,
    }
    return rr


def _enrich_ip_clause(
    clause_type: str,
    clause_text: str,
    risk_level: str,
    reasoning: str,
    redline: str | None,
) -> tuple[str, str, str | None]:
    """
    Story 009: For IP assignment clauses with broad scope ('all work during engagement'),
    ensure risk is HIGH, reasoning warns about side projects in plain English, and redline
    is scoped to deliverables only.
    """
    t = (clause_text or "").lower()
    ct = (clause_type or "").lower()

    is_ip = (
        any(k in ct for k in ["ip", "intellectual property", "work product", "assignment"])
        or any(k in t for k in ["intellectual property", "work product", "ip assignment", "assigns all"])
    )
    if not is_ip:
        return risk_level, reasoning, redline

    broad_scope = any(k in t for k in [
        "all work", "all deliverables", "during the engagement", "during the term",
        "engagement period", "created during", "developed during", "arising from",
        "any work", "all intellectual property", "solely and exclusively",
    ])
    if not broad_scope:
        return risk_level, reasoning, redline

    if risk_level not in {"HIGH", "CRITICAL"}:
        risk_level = "HIGH"

    reasoning = (
        "This means the client owns anything you create while under this contract "
        "— including side projects and personal tools."
    )
    if not (redline or "").strip():
        redline = _heuristic_redline(clause_type, clause_text, risk_level)

    return risk_level, reasoning, redline


async def process_contract_text_fallback(raw_text: str, update_status_callback=None):
    """
    Fallback pipeline that does not call external LLM providers.
    """
    if update_status_callback:
        await update_status_callback("LLM unavailable. Running heuristic extraction + risk scoring...")

    extracted = _heuristic_segment_clauses(raw_text)
    analyzed = []
    total = len(extracted)
    for i, clause in enumerate(extracted):
        if update_status_callback and i % 5 == 0:
            await update_status_callback(f"Heuristic analysis: Clause {i+1} of {total}...")
        analyzed.append({"clause": clause, "analysis": _heuristic_risk(clause.clause_type, clause.text_content)})

    if update_status_callback:
        await update_status_callback("Saving results to database...")
    return analyzed


async def verify_previous_redlines(parent_clauses: list, new_clauses: list, use_llm: bool = True) -> list:
    """
    Compares parent contract clauses that had HIGH or CRITICAL risk with the new contract clauses
    to verify if the recommended redlines were successfully resolved.
    """
    resolutions = []
    
    # We only care about high/critical risks in the parent contract
    parent_risks = [c for c in parent_clauses if (c.risk_level.value if hasattr(c.risk_level, "value") else str(c.risk_level)) in {"HIGH", "CRITICAL"}]
    
    # Create a dictionary for O(1) lookup of new clauses by clause_type
    nc_dict = {}
    for nc in new_clauses:
        nc_type = nc.clause_type if hasattr(nc, "clause_type") else (nc.get("clause") if isinstance(nc, dict) else nc).clause_type
        nc_type_key = nc_type.lower().strip()
        if nc_type_key not in nc_dict:
            nc_dict[nc_type_key] = nc

    for pc in parent_risks:
        # Find matching new clause of same type (case-insensitive)
        pc_type_key = pc.clause_type.lower().strip()
        matched_nc = nc_dict.get(pc_type_key)
                
        # If no exact match, skip or take a best-effort candidate
        if not matched_nc and new_clauses:
            matched_nc = new_clauses[0]
            
        if matched_nc:
            # Handle if matched_nc is dict (like analyzed_clauses result) or SQL Alchemy object
            if isinstance(matched_nc, dict):
                nc_clause = matched_nc.get("clause")
                new_text = nc_clause.text_content if nc_clause else ""
                nc_id = ""
            else:
                new_text = matched_nc.text_content
                nc_id = str(matched_nc.id) if hasattr(matched_nc, "id") else ""
                
            parent_text = pc.text_content
            parent_redline = pc.redline_suggestion or ""
            
            if use_llm and parent_redline.strip():
                try:
                    prompt = (
                        f"Clause Type: {pc.clause_type}\n\n"
                        f"Original Text (Parent):\n{parent_text}\n\n"
                        f"Suggested Redline:\n{parent_redline}\n\n"
                        f"New Text (Child):\n{new_text}"
                    )
                    run_result = await redline_verifier_agent.run(prompt)
                    verification: RedlineVerification = run_result.output
                    
                    status = verification.status.upper().strip()
                    new_risk_level = verification.new_risk_level.upper().strip()
                    details = verification.verification_details
                except Exception as e:
                    print(f"Failed to verify redline via LLM: {e}")
                    status, new_risk_level, details = _heuristic_verify_redline(parent_text, parent_redline, new_text)
            else:
                status, new_risk_level, details = _heuristic_verify_redline(parent_text, parent_redline, new_text)
                
            resolutions.append({
                "clause_type": pc.clause_type,
                "parent_clause_id": str(pc.id),
                "parent_text": parent_text,
                "parent_risk_level": pc.risk_level.value if hasattr(pc.risk_level, "value") else str(pc.risk_level),
                "parent_redline_suggestion": parent_redline,
                "new_clause_id": nc_id,
                "new_text": new_text,
                "new_risk_level": new_risk_level,
                "status": status,
                "verification_details": details
            })
            
    return resolutions

def _heuristic_verify_redline(parent_text: str, parent_redline: str, new_text: str) -> tuple[str, str, str]:
    """
    Deterministic fallback to compare texts if LLM is unavailable or disabled.
    """
    p_text = (parent_text or "").strip().lower()
    r_text = (parent_redline or "").strip().lower()
    n_text = (new_text or "").strip().lower()
    
    if not r_text:
        return "UNRESOLVED", "HIGH", "No redline recommendation was available to verify against."
        
    # Standard text similarity: if the new text is identical to parent text, it is unresolved
    if p_text == n_text:
        return "UNRESOLVED", "HIGH", "The updated clause text is completely identical to the original clause text. No changes were made."
        
    # Check if a key phrase from the redline is present in the new text
    words = [w for w in r_text.split() if len(w) > 4]
    matched_words = sum(1 for w in words if w in n_text)
    match_ratio = matched_words / len(words) if words else 0.0
    
    if match_ratio > 0.6 or r_text in n_text:
        return "RESOLVED", "LOW", "The new text incorporates key provisions from the recommended redline language, limiting potential exposure."
    elif match_ratio > 0.2:
        return "PARTIALLY_RESOLVED", "MEDIUM", "The clause was modified from its original state, but did not fully incorporate the recommended protective redline language."
    else:
        return "UNRESOLVED", "HIGH", "The clause was modified but did not adopt any of the protective terms recommended in the redline suggestion."



# ---------------------------------------------------------
# Agent 5: Template Deviation Analyst (first-party paper)
# Compares a counterparty-edited clause against OUR approved
# standard template clause and scores the risk of the CHANGE.
# ---------------------------------------------------------

class DeviationAnalysis(BaseModel):
    deviation_type: str = Field(description="Must be exactly one of: MODIFIED, ADDED, DELETED")
    materiality: str = Field(description="Must be exactly one of: COSMETIC, IMMATERIAL, MATERIAL. COSMETIC = formatting/numbering/defined-term casing/non-substantive rewording only.")
    direction: str = Field(description="Must be exactly one of: MORE_FAVORABLE_TO_COUNTERPARTY, MORE_FAVORABLE_TO_US, NEUTRAL — judged from the template owner's perspective.")
    playbook_verdict: str = Field(description="Must be exactly: STANDARD (equivalent to our approved language) or OFF_PLAYBOOK (materially departs from it).")
    risk_of_change: str = Field(description="Must be exactly one of: LOW, MEDIUM, HIGH, CRITICAL — the MARGINAL risk introduced by the change versus our standard, not the absolute risk of the clause.")
    suggested_language_to_restore_standard: str | None = Field(default=None, description="If OFF_PLAYBOOK or MATERIAL against us: clean professional replacement language to restore/protect our position.")
    rationale: str = Field(description="ONE sentence in plain English a non-lawyer can paste into a negotiation email explaining why this matters.")
    escalate: bool = Field(default=False, description="True when the deviation is OFF_PLAYBOOK and MATERIAL against us and should be escalated to legal.")


deviation_agent = Agent(
    os.getenv("OPENAI_MODEL_RISK", "openai:gpt-4.1"),
    output_type=DeviationAnalysis,
    retries=3,
    system_prompt=(
        "You are a Senior Legal Counsel reviewing a counterparty's edits to our company's OWN "
        "pre-approved standard contract template ('first-party paper'). The STANDARD language was "
        "already vetted and approved — do not re-litigate it; evaluate only the DEVIATION. "
        "Given (a) our standard template clause and (b) the counterparty's version — or a note that "
        "the clause was deleted or newly inserted — determine: "
        "(1) materiality: COSMETIC (formatting, numbering, defined-term casing, non-substantive "
        "rewording) vs IMMATERIAL (substantive wording but no real shift in position) vs MATERIAL "
        "(shifts a right, obligation, cap, deadline, scope, or risk allocation); "
        "(2) direction: does the change move risk/benefit toward us or toward the counterparty, "
        "judged from OUR perspective as the template owner; "
        "(3) playbook_verdict: STANDARD if effectively equivalent to our approved language, "
        "OFF_PLAYBOOK if it materially departs from it; "
        "(4) risk_of_change: the MARGINAL risk introduced by this change relative to our standard "
        "(a cosmetic change MUST be LOW and STANDARD); "
        "(5) if OFF_PLAYBOOK or MATERIAL against us, provide suggested_language_to_restore_standard "
        "in clean professional legal language (for deletions, propose re-inserting the standard clause); "
        "(6) a ONE-sentence plain-English rationale a non-lawyer can paste into a negotiation email; "
        "(7) escalate=true only when OFF_PLAYBOOK and MATERIAL against us. "
        "Be objective, concise, and specific about exactly what changed."
    )
)


def _heuristic_deviation(kind: str, score: float, template_text: str, contract_text: str) -> DeviationAnalysis:
    """Deterministic fallback when the LLM is unavailable — bands on alignment score."""
    if kind == "DELETED":
        return DeviationAnalysis(
            deviation_type="DELETED",
            materiality="MATERIAL",
            direction="MORE_FAVORABLE_TO_COUNTERPARTY",
            playbook_verdict="OFF_PLAYBOOK",
            risk_of_change="HIGH",
            suggested_language_to_restore_standard=(template_text or "")[:2000] or None,
            rationale="A protection that exists in our approved standard template is absent from this contract and should be restored.",
            escalate=True,
        )
    if kind == "ADDED":
        return DeviationAnalysis(
            deviation_type="ADDED",
            materiality="MATERIAL",
            direction="MORE_FAVORABLE_TO_COUNTERPARTY",
            playbook_verdict="OFF_PLAYBOOK",
            risk_of_change="MEDIUM",
            suggested_language_to_restore_standard=None,
            rationale="This clause does not exist in our approved standard template and needs review before acceptance.",
            escalate=False,
        )
    # MODIFIED — band by how far it drifted
    if score >= 0.85:
        level, mat, verdict, esc = "LOW", "IMMATERIAL", "STANDARD", False
        why = "The clause is close to our approved standard language with only minor wording drift."
    elif score >= 0.72:
        level, mat, verdict, esc = "MEDIUM", "MATERIAL", "OFF_PLAYBOOK", False
        why = "The clause noticeably departs from our approved standard language and should be reviewed."
    else:
        level, mat, verdict, esc = "HIGH", "MATERIAL", "OFF_PLAYBOOK", True
        why = "The clause has been substantially rewritten from our approved standard language."
    return DeviationAnalysis(
        deviation_type="MODIFIED",
        materiality=mat,
        direction="NEUTRAL",
        playbook_verdict=verdict,
        risk_of_change=level,
        suggested_language_to_restore_standard=(template_text or "")[:2000] if verdict == "OFF_PLAYBOOK" else None,
        rationale=why,
        escalate=esc,
    )


def _deviation_item(kind, analysis: DeviationAnalysis, template_clause=None, contract_clause=None,
                    score: float | None = None, type_mismatch: bool = False, absolute_risk: dict | None = None) -> dict:
    clause_type = (
        getattr(contract_clause, "clause_type", None)
        or getattr(template_clause, "clause_type", None)
        or "Clause"
    )
    return {
        "clause_type": clause_type,
        "deviation_type": kind,
        "template_clause_id": str(template_clause.id) if template_clause is not None and getattr(template_clause, "id", None) else None,
        "template_text": (getattr(template_clause, "text_content", None) or None),
        "contract_clause_id": str(contract_clause.id) if contract_clause is not None and getattr(contract_clause, "id", None) else None,
        "contract_text": (getattr(contract_clause, "text_content", None) or None),
        "alignment_score": score,
        "type_mismatch": type_mismatch,
        "materiality": analysis.materiality,
        "direction": analysis.direction,
        "playbook_verdict": analysis.playbook_verdict,
        "risk_of_change": analysis.risk_of_change,
        "suggested_language_to_restore_standard": analysis.suggested_language_to_restore_standard,
        "rationale": analysis.rationale,
        "escalate": analysis.escalate,
        "absolute_risk": absolute_risk,
    }


async def analyze_template_deviations(alignment, use_llm: bool = True, status_callback=None) -> list[dict]:
    """Turn an AlignmentResult into scored DeviationItems.

    MATCHED   -> STANDARD/LOW without any LLM call.
    MODIFIED  -> deviation_agent (template vs counterparty text).
    DELETED   -> deviation_agent deletion branch (risk of losing the protection).
    ADDED     -> existing absolute risk_agent (clause is not in the vetted template).
    Any LLM failure degrades to the deterministic heuristic for that item.
    """
    import asyncio

    items: list[dict] = []

    for pair in alignment.matched:
        analysis = DeviationAnalysis(
            deviation_type="MODIFIED",
            materiality="COSMETIC",
            direction="NEUTRAL",
            playbook_verdict="STANDARD",
            risk_of_change="LOW",
            suggested_language_to_restore_standard=None,
            rationale="Matches our approved standard template language.",
            escalate=False,
        )
        items.append(_deviation_item("MATCHED", analysis, pair.template_clause, pair.contract_clause,
                                     pair.score, pair.type_mismatch))

    sem = asyncio.Semaphore(4)
    total_llm = len(alignment.modified) + len(alignment.deleted) + len(alignment.added)
    done_count = 0

    async def report_progress():
        nonlocal done_count
        done_count += 1
        if status_callback:
            await status_callback(f"Evaluating deviations from standard template: {done_count} of {total_llm}...")

    async def run_modified(pair):
        async with sem:
            t_text = (pair.template_clause.text_content or "")[:4000]
            c_text = (pair.contract_clause.text_content or "")[:4000]
            fragment_note = (
                "\n\nNOTE: clause segmentation differs between the two documents — the counterparty "
                "version may be a FRAGMENT of the standard clause (or vice versa). Judge only the "
                "substantive drift in the language both versions actually cover; text present in the "
                "standard but outside this fragment's scope is NOT a deletion."
                if getattr(pair, "fragment", False) else ""
            )
            analysis = None
            if use_llm:
                try:
                    run = await deviation_agent.run(
                        f"Clause Type: {pair.template_clause.clause_type}\n"
                        f"Embedding similarity to standard: {pair.score}\n\n"
                        f"OUR STANDARD TEMPLATE CLAUSE:\n{t_text}\n\n"
                        f"COUNTERPARTY'S VERSION IN THE INCOMING CONTRACT:\n{c_text}"
                        f"{fragment_note}"
                    )
                    analysis = run.output
                    analysis.deviation_type = "MODIFIED"
                except Exception as e:
                    print(f"Deviation agent failed (MODIFIED, {pair.template_clause.clause_type}): {e}")
            if analysis is None:
                analysis = _heuristic_deviation("MODIFIED", pair.score, t_text, c_text)
            await report_progress()
            return _deviation_item("MODIFIED", analysis, pair.template_clause, pair.contract_clause,
                                   pair.score, pair.type_mismatch)

    async def run_deleted(t_clause):
        async with sem:
            t_text = (t_clause.text_content or "")[:4000]
            analysis = None
            if use_llm:
                try:
                    run = await deviation_agent.run(
                        f"Clause Type: {t_clause.clause_type}\n\n"
                        f"OUR STANDARD TEMPLATE CLAUSE:\n{t_text}\n\n"
                        "COUNTERPARTY'S VERSION: This clause is ENTIRELY ABSENT from the counterparty's "
                        "contract — no clause in the incoming document covers this subject matter. "
                        "First identify WHO this clause binds and WHO it protects. If it protects US "
                        "(the template owner) or binds the counterparty, its deletion is "
                        "MORE_FAVORABLE_TO_COUNTERPARTY and at least MEDIUM risk_of_change. "
                        "Assess the risk of LOSING this protection (deviation_type=DELETED) and propose "
                        "re-insertion language."
                    )
                    analysis = run.output
                    analysis.deviation_type = "DELETED"
                    if not (analysis.suggested_language_to_restore_standard or "").strip():
                        analysis.suggested_language_to_restore_standard = t_text[:2000]
                except Exception as e:
                    print(f"Deviation agent failed (DELETED, {t_clause.clause_type}): {e}")
            if analysis is None:
                analysis = _heuristic_deviation("DELETED", 0.0, t_text, "")
            await report_progress()
            return _deviation_item("DELETED", analysis, t_clause, None, None, False)

    async def run_added(c_clause):
        async with sem:
            c_text = (c_clause.text_content or "")[:4000]
            absolute = None
            analysis = None
            if use_llm:
                try:
                    risk_run = await risk_agent.run(f"Clause Type: {c_clause.clause_type}\nText: {c_text}")
                    rr: RiskAnalysisResult = risk_run.output
                    absolute = {
                        "risk_level": rr.risk_level,
                        "risk_reasoning": rr.risk_reasoning,
                        "redline_suggestion": rr.redline_suggestion,
                    }
                    analysis = DeviationAnalysis(
                        deviation_type="ADDED",
                        materiality="MATERIAL",
                        direction="MORE_FAVORABLE_TO_COUNTERPARTY" if rr.risk_level in {"HIGH", "CRITICAL"} else "NEUTRAL",
                        playbook_verdict="OFF_PLAYBOOK",
                        risk_of_change=rr.risk_level,
                        suggested_language_to_restore_standard=rr.redline_suggestion,
                        rationale=_first_sentence(rr.risk_reasoning or "Counterparty-inserted clause not present in our approved template."),
                        escalate=rr.risk_level in {"HIGH", "CRITICAL"},
                    )
                except Exception as e:
                    print(f"Risk agent failed (ADDED, {c_clause.clause_type}): {e}")
            if analysis is None:
                analysis = _heuristic_deviation("ADDED", 0.0, "", c_text)
            await report_progress()
            return _deviation_item("ADDED", analysis, None, c_clause, None, False, absolute)

    tasks = (
        [run_modified(p) for p in alignment.modified]
        + [run_deleted(t) for t in alignment.deleted]
        + [run_added(c) for c in alignment.added]
    )
    if tasks:
        items.extend(await asyncio.gather(*tasks))

    severity = {"LOW": 0, "MEDIUM": 1, "HIGH": 2, "CRITICAL": 3}
    kind_weight = {"DELETED": 3, "ADDED": 2, "MODIFIED": 1, "MATCHED": 0}
    items.sort(key=lambda i: (severity.get(i["risk_of_change"], 0), kind_weight.get(i["deviation_type"], 0)), reverse=True)
    return items


# ---------------------------------------------------------
# Agent 6: Metadata Extractor (dynamic + structured)
# Replaces the brittle regex header parsing with LLM extraction,
# including open-ended dynamic attributes and document references.
# ---------------------------------------------------------

CONTRACT_TYPES = ["MSA", "NDA", "SOW", "SLA", "DPA", "LICENSE", "SERVICES",
                  "PURCHASE", "EMPLOYMENT", "LEASE", "AMENDMENT", "OTHER"]


class ContractMetadataResult(BaseModel):
    party_a: str | None = Field(default=None, description="First/issuing party legal name")
    party_b: str | None = Field(default=None, description="Counterparty legal name")
    counterparty_role: str | None = Field(default=None, description="One of: vendor, customer, partner, unknown")
    contract_type: str | None = Field(default=None, description=f"Normalized type, exactly one of: {', '.join(CONTRACT_TYPES)}")
    effective_date: str | None = Field(default=None, description="ISO YYYY-MM-DD")
    expiry_date: str | None = Field(default=None, description="ISO YYYY-MM-DD; if only a term is stated, compute from effective_date when possible")
    term_description: str | None = None
    term_months: int | None = None
    auto_renewal: bool | None = None
    renewal_notice_days: int | None = None
    total_value: float | None = Field(default=None, description="Total committed contract value as a plain number")
    currency: str | None = Field(default=None, description="ISO 4217 code, e.g. USD")
    payment_terms: str | None = Field(default=None, description="e.g. 'Net 45'")
    governing_law: str | None = Field(default=None, description="e.g. 'New York' or 'Delaware'")
    business_unit_hint: str | None = Field(default=None, description="Business unit/division mentioned, if any")
    rfq_reference: str | None = Field(default=None, description="RFQ/RFP/PO/sourcing-event reference number if stated, else null")
    # Flat string lists — nested object arrays are unreliable through the
    # OpenAI-compat function-calling layer, so we parse these server-side.
    dynamic_attributes: list[str] = Field(default_factory=list, description="Up to 10 additional contract-specific facts, EACH formatted exactly as 'Label :: value :: category' where category is one of financial, dates, legal, operational, general. Example: 'Retainer fee :: $59,000 in two installments :: financial'")
    references_other_documents: list[str] = Field(default_factory=list, description="Other agreements this document explicitly references, EACH formatted exactly as 'RELATIONSHIP :: referenced document title :: TYPE' where RELATIONSHIP is one of AMENDS, ORDER_UNDER, MASTER_OF, RENEWS, INCORPORATES, RELATED and TYPE is the referenced document's contract type. Example: 'ORDER_UNDER :: Replit Commercial Agreement :: MSA'")
    confidence: float = Field(default=0.6, ge=0.0, le=1.0)


metadata_agent = Agent(
    os.getenv("OPENAI_MODEL_METADATA", os.getenv("OPENAI_MODEL_RISK", "openai:gpt-4.1")),
    output_type=ContractMetadataResult,
    retries=3,
    # long documents + thinking tokens starve the trailing list fields without this
    model_settings={"max_tokens": 8192, "temperature": 0.1},
    system_prompt=(
        "You are a contract metadata extraction specialist for an enterprise contract repository. "
        "Extract ONLY facts stated in the text; use null when a field is absent — never guess. "
        "Dates must be ISO YYYY-MM-DD. Normalize contract_type to the provided enum (the overall "
        "instrument type — an order form under a master agreement is PURCHASE; the master itself is MSA). "
        "total_value is the total committed value (sum line items if itemized), not a unit price. "
        "auto_renewal is true whenever the agreement provides ANY automatic renewal mechanism "
        "(e.g. 'automatically renewed for successive one-year terms'), even if the initial term "
        "itself is defined in a separate order form; false only when renewal is explicitly manual. "
        "dynamic_attributes: surface up to 10 additional contract-specific facts a procurement or legal "
        "reviewer would want at a glance (fees, caps, SLAs, carve-outs, key deadlines, named personnel, "
        "special rights) — concise values, no duplication of the fixed fields. "
        "references_other_documents: list other agreements this document explicitly references "
        "(the master it operates under, documents it amends or renews, incorporated terms), with the "
        "relationship seen FROM THIS DOCUMENT (e.g. an order form ORDER_UNDER its master). "
        "rfq_reference: any RFQ/RFP/PO/sourcing event number; null if none is stated."
    )
)


async def extract_contract_metadata_llm(raw_text: str) -> ContractMetadataResult:
    """Run the metadata extractor on the document (bounded context).

    Gemini Flash handles large contexts cheaply — send up to ~90K chars whole;
    beyond that, head + middle + tail sampling so deep clauses (governing law,
    payment terms) aren't silently cut.
    """
    text = raw_text or ""
    if len(text) <= 90000:
        snippet = text
    else:
        mid = len(text) // 2
        snippet = text[:45000] + "\n...\n" + text[mid - 10000:mid + 10000] + "\n...\n" + text[-20000:]
    run = await metadata_agent.run(snippet)
    return run.output


class MetadataEnrichment(BaseModel):
    dynamic_attributes: list[str] = Field(default_factory=list, description="Contract-specific facts, EACH as 'Label :: value :: category' (category: financial, dates, legal, operational, general)")
    references_other_documents: list[str] = Field(default_factory=list, description="Referenced agreements, EACH as 'RELATIONSHIP :: document title :: TYPE' (RELATIONSHIP: AMENDS, ORDER_UNDER, MASTER_OF, RENEWS, INCORPORATES, RELATED)")


enrichment_agent = Agent(
    os.getenv("OPENAI_MODEL_EXTRACTOR", "openai:gpt-4.1-mini"),
    output_type=MetadataEnrichment,
    retries=2,
    model_settings={"max_tokens": 4096, "temperature": 0.1},
    system_prompt=(
        "You mine contracts for two things ONLY. "
        "1) dynamic_attributes: 5-10 contract-specific facts a procurement reviewer wants at a glance "
        "(fees, caps, SLAs, carve-outs, deadlines, named personnel, special rights) — each formatted "
        "'Label :: concise value :: category'. "
        "2) references_other_documents: every OTHER agreement this document explicitly references "
        "(the master it operates under, documents it amends/renews, incorporated terms) — each formatted "
        "'RELATIONSHIP :: referenced document title :: TYPE'. "
        "Both lists MUST be populated when the text supports it."
    )
)


async def extract_metadata_enrichment(raw_text: str) -> MetadataEnrichment:
    text = raw_text or ""
    snippet = text[:20000]
    run = await enrichment_agent.run(snippet)
    return run.output
