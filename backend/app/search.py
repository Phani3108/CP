"""Natural-language contract search — the "metadata route".

A single forced Gemini function call translates a question into structured
filters; the arguments are re-validated through Pydantic before they touch
SQLAlchemy (LLM output NEVER reaches SQL directly). Any failure degrades to
a deterministic ILIKE keyword fallback so search never dead-ends.
"""
import os
import json
import re
from datetime import date, datetime, timezone

from pydantic import BaseModel, Field, field_validator
from sqlalchemy import or_, func

from .models import Contract, ContractStatus

CONTRACT_TYPES = ["MSA", "NDA", "SOW", "SLA", "DPA", "LICENSE", "SERVICES",
                  "PURCHASE", "EMPLOYMENT", "LEASE", "AMENDMENT", "OTHER"]

RISK_ORDER = {"LOW": 0, "MEDIUM": 1, "HIGH": 2, "CRITICAL": 3}

SEARCH_TOOL = {
    "type": "function",
    "function": {
        "name": "search_contracts",
        "description": "Search the contract repository using structured metadata filters.",
        "parameters": {
            "type": "object",
            "properties": {
                "counterparty_contains": {"type": "string", "description": "Substring of the vendor/counterparty name"},
                "company_contains": {"type": "string", "description": "Substring of our own party name"},
                "contract_types": {"type": "array", "items": {"type": "string", "enum": CONTRACT_TYPES}},
                "effective_after": {"type": "string", "description": "ISO date YYYY-MM-DD"},
                "effective_before": {"type": "string", "description": "ISO date YYYY-MM-DD"},
                "expiry_after": {"type": "string", "description": "ISO date YYYY-MM-DD"},
                "expiry_before": {"type": "string", "description": "ISO date YYYY-MM-DD"},
                "auto_renewal": {"type": "boolean"},
                "min_value": {"type": "number"},
                "max_value": {"type": "number"},
                "business_units": {"type": "array", "items": {"type": "string"}},
                "governing_law_contains": {"type": "string"},
                "risk_at_least": {"type": "string", "enum": ["LOW", "MEDIUM", "HIGH", "CRITICAL"]},
                "status": {"type": "string", "enum": ["PENDING", "PROCESSING", "COMPLETED", "FAILED"]},
                "max_completeness": {"type": "number", "description": "Return contracts whose metadata completeness score is at or below this (0..1) — used for 'incomplete contracts'"},
                "text_terms": {"type": "string", "description": "Free text to match against filename and party names"},
                "content_terms": {"type": "string", "description": "Words that must appear in the contract body text"},
                "sort_by": {"type": "string", "enum": ["expiry_date", "effective_date", "total_value", "created_at", "counterparty"]},
                "sort_dir": {"type": "string", "enum": ["asc", "desc"]},
                "limit": {"type": "integer", "minimum": 1, "maximum": 100},
            },
        },
    },
}


