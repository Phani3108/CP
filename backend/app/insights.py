"""Cross-portfolio clause intelligence for a single contract.

Answers three questions the client asked for directly:
- Coverage gaps  — "which clauses are MISSING here that our similar
  agreements (or our template) normally have?"
- Precedents     — "what language did we previously APPROVE that could fill
  the gap / replace a risky clause?" (grounded in clause_feedback approvals)
- Ambiguities    — "which clauses read as vague or under-specified?"

Deterministic (no LLM calls) so it renders instantly in the cockpit.
"""
import re
from collections import defaultdict

from sqlalchemy import or_

from .models import (Contract, ContractClause, ContractStatus, ClauseFeedback,
                     RiskLevel, BusinessUnit)

# Canonical clause taxonomy: synonym → canonical bucket
CANONICAL_CLAUSE_TYPES: dict[str, str] = {
    "limitation of liability": "liability",
    "liability cap": "liability",
    "liability": "liability",
    "maximum liability": "liability",
    "indemnification": "indemnification",
    "indemnity": "indemnification",
    "ip indemnification": "indemnification",
    "confidentiality": "confidentiality",
    "non-disclosure": "confidentiality",
    "nda": "confidentiality",
    "termination": "termination",
    "termination for cause": "termination",
    "termination for convenience": "termination",
    "term": "term",
    "renewal": "term",
    "auto-renewal": "term",
    "payment terms": "payment",
    "payment": "payment",
    "fees": "payment",
    "pricing": "payment",
    "price increase": "payment",
    "intellectual property": "ip",
    "ip ownership": "ip",
    "ip assignment": "ip",
    "work product": "ip",
    "governing law": "dispute_resolution",
    "dispute resolution": "dispute_resolution",
    "jurisdiction": "dispute_resolution",
    "venue": "dispute_resolution",
    "arbitration": "dispute_resolution",
    "warranty": "warranty",
    "warranties": "warranty",
    "disclaimer": "warranty",
    "data security": "data_security",
    "data protection": "data_security",
    "security": "data_security",
    "privacy": "data_security",
    "insurance": "insurance",
    "force majeure": "force_majeure",
    "assignment": "assignment",
    "non-solicitation": "non_solicitation",
    "non solicitation": "non_solicitation",
    "audit": "audit_rights",
    "audit rights": "audit_rights",
    "compliance": "compliance",
    "sla": "service_levels",
    "service levels": "service_levels",
    "service level agreement": "service_levels",
}

HUMAN_NAMES = {
    "liability": "Limitation of Liability",
    "indemnification": "Indemnification",
    "confidentiality": "Confidentiality",
    "termination": "Termination",
    "term": "Term & Renewal",
    "payment": "Payment Terms",
    "ip": "Intellectual Property",
    "dispute_resolution": "Governing Law / Disputes",
    "warranty": "Warranties",
    "data_security": "Data Security",
    "insurance": "Insurance",
    "force_majeure": "Force Majeure",
    "assignment": "Assignment",
    "non_solicitation": "Non-Solicitation",
    "audit_rights": "Audit Rights",
    "compliance": "Compliance",
    "service_levels": "Service Levels",
}

AMBIGUITY_RE = re.compile(
    r"(ambigu|unclear|vague|not specified|silent on|undefined|open-ended|"
    r"does not specify|lacks (?:a |any )?(?:clear|defined)|no (?:clear|defined|specific))",
    re.I,
)


def canonicalize(clause_type: str | None) -> str | None:
    t = (clause_type or "").lower().strip()
    if not t:
        return None
    if t in CANONICAL_CLAUSE_TYPES:
        return CANONICAL_CLAUSE_TYPES[t]
    # containment pass ("Fee Terms (Retainer Fee)" → payment)
    for syn, canon in CANONICAL_CLAUSE_TYPES.items():
        if syn in t:
            return canon
    # word-overlap fallback
    words = {w for w in re.split(r"[^a-z]+", t) if len(w) > 3}
    for syn, canon in CANONICAL_CLAUSE_TYPES.items():
        syn_words = {w for w in syn.split() if len(w) > 3}
        if syn_words and (words & syn_words):
            return canon
    return None


def org_scope_contract_ids(db, user) -> list:
    """COMPLETED contracts across the user's organization (clause-type presence
    stats are metadata-level and shareable cross-BU by design)."""
    q = db.query(Contract.id).filter(Contract.status == ContractStatus.COMPLETED)
    if user.org_id:
        q = (q.join(BusinessUnit, Contract.business_unit_id == BusinessUnit.id)
             .filter(BusinessUnit.org_id == user.org_id))
    else:
        q = q.filter(Contract.user_id == user.id)
    return [r[0] for r in q.all()]