class SearchFilters(BaseModel):
    """Validated, typed filter set — the only thing allowed to build SQL."""
    counterparty_contains: str | None = None
    company_contains: str | None = None
    contract_types: list[str] = Field(default_factory=list)
    effective_after: date | None = None
    effective_before: date | None = None
    expiry_after: date | None = None
    expiry_before: date | None = None
    auto_renewal: bool | None = None
    min_value: float | None = None
    max_value: float | None = None
    business_units: list[str] = Field(default_factory=list)
    governing_law_contains: str | None = None
    risk_at_least: str | None = None
    status: str | None = None
    max_completeness: float | None = None
    text_terms: str | None = None
    content_terms: str | None = None
    sort_by: str = "created_at"
    sort_dir: str = "desc"
    limit: int = 25

    @field_validator("contract_types")
    @classmethod
    def _valid_types(cls, v):
        return [t for t in v if t in CONTRACT_TYPES]

    @field_validator("risk_at_least")
    @classmethod
    def _valid_risk(cls, v):
        return v if v in RISK_ORDER else None

    @field_validator("status")
    @classmethod
    def _valid_status(cls, v):
        return v if v in {"PENDING", "PROCESSING", "COMPLETED", "FAILED"} else None

    @field_validator("sort_by")
    @classmethod
    def _valid_sort(cls, v):
        return v if v in {"expiry_date", "effective_date", "total_value", "created_at", "counterparty"} else "created_at"

    @field_validator("sort_dir")
    @classmethod
    def _valid_dir(cls, v):
        return v if v in {"asc", "desc"} else "desc"

    @field_validator("limit")
    @classmethod
    def _clamp_limit(cls, v):
        return max(1, min(int(v or 25), 100))

    def applied(self) -> dict:
        """Non-empty filters, for UI chips."""
        out = {}
        for k, v in self.model_dump().items():
            if k in {"sort_by", "sort_dir", "limit"}:
                continue
            if v in (None, [], ""):
                continue
            out[k] = v.isoformat() if isinstance(v, date) else v
        return out

    def describe(self) -> str:
        parts = []
        if self.contract_types:
            parts.append(f"type in [{', '.join(self.contract_types)}]")
        if self.counterparty_contains:
            parts.append(f"counterparty ~ '{self.counterparty_contains}'")
        if self.company_contains:
            parts.append(f"company ~ '{self.company_contains}'")
        if self.expiry_before:
            parts.append(f"expires before {self.expiry_before}")
        if self.expiry_after:
            parts.append(f"expires after {self.expiry_after}")
        if self.effective_before:
            parts.append(f"effective before {self.effective_before}")
        if self.effective_after:
            parts.append(f"effective after {self.effective_after}")
        if self.auto_renewal is not None:
            parts.append("auto-renewing" if self.auto_renewal else "non-renewing")
        if self.min_value is not None:
            parts.append(f"value ≥ {self.min_value:,.0f}")
        if self.max_value is not None:
            parts.append(f"value ≤ {self.max_value:,.0f}")
        if self.business_units:
            parts.append(f"BU in [{', '.join(self.business_units)}]")
        if self.governing_law_contains:
            parts.append(f"governing law ~ '{self.governing_law_contains}'")
        if self.risk_at_least:
            parts.append(f"risk ≥ {self.risk_at_least}")
        if self.status:
            parts.append(f"status = {self.status}")
        if self.max_completeness is not None:
            parts.append(f"completeness ≤ {self.max_completeness}")
        if self.text_terms:
            parts.append(f"matching '{self.text_terms}'")
        if self.content_terms:
            parts.append(f"content mentions '{self.content_terms}'")
        return "Contracts " + ("with " + "; ".join(parts) if parts else "(no filters — most recent)")


def build_query(base_query, f: SearchFilters):
    """Apply validated filters to a SQLAlchemy query over Contract."""
    q = base_query
    if f.counterparty_contains:
        # counterparty may be unset on older rows — fall back to company/filename
        term = f"%{f.counterparty_contains}%"
        q = q.filter(or_(Contract.counterparty.ilike(term),
                         Contract.company.ilike(term),
                         Contract.filename.ilike(term)))
    if f.company_contains:
        q = q.filter(Contract.company.ilike(f"%{f.company_contains}%"))
    if f.contract_types:
        q = q.filter(Contract.contract_type.in_(f.contract_types))
    if f.effective_after:
        q = q.filter(Contract.effective_date >= f.effective_after)
    if f.effective_before:
        q = q.filter(Contract.effective_date <= f.effective_before)
    if f.expiry_after:
        q = q.filter(Contract.expiry_date >= f.expiry_after)
    if f.expiry_before:
        q = q.filter(Contract.expiry_date <= f.expiry_before)
    if f.auto_renewal is not None:
        q = q.filter(Contract.auto_renewal.is_(f.auto_renewal))
    if f.min_value is not None:
        q = q.filter(Contract.total_value >= f.min_value)
    if f.max_value is not None:
        q = q.filter(Contract.total_value <= f.max_value)
    if f.business_units:
        lowered = [b.lower() for b in f.business_units]
        q = q.filter(func.lower(Contract.business_unit).in_(lowered))
    if f.governing_law_contains:
        q = q.filter(Contract.governing_law.ilike(f"%{f.governing_law_contains}%"))
    if f.status:
        q = q.filter(Contract.status == ContractStatus(f.status))
    if f.max_completeness is not None:
        q = q.filter(Contract.completeness_score <= f.max_completeness)
    if f.text_terms:
        term = f"%{f.text_terms}%"
        q = q.filter(or_(Contract.filename.ilike(term),
                         Contract.company.ilike(term),
                         Contract.counterparty.ilike(term),
                         Contract.contract_type.ilike(term)))

    sort_col = {
        "expiry_date": Contract.expiry_date,
        "effective_date": Contract.effective_date,
        "total_value": Contract.total_value,
        "counterparty": Contract.counterparty,
        "created_at": Contract.created_at,
    }[f.sort_by]
    q = q.order_by(sort_col.asc().nullslast() if f.sort_dir == "asc" else sort_col.desc().nullslast())
    return q