def compute_insights(db, contract: Contract, user) -> dict:
    clauses = db.query(ContractClause).filter(ContractClause.contract_id == contract.id).all()
    own_canon = {c for c in (canonicalize(cl.clause_type) for cl in clauses) if c}

    # ---------------- Coverage gaps: portfolio norm ----------------
    peer_ids = [cid for cid in org_scope_contract_ids(db, user) if cid != contract.id]
    peer_filter_type = contract.contract_type
    peers_by_contract: dict = defaultdict(set)
    peer_names: dict = {}
    if peer_ids:
        peer_q = (
            db.query(ContractClause.contract_id, ContractClause.clause_type,
                     Contract.filename, Contract.contract_type)
            .join(Contract, Contract.id == ContractClause.contract_id)
            .filter(ContractClause.contract_id.in_(peer_ids))
        )
        for cid, ctype, fname, cont_type in peer_q.all():
            # prefer same-type peers; fall back to全部 when the type is unset
            if peer_filter_type and cont_type and cont_type != peer_filter_type:
                continue
            canon = canonicalize(ctype)
            if canon:
                peers_by_contract[cid].add(canon)
                peer_names[cid] = fname

    coverage_gaps = []
    n_peers = len(peers_by_contract)
    if n_peers >= 2:
        presence: dict = defaultdict(list)
        for cid, canon_set in peers_by_contract.items():
            for canon in canon_set:
                presence[canon].append(cid)
        for canon, cids in sorted(presence.items(), key=lambda kv: -len(kv[1])):
            pct = round(100 * len(cids) / n_peers)
            if pct >= 60 and canon not in own_canon:
                coverage_gaps.append({
                    "clause_type": HUMAN_NAMES.get(canon, canon.replace("_", " ").title()),
                    "canonical": canon,
                    "presence_pct": pct,
                    "source": "portfolio",
                    "sample_contracts": [
                        {"id": str(cid), "filename": peer_names.get(cid, "")}
                        for cid in cids[:3]
                    ],
                    "suggested_text": None,
                })

    # Template source: deviation analysis DELETED items are confirmed gaps
    deviation = (contract.metadata_json or {}).get("deviation_analysis") or {}
    for item in deviation.get("items", []):
        if item.get("deviation_type") == "DELETED":
            coverage_gaps.insert(0, {
                "clause_type": item.get("clause_type"),
                "canonical": canonicalize(item.get("clause_type")),
                "presence_pct": 100,
                "source": "template",
                "sample_contracts": [],
                "suggested_text": (item.get("suggested_language_to_restore_standard")
                                   or item.get("template_text")),
            })

    # ---------------- Precedents from previous approvals ----------------
    # Approved library: explicit user approvals first, then LOW-risk clauses.
    approved_ids = {
        r[0] for r in db.query(ClauseFeedback.clause_id)
        .filter(ClauseFeedback.is_risky.is_(False)).all()
    }
    scope_ids = org_scope_contract_ids(db, user)
    lib_q = (
        db.query(ContractClause, Contract.filename)
        .join(Contract, Contract.id == ContractClause.contract_id)
        .filter(ContractClause.contract_id.in_(scope_ids)) if scope_ids else None
    )
    library = []
    if lib_q is not None:
        for cl, fname in lib_q.all():
            is_approved = cl.id in approved_ids
            is_low = cl.risk_level == RiskLevel.LOW
            if not (is_approved or is_low):
                continue
            canon = canonicalize(cl.clause_type)
            if not canon:
                continue
            library.append({
                "clause": cl, "filename": fname, "canon": canon,
                "approved_via": "feedback" if is_approved else "low_risk",
            })
    # feedback-approved entries rank first
    library.sort(key=lambda e: 0 if e["approved_via"] == "feedback" else 1)

    precedents = []
    # (a) for each gap → best approved clause of that canonical type
    for gap in coverage_gaps:
        canon = gap.get("canonical")
        if not canon:
            continue
        for entry in library:
            if entry["canon"] == canon and entry["clause"].contract_id != contract.id:
                precedents.append({
                    "for": {"gap": gap["clause_type"], "clause_id": None},
                    "clause_id": str(entry["clause"].id),
                    "contract_id": str(entry["clause"].contract_id),
                    "contract_filename": entry["filename"],
                    "clause_type": entry["clause"].clause_type,
                    "text_excerpt": (entry["clause"].text_content or "")[:420],
                    "approved_via": entry["approved_via"],
                })
                break

    # (b) for each HIGH/CRITICAL clause → up to 2 approved same-type precedents
    risky = [cl for cl in clauses if cl.risk_level in (RiskLevel.HIGH, RiskLevel.CRITICAL)]
    for cl in risky[:6]:
        canon = canonicalize(cl.clause_type)
        if not canon:
            continue
        found = 0
        for entry in library:
            if found >= 2:
                break
            if entry["canon"] == canon and entry["clause"].contract_id != contract.id:
                precedents.append({
                    "for": {"gap": None, "clause_id": str(cl.id), "clause_type": cl.clause_type},
                    "clause_id": str(entry["clause"].id),
                    "contract_id": str(entry["clause"].contract_id),
                    "contract_filename": entry["filename"],
                    "clause_type": entry["clause"].clause_type,
                    "text_excerpt": (entry["clause"].text_content or "")[:420],
                    "approved_via": entry["approved_via"],
                })
                found += 1

    # ---------------- Ambiguity flags (cheap deterministic pass) ----------------
    ambiguities = []
    for cl in clauses:
        debug = cl.risk_debug_json or {}
        conf = debug.get("confidence")
        reason = None
        if isinstance(conf, (int, float)) and conf < 0.5:
            reason = f"Low analysis confidence ({conf:.2f}) — the language resists a clear reading."
        elif cl.risk_reasoning and AMBIGUITY_RE.search(cl.risk_reasoning):
            m = AMBIGUITY_RE.search(cl.risk_reasoning)
            reason = f"Risk analysis flags unclear language ('{m.group(0)}')."
        if reason:
            ambiguities.append({
                "clause_id": str(cl.id),
                "clause_type": cl.clause_type,
                "risk_level": cl.risk_level.value if cl.risk_level else "LOW",
                "reason": reason,
                "confidence": conf,
                "text_excerpt": (cl.text_content or "")[:220],
            })

    return {
        "coverage_gaps": coverage_gaps[:10],
        "precedents": precedents[:10],
        "ambiguities": ambiguities[:10],
        "peer_count": n_peers,
        "peer_contract_type": peer_filter_type,
    }