def apply_risk_floor(rows, risk_at_least: str | None):
    """Post-filter on metadata_json risk_counts (small result sets)."""
    if not risk_at_least:
        return rows
    floor = RISK_ORDER[risk_at_least]
    out = []
    for c in rows:
        counts = (c.metadata_json or {}).get("risk_counts") or {}
        worst = max((RISK_ORDER.get(k, -1) for k, v in counts.items() if v), default=-1)
        if worst >= floor:
            out.append(c)
    return out


def contract_row(c: Contract) -> dict:
    """Search-result projection (no raw_text, no full metadata blob)."""
    counts = (c.metadata_json or {}).get("risk_counts") or {}
    overall = None
    if c.status == ContractStatus.COMPLETED:
        for lvl in ("CRITICAL", "HIGH", "MEDIUM", "LOW"):
            if counts.get(lvl):
                overall = lvl
                break
        overall = overall or "LOW"
    return {
        "id": str(c.id),
        "filename": c.filename,
        "company": c.company,
        "counterparty": c.counterparty,
        "contract_type": c.contract_type,
        "effective_date": c.effective_date.isoformat() if c.effective_date else None,
        "expiry_date": c.expiry_date.isoformat() if c.expiry_date else None,
        "auto_renewal": c.auto_renewal,
        "renewal_notice_days": c.renewal_notice_days,
        "total_value": float(c.total_value) if c.total_value is not None else None,
        "currency": c.currency,
        "business_unit": c.business_unit,
        "governing_law": c.governing_law,
        "status": c.status.value if c.status else None,
        "overall_risk": overall,
        "completeness_score": c.completeness_score,
        "created_at": c.created_at.isoformat() if c.created_at else None,
    }


async def parse_question_to_filters(question: str, known_business_units: list[str]) -> SearchFilters:
    """One forced Gemini function call → validated SearchFilters. Raises on failure."""
    import openai
    client = openai.AsyncOpenAI(api_key=os.getenv("OPENAI_API_KEY"))
    today = datetime.now(timezone.utc).date().isoformat()
    bu_note = f" Known business units: {', '.join(known_business_units)}." if known_business_units else ""
    system = (
        f"You translate natural-language questions about a contract repository into search filters. "
        f"Today is {today}. Relative phrases like 'expiring soon' mean within 90 days; "
        f"'this year' means the current calendar year.{bu_note} "
        "Call search_contracts exactly once with only the filters implied by the question."
    )
    model = (os.getenv("OPENAI_MODEL_CHAT") or "gpt-5.4").strip()
    resp = await client.chat.completions.create(
        model=model,
        messages=[{"role": "system", "content": system}, {"role": "user", "content": question}],
        tools=[SEARCH_TOOL],
        tool_choice={"type": "function", "function": {"name": "search_contracts"}},
        max_tokens=2048,
        temperature=0.0,
    )
    tool_calls = resp.choices[0].message.tool_calls or []
    if not tool_calls:
        raise ValueError("No tool call returned")
    args = json.loads(tool_calls[0].function.arguments or "{}")
    return SearchFilters(**{k: v for k, v in args.items() if k in SearchFilters.model_fields})


def fallback_filters(question: str) -> SearchFilters:
    """Deterministic keyword fallback when the LLM parse fails."""
    words = [w for w in re.findall(r"[A-Za-z0-9']+", question or "") if len(w) > 2]
    # single most distinctive token — a joined phrase would rarely substring-match
    return SearchFilters(text_terms=max(words, key=len) if words else None)
