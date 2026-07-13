import os
import bcrypt
import jwt
import re
from datetime import datetime, timedelta, timezone
from fastapi import FastAPI, UploadFile, File, BackgroundTasks, HTTPException, Depends, Header
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel
from sqlalchemy.orm import Session
from sqlalchemy import text, func, or_

# Security & JWT settings
JWT_SECRET = os.getenv("JWT_SECRET")
if not JWT_SECRET:
    raise ValueError("JWT_SECRET environment variable must be set for security reasons.")
JWT_ALGORITHM = "HS256"
ACCESS_TOKEN_EXPIRE_MINUTES = 60 * 24 * 7 # 1 week
def is_signup_disabled() -> bool:
    # Read dynamically so docker `.env` changes take effect after container restart,
    # and so `uvicorn --reload` doesn't accidentally keep stale module-level state.
    return os.getenv("DISABLE_SIGNUP", "false").lower().strip() in ("true", "1", "yes")


def _openai_chat_model() -> str:
    # Used by direct OpenAI calls in this module (not pydantic-ai Agents).
    return (os.getenv("OPENAI_MODEL_CHAT") or "gpt-5.4").strip()


def _openai_assistant_model() -> str:
    return (os.getenv("OPENAI_MODEL_ASSISTANT") or _openai_chat_model()).strip()


def _openai_embedding_model() -> str:
    return (os.getenv("OPENAI_EMBEDDING_MODEL") or "text-embedding-3-small").strip()


def _embedding_dimensions() -> int:
    # Must match the vector(1536) column on contract_clauses.
    return int(os.getenv("OPENAI_EMBEDDING_DIMENSIONS", "1536"))

def get_password_hash(password: str) -> str:
    pwd_bytes = password.encode('utf-8')
    salt = bcrypt.gensalt()
    hashed = bcrypt.hashpw(pwd_bytes, salt)
    return hashed.decode('utf-8')

def verify_password(plain_password: str, hashed_password: str) -> bool:
    try:
        return bcrypt.checkpw(plain_password.encode('utf-8'), hashed_password.encode('utf-8'))
    except Exception:
        return False

def create_access_token(data: dict, expires_delta: timedelta | None = None) -> str:
    to_encode = data.copy()
    if expires_delta:
        expire = datetime.now(timezone.utc) + expires_delta
    else:
        expire = datetime.now(timezone.utc) + timedelta(minutes=15)
    to_encode.update({"exp": expire})
    encoded_jwt = jwt.encode(to_encode, JWT_SECRET, algorithm=JWT_ALGORITHM)
    return encoded_jwt

# Optional OpenTelemetry tracing — only when an OTLP endpoint is configured
# AND the packages are installed (kept out of the default requirements to
# keep the deploy bundle small; the app never instrumented spans anyway).
if os.getenv("OTEL_EXPORTER_OTLP_ENDPOINT"):
    try:
        from opentelemetry import trace
        from opentelemetry.sdk.trace import TracerProvider
        from opentelemetry.sdk.trace.export import BatchSpanProcessor
        from opentelemetry.exporter.otlp.proto.grpc.trace_exporter import OTLPSpanExporter
        from opentelemetry.sdk.resources import Resource

        provider = TracerProvider(resource=Resource.create({"service.name": "contractspulse-api"}))
        provider.add_span_processor(BatchSpanProcessor(
            OTLPSpanExporter(endpoint=os.getenv("OTEL_EXPORTER_OTLP_ENDPOINT"))))
        trace.set_tracer_provider(provider)
    except ImportError:
        print("OTEL endpoint set but opentelemetry packages not installed; tracing disabled.")

app = FastAPI(
    title="ContractsPulse API",
    description="Backend API for Legal-Grade RAG and Contract Intelligence",
    version="0.1.0"
)

# CORS to allow SvelteKit frontend to communicate
cors_origins_str = os.getenv("CORS_ORIGINS", "http://localhost:5173")
cors_origins = [origin.strip() for origin in cors_origins_str.split(",") if origin.strip()]

app.add_middleware(
    CORSMiddleware,
    allow_origins=cors_origins,
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

@app.get("/")
async def root():
    return {"message": "ContractsPulse API is running"}

@app.get("/health")
async def health_check():
    return {"status": "ok"}

from .parser import extract_text_from_pdf, extract_contract_metadata, standardized_filename, compute_text_hash
from .agents import process_contract_text, process_contract_text_fallback, _heuristic_redline, _first_sentence, _heuristic_risk, _enrich_ip_clause, verify_previous_redlines, extract_contract_obligations
from .database import engine, Base, get_db
from .models import User, Contract, ContractClause, ContractStatus, RiskLevel, ContractEvent, ClauseFeedback, ContractReminder, ContractTemplate, TemplateClause

def get_current_user(
    authorization: str | None = Header(None),
    db: Session = Depends(get_db)
):
    # CLI compatibility fallback:
    # If the Authorization header is missing, fall back to the default seeded admin@admin.com user.
    # This allows unauthenticated API calls (like from the local CLI out-of-the-box) to function normally.
    token = None
    if authorization and authorization.lower().startswith("bearer "):
        token = authorization.split(" ")[1]
    
    if not token:
        raise HTTPException(
            status_code=401,
            detail="Not authenticated",
            headers={"WWW-Authenticate": "Bearer"},
        )

    try:
        payload = jwt.decode(token, JWT_SECRET, algorithms=[JWT_ALGORITHM])
        user_id: str = payload.get("sub")
        if user_id is None:
            raise HTTPException(status_code=401, detail="Invalid token payload.")
    except jwt.ExpiredSignatureError:
        raise HTTPException(status_code=401, detail="Token has expired.")
    except jwt.InvalidTokenError:
        raise HTTPException(status_code=401, detail="Invalid authentication credentials.")

    user = db.query(User).filter(User.id == user_id).first()
    if user is None:
        raise HTTPException(status_code=401, detail="User not found.")
    return user



# ---------------------------------------------------------------------------
# Cross-business access control (org / business-unit / role)
# ---------------------------------------------------------------------------

def can_read_full(user, contract) -> bool:
    """Full contract access: owner, same business unit, or org admin."""
    if contract.user_id == user.id:
        return True
    if user.business_unit_id is not None and contract.business_unit_id == user.business_unit_id:
        return True
    return (getattr(user, "role", None) or "member") == "org_admin"


def get_accessible_contract(db, contract_id: str, user):
    """Fetch a contract enforcing access rules.

    404 when it doesn't exist or belongs to another organization,
    403 when it exists in a sister business unit (discovery-only visibility).
    """
    from .models import BusinessUnit
    contract = db.query(Contract).filter(Contract.id == contract_id).first()
    if not contract:
        raise HTTPException(status_code=404, detail="Contract not found")
    if can_read_full(user, contract):
        return contract
    same_org = (
        user.org_id is not None
        and contract.business_unit_id is not None
        and db.query(BusinessUnit)
        .filter(BusinessUnit.id == contract.business_unit_id, BusinessUnit.org_id == user.org_id)
        .first() is not None
    )
    if same_org:
        raise HTTPException(status_code=403, detail="Cross-business-unit access is limited to discovery")
    raise HTTPException(status_code=404, detail="Contract not found")


def bu_scope_criterion(user):
    """Aggregate-endpoint scope: the user's business unit's contracts plus their own."""
    if user.business_unit_id is not None:
        return or_(Contract.business_unit_id == user.business_unit_id,
                   Contract.user_id == user.id)
    return Contract.user_id == user.id


def _resolve_business_unit(db, current_user, business_unit_id: str | None):
    """BU for a new contract: explicit choice -> uploader's BU. Returns (id, name)."""
    from .models import BusinessUnit
    bu = None
    if business_unit_id:
        bu = (db.query(BusinessUnit)
              .filter(BusinessUnit.id == business_unit_id,
                      BusinessUnit.org_id == current_user.org_id)
              .first())
    if bu is None and current_user.business_unit_id is not None:
        bu = db.query(BusinessUnit).filter(BusinessUnit.id == current_user.business_unit_id).first()
    return (bu.id if bu else None), (bu.name if bu else None)


class ContractTextIn(BaseModel):
    text: str
    business_unit_id: str | None = None


def log_contract_event(db: Session, contract_id: str, event_type: str, message: str, payload: dict | None = None):
    db.add(
        ContractEvent(
            contract_id=contract_id,
            event_type=event_type,
            message=message,
            payload_json=payload or {},
        )
    )


@app.post("/api/v1/contracts/text")
async def create_contract_from_text(
    payload: ContractTextIn,
    background_tasks: BackgroundTasks,
    parent_id: str | None = None,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    raw_text = (payload.text or "").strip()
    if not raw_text:
        raise HTTPException(status_code=400, detail="text is required")

    meta = extract_contract_metadata(raw_text)
    from datetime import datetime
    upload_date = datetime.utcnow().date().isoformat()
    std_name = standardized_filename(meta, upload_date)
    text_hash = compute_text_hash(raw_text)

    # Bypass cache if it's a version revision upload
    existing_contract = None
    if not parent_id:
        existing_contract = db.query(Contract).filter(Contract.file_hash == text_hash, Contract.user_id == current_user.id).first()
        if existing_contract:
            # Refresh metadata + filename each submission, but skip re-analysis if already processed.
            existing = existing_contract.metadata_json or {}
            existing_contract.filename = std_name
            existing_contract.metadata_json = {**existing, "raw_text": raw_text, **meta}

            if existing_contract.status == ContractStatus.FAILED:
                existing_contract.status = ContractStatus.PROCESSING
                log_contract_event(db, str(existing_contract.id), "ingest", "Text re-submitted; retrying failed analysis", {"cache": "hit", "mode": "text"})
                db.commit()
                background_tasks.add_task(analyze_contract_background, str(existing_contract.id), raw_text)
                return {
                    "filename": existing_contract.filename,
                    "contract_id": str(existing_contract.id),
                    "status": "PROCESSING",
                    "message": "Retrying failed contract analysis.",
                }

            log_contract_event(db, str(existing_contract.id), "ingest", "Text re-submitted; using cached result", {"cache": "hit", "mode": "text", "status": existing_contract.status.value})
            db.commit()
            return {
                "filename": existing_contract.filename,
                "contract_id": str(existing_contract.id),
                "status": existing_contract.status.value if existing_contract.status else "COMPLETED",
                "message": ("Contract already processing." if existing_contract.status == ContractStatus.PROCESSING else "Contract already processed."),
            }

    # Version chaining support
    metadata = {"raw_text": raw_text, **meta}
    parent_contract = None
    version_number = 1
    if parent_id:
        parent_contract = db.query(Contract).filter(Contract.id == parent_id, Contract.user_id == current_user.id).first()
        if parent_contract:
            parent_version = parent_contract.metadata_json.get("version_number", 1) if parent_contract.metadata_json else 1
            version_number = parent_version + 1
            metadata["parent_contract_id"] = parent_id
            metadata["version_number"] = version_number

    bu_id, bu_name = _resolve_business_unit(db, current_user, payload.business_unit_id)
    new_contract = Contract(
        filename=std_name,
        file_hash=text_hash,
        status=ContractStatus.PROCESSING,
        metadata_json=metadata,
        user_id=current_user.id,
        business_unit_id=bu_id,
        business_unit=bu_name,
    )
    db.add(new_contract)
    db.flush()
    log_contract_event(db, str(new_contract.id), "ingest", "Text submitted for analysis", {"cache": "miss", "mode": "text"})
    db.commit()
    db.refresh(new_contract)

    background_tasks.add_task(analyze_contract_background, str(new_contract.id), raw_text)

    return {
        "filename": new_contract.filename,
        "contract_id": str(new_contract.id),
        "status": new_contract.status.value,
        "message": "Analysis running in background.",
    }

@app.on_event("startup")
async def startup_event():
    with engine.begin() as conn:
        conn.execute(text("CREATE EXTENSION IF NOT EXISTS vector;"))

    # Create all model tables FIRST — on a truly fresh database the ALTER
    # fix-ups below would otherwise fail with "relation does not exist".
    Base.metadata.create_all(bind=engine)

    # Remove unique index constraint on contracts.file_hash if it exists to allow per-user duplicate uploads
    with engine.begin() as conn:
        conn.execute(text("ALTER TABLE contracts DROP CONSTRAINT IF EXISTS uq_contracts_file_hash;"))
        conn.execute(text("DROP INDEX IF EXISTS ix_contracts_file_hash;"))
        conn.execute(text("CREATE INDEX IF NOT EXISTS ix_contracts_file_hash ON contracts(file_hash);"))

    # Contract deletion must cascade to events/feedback. The models predate
    # ON DELETE CASCADE on these FKs, so re-create them idempotently.
    with engine.begin() as conn:
        conn.execute(text(
            "ALTER TABLE contract_events DROP CONSTRAINT IF EXISTS contract_events_contract_id_fkey;"
        ))
        conn.execute(text(
            "ALTER TABLE contract_events ADD CONSTRAINT contract_events_contract_id_fkey "
            "FOREIGN KEY (contract_id) REFERENCES contracts(id) ON DELETE CASCADE;"
        ))
        conn.execute(text(
            "ALTER TABLE clause_feedback DROP CONSTRAINT IF EXISTS clause_feedback_contract_id_fkey;"
        ))
        conn.execute(text(
            "ALTER TABLE clause_feedback ADD CONSTRAINT clause_feedback_contract_id_fkey "
            "FOREIGN KEY (contract_id) REFERENCES contracts(id) ON DELETE CASCADE;"
        ))
        conn.execute(text(
            "ALTER TABLE clause_feedback DROP CONSTRAINT IF EXISTS clause_feedback_clause_id_fkey;"
        ))
        conn.execute(text(
            "ALTER TABLE clause_feedback ADD CONSTRAINT clause_feedback_clause_id_fkey "
            "FOREIGN KEY (clause_id) REFERENCES contract_clauses(id) ON DELETE CASCADE;"
        ))
        
    # Lightweight schema catch-up for additive columns (demo-friendly; avoids migrations).
    with engine.begin() as conn:
        conn.execute(
            text(
                "ALTER TABLE contract_clauses "
                "ADD COLUMN IF NOT EXISTS risk_debug_json jsonb DEFAULT '{}'::jsonb;"
            )
        )
        conn.execute(
            text(
                "ALTER TABLE contract_clauses "
                "ADD COLUMN IF NOT EXISTS embedding vector(1536);"
            )
        )
    # Ensure contracts has user_id
    with engine.begin() as conn:
        conn.execute(
            text(
                "ALTER TABLE contracts ADD COLUMN IF NOT EXISTS user_id uuid REFERENCES users(id) ON DELETE CASCADE;"
            )
        )
    # Stories 013/014: ensure clause_feedback table exists.
    with engine.begin() as conn:
        conn.execute(text(
            "CREATE TABLE IF NOT EXISTS clause_feedback ("
            "  id uuid PRIMARY KEY DEFAULT gen_random_uuid(), "
            "  contract_id uuid NOT NULL REFERENCES contracts(id) ON DELETE CASCADE, "
            "  clause_id uuid NOT NULL REFERENCES contract_clauses(id) ON DELETE CASCADE, "
            "  is_risky boolean NOT NULL, "
            "  note text, "
            "  created_at timestamptz NOT NULL DEFAULT now() "
            ");"
        ))

    # Renewal/notice automation: reminders
    with engine.begin() as conn:
        conn.execute(text(
            "CREATE TABLE IF NOT EXISTS contract_reminders ("
            "  id uuid PRIMARY KEY DEFAULT gen_random_uuid(), "
            "  user_id uuid NOT NULL REFERENCES users(id) ON DELETE CASCADE, "
            "  contract_id uuid NOT NULL REFERENCES contracts(id) ON DELETE CASCADE, "
            "  reminder_type text NOT NULL, "
            "  status text NOT NULL DEFAULT 'OPEN', "
            "  due_date timestamptz NOT NULL, "
            "  title text NOT NULL, "
            "  body text, "
            "  letter_json jsonb DEFAULT '{}'::jsonb, "
            "  created_at timestamptz NOT NULL DEFAULT now() "
            ");"
        ))
        conn.execute(text("CREATE INDEX IF NOT EXISTS ix_contract_reminders_user_due ON contract_reminders(user_id, due_date);"))

    # Contract template library
    with engine.begin() as conn:
        conn.execute(text(
            "CREATE TABLE IF NOT EXISTS contract_templates ("
            "  id uuid PRIMARY KEY DEFAULT gen_random_uuid(), "
            "  user_id uuid NOT NULL REFERENCES users(id) ON DELETE CASCADE, "
            "  name text NOT NULL, "
            "  description text, "
            "  raw_text text NOT NULL, "
            "  created_at timestamptz NOT NULL DEFAULT now() "
            ");"
        ))
        conn.execute(text("CREATE INDEX IF NOT EXISTS ix_contract_templates_user_created ON contract_templates(user_id, created_at);"))
        # Additive: template pipeline status + segmented template clauses
        conn.execute(text("ALTER TABLE contract_templates ADD COLUMN IF NOT EXISTS status text DEFAULT 'PENDING';"))
        conn.execute(text(
            "CREATE TABLE IF NOT EXISTS template_clauses ("
            "  id uuid PRIMARY KEY DEFAULT gen_random_uuid(), "
            "  template_id uuid NOT NULL REFERENCES contract_templates(id) ON DELETE CASCADE, "
            "  clause_type text, "
            "  text_content text NOT NULL, "
            "  position_index int DEFAULT 0, "
            "  embedding vector(1536), "
            "  created_at timestamptz NOT NULL DEFAULT now() "
            ");"
        ))
        conn.execute(text("CREATE INDEX IF NOT EXISTS ix_template_clauses_template ON template_clauses(template_id);"))

    # Promoted, queryable contract metadata columns (dual-written with metadata_json)
    with engine.begin() as conn:
        for ddl in [
            "ALTER TABLE contracts ADD COLUMN IF NOT EXISTS company text;",
            "ALTER TABLE contracts ADD COLUMN IF NOT EXISTS counterparty text;",
            "ALTER TABLE contracts ADD COLUMN IF NOT EXISTS contract_type text;",
            "ALTER TABLE contracts ADD COLUMN IF NOT EXISTS effective_date date;",
            "ALTER TABLE contracts ADD COLUMN IF NOT EXISTS expiry_date date;",
            "ALTER TABLE contracts ADD COLUMN IF NOT EXISTS auto_renewal boolean;",
            "ALTER TABLE contracts ADD COLUMN IF NOT EXISTS renewal_notice_days integer;",
            "ALTER TABLE contracts ADD COLUMN IF NOT EXISTS total_value numeric(14,2);",
            "ALTER TABLE contracts ADD COLUMN IF NOT EXISTS currency varchar(3);",
            "ALTER TABLE contracts ADD COLUMN IF NOT EXISTS payment_terms text;",
            "ALTER TABLE contracts ADD COLUMN IF NOT EXISTS governing_law text;",
            "ALTER TABLE contracts ADD COLUMN IF NOT EXISTS business_unit text;",
            "ALTER TABLE contracts ADD COLUMN IF NOT EXISTS completeness_score real;",
            "CREATE INDEX IF NOT EXISTS ix_contracts_company_lower ON contracts (lower(company));",
            "CREATE INDEX IF NOT EXISTS ix_contracts_counterparty_lower ON contracts (lower(counterparty));",
            "CREATE INDEX IF NOT EXISTS ix_contracts_contract_type ON contracts (contract_type);",
            "CREATE INDEX IF NOT EXISTS ix_contracts_effective_date ON contracts (effective_date);",
            "CREATE INDEX IF NOT EXISTS ix_contracts_expiry_date ON contracts (expiry_date);",
            "CREATE INDEX IF NOT EXISTS ix_contracts_auto_renewal ON contracts (auto_renewal) WHERE auto_renewal IS TRUE;",
            "CREATE INDEX IF NOT EXISTS ix_contracts_total_value ON contracts (total_value);",
            "CREATE INDEX IF NOT EXISTS ix_contracts_business_unit_lower ON contracts (lower(business_unit));",
        ]:
            conn.execute(text(ddl))

    # Organizations / business units / roles (additive to existing tables)
    with engine.begin() as conn:
        for ddl in [
            "ALTER TABLE users ADD COLUMN IF NOT EXISTS org_id uuid REFERENCES organizations(id);",
            "ALTER TABLE users ADD COLUMN IF NOT EXISTS business_unit_id uuid REFERENCES business_units(id);",
            "ALTER TABLE users ADD COLUMN IF NOT EXISTS role text NOT NULL DEFAULT 'member';",
            "ALTER TABLE contracts ADD COLUMN IF NOT EXISTS business_unit_id uuid REFERENCES business_units(id);",
            "CREATE INDEX IF NOT EXISTS ix_contracts_business_unit_id ON contracts(business_unit_id);",
        ]:
            conn.execute(text(ddl))

    # One-time cheap backfill: copy regex-era JSONB fields into the new columns.
    with engine.begin() as conn:
        conn.execute(text(
            "UPDATE contracts SET company = metadata_json->>'company' "
            "WHERE company IS NULL AND metadata_json->>'company' IS NOT NULL;"
        ))
        conn.execute(text(
            "UPDATE contracts SET expiry_date = (metadata_json->>'expiry_date')::date "
            "WHERE expiry_date IS NULL AND metadata_json->>'expiry_date' ~ '^\\d{4}-\\d{2}-\\d{2}$';"
        ))
        conn.execute(text(
            "UPDATE contracts SET effective_date = (metadata_json->>'contract_date')::date "
            "WHERE effective_date IS NULL AND metadata_json->>'contract_date' ~ '^\\d{4}-\\d{2}-\\d{2}$';"
        ))
        conn.execute(text(
            "UPDATE contracts SET renewal_notice_days = (metadata_json->>'renewal_notice_days')::int "
            "WHERE renewal_notice_days IS NULL AND metadata_json->>'renewal_notice_days' ~ '^\\d+$';"
        ))

    # Full-text search over contract bodies + clause text (expression GIN indexes)
    with engine.begin() as conn:
        conn.execute(text(
            "CREATE INDEX IF NOT EXISTS ix_contracts_rawtext_fts ON contracts "
            "USING GIN (to_tsvector('english', coalesce(left(metadata_json->>'raw_text', 400000), '')));"
        ))
        conn.execute(text(
            "CREATE INDEX IF NOT EXISTS ix_clauses_fts ON contract_clauses "
            "USING GIN (to_tsvector('english', text_content));"
        ))

    # Seed default user if not exists
    from sqlalchemy.orm import sessionmaker
    SessionLocal = sessionmaker(bind=engine)
    db = SessionLocal()
    try:
        admin_exists = db.query(User).filter(User.email == "admin@admin.com").first()
        if not admin_exists:
            admin_hashed = get_password_hash("admin")
            default_admin = User(email="admin@admin.com", hashed_password=admin_hashed)
            db.add(default_admin)
            db.commit()
            print("Default admin user admin@admin.com created.")
    except Exception as e:
        print(f"Error seeding default user: {e}")
    finally:
        db.close()

    # Seed organization + business units + demo users (idempotent)
    from .models import Organization, BusinessUnit
    db = SessionLocal()
    try:
        org_name = os.getenv("ORG_NAME", "Jaggaer Parent").strip() or "Jaggaer Parent"
        org = db.query(Organization).filter(Organization.name == org_name).first()
        if not org:
            org = Organization(name=org_name)
            db.add(org)
            db.flush()

        bu_names = [b.strip() for b in os.getenv("DEMO_BUSINESS_UNITS", "Aerospace,Healthcare,Vernova").split(",") if b.strip()]
        bus = {}
        for name in bu_names:
            bu = db.query(BusinessUnit).filter(BusinessUnit.org_id == org.id, BusinessUnit.name == name).first()
            if not bu:
                bu = BusinessUnit(org_id=org.id, name=name)
                db.add(bu)
                db.flush()
            bus[name] = bu

        first_bu = bus.get(bu_names[0]) if bu_names else None
        admin = db.query(User).filter(User.email == "admin@admin.com").first()
        if admin and admin.org_id is None:
            admin.org_id = org.id
            admin.business_unit_id = first_bu.id if first_bu else None
            admin.role = "org_admin"

        # Second demo user in another BU for on-stage scope switching
        second_bu = bus.get(bu_names[1]) if len(bu_names) > 1 else None
        analyst = db.query(User).filter(User.email == "analyst@healthcare.demo").first()
        if not analyst and second_bu is not None:
            analyst = User(
                email="analyst@healthcare.demo",
                hashed_password=get_password_hash("analyst"),
                org_id=org.id,
                business_unit_id=second_bu.id,
                role="member",
            )
            db.add(analyst)

        # Attach org/BU to any users still missing one (e.g. self-signups)
        for u in db.query(User).filter(User.org_id.is_(None)).all():
            u.org_id = org.id
            if u.business_unit_id is None and first_bu is not None:
                u.business_unit_id = first_bu.id
        db.commit()

        # Contract BU backfill: match text column → BU, else owner's BU
        db.execute(text(
            "UPDATE contracts SET business_unit_id = bu.id FROM business_units bu "
            "WHERE contracts.business_unit_id IS NULL AND contracts.business_unit IS NOT NULL "
            "AND lower(contracts.business_unit) = lower(bu.name);"
        ))
        db.execute(text(
            "UPDATE contracts SET business_unit_id = u.business_unit_id FROM users u "
            "WHERE contracts.business_unit_id IS NULL AND contracts.user_id = u.id "
            "AND u.business_unit_id IS NOT NULL;"
        ))
        db.execute(text(
            "UPDATE contracts SET business_unit = bu.name FROM business_units bu "
            "WHERE contracts.business_unit_id = bu.id "
            "AND (contracts.business_unit IS DISTINCT FROM bu.name);"
        ))
        # Demo staging: move the Poppulo agreement to the second BU so
        # cross-business discovery has real content on stage.
        if second_bu is not None:
            db.execute(text(
                "UPDATE contracts SET business_unit_id = :bu, business_unit = :bn "
                "WHERE filename ILIKE '%poppulo%';"
            ), {"bu": str(second_bu.id), "bn": second_bu.name})
        db.commit()
        print(f"Org seed complete: {org_name} with BUs {', '.join(bu_names)}")
    except Exception as e:
        print(f"Error seeding organization: {e}")
    finally:
        db.close()

def extract_auto_renewal_info(text: str) -> dict | None:
    t = (text or "")
    if not re.search(r"\b(auto[\s-]?renew|auto[\s-]?renewal|automatically renew[s]?|renewal term)\b", t, flags=re.I):
        return None
    days = None
    m = re.search(r"\b(\d{1,3})\s+days?\s+(?:prior\s+to|before)\b", t, flags=re.I)
    if m:
        try:
            days = int(m.group(1))
        except Exception:
            days = None
    return {"opt_out_days_before_renewal": days}

def save_analysis_results(db: Session, contract_id: str, analysis_results: list):
    contract = db.query(Contract).filter(Contract.id == contract_id).first()
    if not contract:
        return
    
    # Track overall risk for metadata
    risk_counts = {"LOW": 0, "MEDIUM": 0, "HIGH": 0, "CRITICAL": 0}

    severity = {"LOW": 0, "MEDIUM": 1, "HIGH": 2, "CRITICAL": 3}
    top_candidates = []
    
    for item in analysis_results:
        clause_data = item['clause']
        risk_data = item['analysis']

        auto_info = extract_auto_renewal_info(clause_data.text_content)
        effective_risk_level = risk_data.risk_level
        effective_reasoning = risk_data.risk_reasoning
        if auto_info and effective_risk_level == "LOW":
            effective_risk_level = "MEDIUM"
            effective_reasoning = (
                "Auto-renewal clause detected. Confirm the opt-out deadline to avoid unwanted renewals."
                if not (effective_reasoning or "").strip()
                else effective_reasoning
            )

        # Story 006: HIGH/CRITICAL must include (a) a copy/paste-ready one-sentence rationale and
        # (b) a suggested replacement (redline). If the LLM omitted these, fill deterministically.
        effective_redline = risk_data.redline_suggestion
        if effective_risk_level in {"HIGH", "CRITICAL"}:
            effective_reasoning = _first_sentence(
                effective_reasoning
                or "This clause shifts disproportionate risk; the suggested replacement narrows scope and adds market-standard protections."
            )
            if not (effective_redline or "").strip():
                effective_redline = _heuristic_redline(
                    clause_data.clause_type,
                    clause_data.text_content,
                    effective_risk_level,
                )
        elif (effective_reasoning or "").strip():
            effective_reasoning = effective_reasoning.strip()

        # Story 009: enforce plain-English side-project warning for broad IP assignment clauses.
        effective_risk_level, effective_reasoning, effective_redline = _enrich_ip_clause(
            clause_data.clause_type,
            clause_data.text_content,
            effective_risk_level,
            effective_reasoning,
            effective_redline,
        )
        
        clause = ContractClause(
            contract_id=contract_id,
            clause_type=clause_data.clause_type,
            text_content=clause_data.text_content,
            risk_level=RiskLevel(effective_risk_level),
            risk_reasoning=effective_reasoning,
            redline_suggestion=effective_redline,
            risk_debug_json=getattr(risk_data, "debug_json", {}) or {},
        )
        db.add(clause)
        
        if effective_risk_level in risk_counts:
            risk_counts[effective_risk_level] += 1

        top_candidates.append({
            "clause_type": clause_data.clause_type,
            "risk_level": effective_risk_level,
            "risk_reasoning": effective_reasoning,
            "text_excerpt": (clause_data.text_content or "")[:600],
            "auto_renewal": auto_info,
        })

    contract.status = ContractStatus.COMPLETED
    top_candidates.sort(key=lambda x: (severity.get(x["risk_level"], 0), len(x.get("risk_reasoning") or "")), reverse=True)
    top_risks = top_candidates[:3]
    existing = contract.metadata_json or {}
    contract.metadata_json = {**existing, "risk_counts": risk_counts, "top_risks": top_risks}
    db.commit()

async def _run_analysis_pipeline(contract_id: str, raw_text: str, update_status):
    import asyncio
    import os
    from .database import SessionLocal
    from .agents import process_contract_text, process_contract_text_fallback

    timeout_s = float(os.getenv("CONTRACT_ANALYSIS_TIMEOUT_S", "60"))
    try:
        db0 = SessionLocal()
        try:
            log_contract_event(db0, contract_id, "analysis", "LLM analysis started", {"timeout_s": timeout_s})
            db0.commit()
        finally:
            db0.close()
        analysis_results = await asyncio.wait_for(
            process_contract_text(raw_text, update_status),
            timeout=timeout_s,
        )
        analysis_meta = {"analysis_mode": "llm"}
    except asyncio.TimeoutError:
        db0 = SessionLocal()
        try:
            log_contract_event(db0, contract_id, "analysis", "LLM timed out; using heuristic fallback", {"timeout_s": timeout_s})
            db0.commit()
        finally:
            db0.close()
        # Provider/network hang; fall back to deterministic processing so the pipeline completes.
        analysis_results = await process_contract_text_fallback(raw_text, update_status)
        analysis_meta = {"analysis_mode": "heuristic_fallback", "llm_timeout_s": timeout_s}
    return analysis_results, analysis_meta


async def _embed_texts(texts: list[str]) -> list[list[float]]:
    """Batch-embed texts with the configured model/dimensions (shared by
    contract clauses and template clauses)."""
    import openai
    client = openai.AsyncOpenAI(api_key=os.getenv("OPENAI_API_KEY"))
    out: list[list[float]] = []
    for i in range(0, len(texts), 100):
        chunk = texts[i:i + 100]
        res = await client.embeddings.create(
            model=_openai_embedding_model(), dimensions=_embedding_dimensions(),
            input=chunk
        )
        out.extend([item.embedding for item in res.data])
    return out


async def _generate_clause_embeddings(db, contract, contract_id: str):
    from .models import ContractClause
    existing = contract.metadata_json or {}
    try:
        existing["processing_step"] = "Generating clause embeddings for semantic search..."
        contract.metadata_json = existing
        db.commit()

        clauses = db.query(ContractClause).filter(ContractClause.contract_id == contract_id).all()
        if clauses:
            embeddings = await _embed_texts([c.text_content for c in clauses])

            if len(embeddings) == len(clauses):
                for clause, emb in zip(clauses, embeddings):
                    clause.embedding = emb
                db.commit()
                print(f"Successfully generated and saved embeddings for {len(clauses)} clauses.")
            else:
                print(f"Warning: Count mismatch in embeddings generation: {len(embeddings)} vs {len(clauses)}")
    except Exception as emb_err:
        print(f"Failed to generate clause embeddings: {emb_err}")


async def _verify_contract_redlines(db, contract, contract_id: str, analysis_meta: dict):
    from .models import ContractClause
    existing = contract.metadata_json or {}
    parent_id = existing.get("parent_contract_id")
    if parent_id:
        # Fetch parent contract clauses
        parent_clauses = db.query(ContractClause).filter(ContractClause.contract_id == parent_id).all()

        if parent_clauses:
            # Update status callback to keep client informed
            existing["processing_step"] = "Verifying resolved redlines against parent version..."
            contract.metadata_json = existing
            db.commit()

            # Fetch current contract clauses
            new_clauses = db.query(ContractClause).filter(ContractClause.contract_id == contract_id).all()

            # Run redline verification
            use_llm = analysis_meta.get("analysis_mode") == "llm"
            try:
                from .agents import verify_previous_redlines
                redline_resolutions = await verify_previous_redlines(parent_clauses, new_clauses, use_llm=use_llm)
                existing = contract.metadata_json or {}
                existing["redline_resolutions"] = redline_resolutions
            except Exception as ve:
                print(f"Error during redline verification: {ve}")
                # Fallback using heuristic verifier manually
                try:
                    from .agents import _heuristic_verify_redline
                    resolutions = []
                    parent_risks = [c for c in parent_clauses if (c.risk_level.value if hasattr(c.risk_level, "value") else str(c.risk_level)) in {"HIGH", "CRITICAL"}]

                    nc_dict = {}
                    for nc in new_clauses:
                        nc_type_key = nc.clause_type.lower().strip()
                        if nc_type_key not in nc_dict:
                            nc_dict[nc_type_key] = nc

                    for pc in parent_risks:
                        pc_type_key = pc.clause_type.lower().strip()
                        matched_nc = nc_dict.get(pc_type_key)
                        if not matched_nc and new_clauses:
                            matched_nc = new_clauses[0]

                        new_text = matched_nc.text_content if matched_nc else ""
                        nc_id = str(matched_nc.id) if matched_nc else ""
                        status, new_risk_level, details = _heuristic_verify_redline(pc.text_content, pc.redline_suggestion or "", new_text)
                        resolutions.append({
                            "clause_type": pc.clause_type,
                            "parent_clause_id": str(pc.id),
                            "parent_text": pc.text_content,
                            "parent_risk_level": pc.risk_level.value if hasattr(pc.risk_level, "value") else str(pc.risk_level),
                            "parent_redline_suggestion": pc.redline_suggestion or "",
                            "new_clause_id": nc_id,
                            "new_text": new_text,
                            "new_risk_level": new_risk_level,
                            "status": status,
                            "verification_details": details
                        })
                    existing = contract.metadata_json or {}
                    existing["redline_resolutions"] = resolutions
                except Exception as he:
                    print(f"Deep fallback verification failed: {he}")


async def _extract_and_save_obligations(contract, raw_text: str):
    existing = contract.metadata_json or {}
    try:
        from .agents import extract_contract_obligations
        obligation_result = await extract_contract_obligations(raw_text)
        obligations_data = [
            {
                "title": o.title,
                "description": o.description,
                "party_responsible": o.party_responsible,
                "due_trigger": o.due_trigger,
                "category": o.category,
            }
            for o in obligation_result.obligations
        ]
        existing["obligations"] = obligations_data
    except Exception as oe:
        print(f"Obligation extraction failed (non-fatal): {oe}")
        existing["obligations"] = []


def _parse_iso_date(value):
    """Best-effort ISO date parse — swallows malformed LLM output."""
    if not value:
        return None
    try:
        from datetime import date as _date
        return _date.fromisoformat(str(value).strip()[:10])
    except Exception:
        return None


# Fields counted toward the repository completeness score (pain point:
# "incomplete contract repository" — makes gaps visible and measurable).
COMPLETENESS_FIELDS = [
    "counterparty", "contract_type", "effective_date", "expiry_date",
    "auto_renewal", "renewal_notice_days", "total_value", "currency",
    "payment_terms", "governing_law",
]


async def _extract_and_save_metadata(contract, raw_text: str):
    """LLM metadata extraction → promoted columns + dual-write into metadata_json.

    Mirrors _extract_and_save_obligations: non-fatal on any failure.
    """
    # COPY the loaded JSONB — mutating it in place also mutates SQLAlchemy's
    # committed-state snapshot, and the flush diff then sees "no change".
    existing = dict(contract.metadata_json or {})
    try:
        from .agents import extract_contract_metadata_llm, CONTRACT_TYPES
        m = await extract_contract_metadata_llm(raw_text)

        contract.company = m.party_a or contract.company
        contract.counterparty = m.party_b or contract.counterparty
        if m.contract_type:
            contract.contract_type = m.contract_type if m.contract_type in CONTRACT_TYPES else "OTHER"
        contract.effective_date = _parse_iso_date(m.effective_date) or contract.effective_date
        contract.expiry_date = _parse_iso_date(m.expiry_date) or contract.expiry_date
        if m.auto_renewal is not None:
            contract.auto_renewal = m.auto_renewal
        if m.renewal_notice_days is not None:
            contract.renewal_notice_days = m.renewal_notice_days
        if m.total_value is not None:
            contract.total_value = m.total_value
        if m.currency:
            contract.currency = (m.currency or "").strip()[:3].upper() or None
        contract.payment_terms = m.payment_terms or contract.payment_terms
        contract.governing_law = m.governing_law or contract.governing_law
        contract.business_unit = contract.business_unit or m.business_unit_hint

        present = sum(1 for f in COMPLETENESS_FIELDS if getattr(contract, f) is not None)
        contract.completeness_score = round(present / len(COMPLETENESS_FIELDS), 2)

        # Dual-write so existing JSONB readers (dashboard/calendar/vendors) keep working.
        updates = {
            "company": contract.company,
            "counterparty": contract.counterparty,
            "contract_type": contract.contract_type,
            "contract_date": contract.effective_date.isoformat() if contract.effective_date else existing.get("contract_date"),
            "expiry_date": contract.expiry_date.isoformat() if contract.expiry_date else existing.get("expiry_date"),
            "auto_renewal": contract.auto_renewal,
            "renewal_notice_days": contract.renewal_notice_days,
            "total_value": float(contract.total_value) if contract.total_value is not None else None,
            "currency": contract.currency,
            "payment_terms": contract.payment_terms,
            "governing_law": contract.governing_law,
            "term_description": m.term_description,
            "term_months": m.term_months,
            "rfq_reference": m.rfq_reference,
            "metadata_confidence": m.confidence,
            "completeness_score": contract.completeness_score,
        }
        existing.update({k: v for k, v in updates.items() if v is not None})
        def _split3(entry: str) -> tuple[str, str, str]:
            parts = [p.strip() for p in str(entry).split("::")]
            a = parts[0] if len(parts) > 0 else ""
            b = parts[1] if len(parts) > 1 else ""
            c = parts[2] if len(parts) > 2 else ""
            return a, b, c

        def _parse_attrs(entries):
            out = []
            for entry in (entries or [])[:10]:
                key, value, category = _split3(entry)
                if key and value:
                    out.append({"key": key, "value": value,
                                "category": (category or "general").lower()})
            return out

        VALID_REL = {"AMENDS", "AMENDED_BY", "ORDER_UNDER", "MASTER_OF", "RENEWS", "INCORPORATES", "RELATED"}

        def _parse_refs(entries):
            out = []
            for entry in (entries or [])[:8]:
                rel, title, doc_type = _split3(entry)
                rel = rel.upper().replace(" ", "_")
                if title:
                    out.append({"title": title,
                                "relationship": rel if rel in VALID_REL else "RELATED",
                                "doc_type": doc_type or None,
                                "date_mentioned": None})
            return out

        attrs = _parse_attrs(m.dynamic_attributes)
        refs = _parse_refs(m.references_other_documents)

        # Focused second pass — the wide scalar extraction reliably starves or
        # mangles these two lists; a dedicated small agent fills them in.
        if not attrs and not refs:
            try:
                from .agents import extract_metadata_enrichment
                enrich = await extract_metadata_enrichment(raw_text)
                attrs = attrs or _parse_attrs(enrich.dynamic_attributes)
                refs = refs or _parse_refs(enrich.references_other_documents)
            except Exception as ee:
                print(f"Metadata enrichment pass failed (non-fatal): {ee}")

        if attrs:
            existing["dynamic_attributes"] = attrs
        if refs:
            existing["document_references"] = refs
        # RFQ-bypass heuristic: new vendor to the org AND no sourcing reference.
        try:
            from sqlalchemy.orm import object_session
            dbs = object_session(contract)
            vendor = (contract.counterparty or "").strip()
            if dbs is not None and vendor and not (m.rfq_reference or "").strip():
                prior = (
                    dbs.query(Contract)
                    .filter(Contract.id != contract.id,
                            Contract.counterparty.ilike(f"%{vendor.split(',')[0][:40]}%"))
                    .first()
                )
                existing["rfq_bypass_suspect"] = prior is None
            elif (m.rfq_reference or "").strip():
                existing["rfq_bypass_suspect"] = False
        except Exception:
            pass

        contract.metadata_json = dict(existing)
    except Exception as me:
        print(f"Metadata extraction failed (non-fatal): {me}")


async def analyze_contract_background(contract_id: str, raw_text: str):
    print(f"Background task started for contract: {contract_id}")
    
    try:
        from .models import Contract, ContractStatus
        from .database import SessionLocal
        # Define callback to update detailed step
        async def update_status(step_msg: str):
            db_tmp = SessionLocal()
            try:
                c = db_tmp.query(Contract).filter(Contract.id == contract_id).first()
                if c:
                    existing = c.metadata_json or {}
                    c.metadata_json = {**existing, "processing_step": step_msg}
                    log_contract_event(db_tmp, contract_id, "status", step_msg)
                    db_tmp.commit()
            finally:
                db_tmp.close()

        # 1. Run the Pydantic AI graph.
        analysis_results, analysis_meta = await _run_analysis_pipeline(contract_id, raw_text, update_status)
        
        # 2. Save to DB (Postgres/pgvector)
        db = SessionLocal()
        try:
            save_analysis_results(db, contract_id, analysis_results)
            # Mark which analysis mode was used.
            contract = db.query(Contract).filter(Contract.id == contract_id).first()
            if contract:
                # Story: Generate embeddings for semantic search
                await _generate_clause_embeddings(db, contract, contract_id)
                
                # Check for parent and run redline verification
                await _verify_contract_redlines(db, contract, contract_id, analysis_meta)
                
                # Run obligation extraction
                await _extract_and_save_obligations(contract, raw_text)

                # Structured + dynamic metadata extraction (promoted columns)
                await _extract_and_save_metadata(contract, raw_text)

                existing = contract.metadata_json or {}
                contract.metadata_json = {**existing, **analysis_meta}
                log_contract_event(db, contract_id, "analysis", "Analysis completed", analysis_meta)
                db.commit()
            print(f"Background task complete. Saved {len(analysis_results)} clauses.")
        finally:
            db.close()
            
    except Exception as e:
        print(f"Error in background task: {e}")
        from .models import Contract, ContractStatus
        from .database import SessionLocal
        db = SessionLocal()
        try:
            contract = db.query(Contract).filter(Contract.id == contract_id).first()
            if contract:
                # asyncio.TimeoutError stringifies to "" so make it explicit.
                try:
                    import asyncio
                    is_timeout = isinstance(e, asyncio.TimeoutError)
                except Exception:
                    is_timeout = False

                timeout_s = os.getenv("CONTRACT_ANALYSIS_TIMEOUT_S", "60")
                contract.status = ContractStatus.FAILED
                existing = contract.metadata_json or {}
                contract.metadata_json = {
                    **existing,
                    "processing_step": f"Failed: {type(e).__name__}",
                    "error": (f"Timed out after {timeout_s}s" if is_timeout else (str(e) or type(e).__name__)),
                }
                log_contract_event(db, contract_id, "error", "Analysis failed", {"error": contract.metadata_json.get("error")})
                db.commit()
        finally:
            db.close()


@app.get("/api/v1/contracts/{contract_id}/events")
async def get_contract_events(
    contract_id: str,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user)
):
    # Verify contract ownership
    contract = get_accessible_contract(db, contract_id, current_user)
    if not contract:
        raise HTTPException(status_code=404, detail="Contract not found")
        
    events = (
        db.query(ContractEvent)
        .filter(ContractEvent.contract_id == contract_id)
        .order_by(ContractEvent.created_at.desc())
        .limit(200)
        .all()
    )
    return {
        "events": [
            {
                "id": str(e.id),
                "contract_id": str(e.contract_id),
                "event_type": e.event_type,
                "message": e.message,
                "payload_json": e.payload_json,
                "created_at": e.created_at.isoformat(),
            }
            for e in events
        ]
    }


@app.get("/api/v1/events/recent")
async def get_recent_events(
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user)
):
    # Join with Contract to retrieve only events generated by contracts owned by current_user
    events = (
        db.query(ContractEvent)
        .join(Contract, ContractEvent.contract_id == Contract.id)
        .filter(bu_scope_criterion(current_user))
        .order_by(ContractEvent.created_at.desc())
        .limit(200)
        .all()
    )
    return {
        "events": [
            {
                "id": str(e.id),
                "contract_id": str(e.contract_id),
                "event_type": e.event_type,
                "message": e.message,
                "payload_json": e.payload_json,
                "created_at": e.created_at.isoformat(),
            }
            for e in events
        ]
    }

@app.post("/api/v1/contracts/upload")
async def upload_contract(
    background_tasks: BackgroundTasks, 
    file: UploadFile = File(...),
    parent_id: str | None = None,
    business_unit_id: str | None = None,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user)
):
    # 1. Read bytes
    file_bytes = await file.read()
    
    # 2. Extract Text and Hash
    file_hash, raw_text = await extract_text_from_pdf(file_bytes)
    meta = extract_contract_metadata(raw_text)
    from datetime import datetime
    upload_date = datetime.utcnow().date().isoformat()
    std_name = standardized_filename(meta, upload_date)
    
    # Check if exists (specifically owned by current_user)
    existing_contract = None
    if not parent_id:
        existing_contract = db.query(Contract).filter(Contract.file_hash == file_hash, Contract.user_id == current_user.id).first()
        
    if existing_contract:
        # Always refresh filename/metadata on upload (user requested regeneration every time).
        existing = existing_contract.metadata_json or {}
        existing_contract.filename = std_name
        existing_contract.metadata_json = {**existing, "raw_text": raw_text, **meta}
        if existing_contract.status == ContractStatus.FAILED:
            # Retry processing for failed contracts
            existing_contract.status = ContractStatus.PROCESSING
            log_contract_event(db, str(existing_contract.id), "ingest", "PDF re-uploaded; retrying failed analysis", {"cache": "hit", "mode": "pdf"})
            db.commit()
            background_tasks.add_task(analyze_contract_background, str(existing_contract.id), raw_text)
            return {
                "filename": existing_contract.filename,
                "contract_id": str(existing_contract.id),
                "status": "PROCESSING",
                "message": "Retrying failed contract analysis."
            }
        else:
            log_contract_event(db, str(existing_contract.id), "ingest", "PDF re-uploaded; using cached result", {"cache": "hit", "mode": "pdf", "status": existing_contract.status.value})
            db.commit()
            return {
                "filename": existing_contract.filename,
                "contract_id": str(existing_contract.id),
                "status": existing_contract.status.value if existing_contract.status else "COMPLETED",
                "message": ("Contract already processing." if existing_contract.status == ContractStatus.PROCESSING else "Contract already processed."),
            }
    
    # 3. Create Contract Record
    parent_contract = None
    version_number = 1
    if parent_id:
        parent_contract = db.query(Contract).filter(Contract.id == parent_id, Contract.user_id == current_user.id).first()
        if parent_contract:
            parent_version = parent_contract.metadata_json.get("version_number", 1) if parent_contract.metadata_json else 1
            version_number = parent_version + 1
            
    metadata = {"raw_text": raw_text, **meta}
    if parent_id and parent_contract:
        metadata["parent_contract_id"] = parent_id
        metadata["version_number"] = version_number
    else:
        metadata["version_number"] = 1
        
    bu_id, bu_name = _resolve_business_unit(db, current_user, business_unit_id)
    new_contract = Contract(
        filename=std_name,
        file_hash=file_hash,
        status=ContractStatus.PROCESSING,
        metadata_json=metadata,
        user_id=current_user.id,
        business_unit_id=bu_id,
        business_unit=bu_name
    )
    db.add(new_contract)
    db.flush()
    log_contract_event(db, str(new_contract.id), "ingest", "PDF uploaded for analysis", {"cache": "miss", "mode": "pdf"})
    db.commit()
    db.refresh(new_contract)
    
    # 4. Dispatch to background worker
    background_tasks.add_task(analyze_contract_background, str(new_contract.id), raw_text)
    
    return {
        "filename": new_contract.filename, 
        "contract_id": str(new_contract.id),
        "status": new_contract.status.value if hasattr(new_contract.status, "value") else str(new_contract.status),
        "message": "Analysis running in background."
    }

@app.post("/api/v1/contracts/{contract_id}/reprocess")
async def reprocess_contract(
    contract_id: str,
    background_tasks: BackgroundTasks,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user)
):
    contract = get_accessible_contract(db, contract_id, current_user)
    if not contract:
        raise HTTPException(status_code=404, detail="Contract not found")
        
    raw_text = contract.metadata_json.get("raw_text")
    if not raw_text:
        raise HTTPException(status_code=400, detail="Raw text not available. Please re-upload.")
        
    # Wipe existing clauses
    db.query(ContractClause).filter(ContractClause.contract_id == contract_id).delete()
    
    from datetime import datetime
    meta = extract_contract_metadata(raw_text)
    upload_date = datetime.utcnow().date().isoformat()
    contract.filename = standardized_filename(meta, upload_date)

    contract.status = ContractStatus.PROCESSING
    # Keep raw_text + extracted naming metadata, clear analysis outputs.
    contract.metadata_json = {"raw_text": raw_text, **meta}
    log_contract_event(db, str(contract.id), "ingest", "Reprocess requested", {"mode": "reprocess"})
    db.commit()
    
    background_tasks.add_task(analyze_contract_background, str(contract.id), raw_text)
    
    return {"message": "Reprocessing started", "status": "PROCESSING"}

@app.delete("/api/v1/contracts/{contract_id}")
async def delete_contract(
    contract_id: str,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user)
):
    contract = get_accessible_contract(db, contract_id, current_user)
    if not contract:
        raise HTTPException(status_code=404, detail="Contract not found")
        
    db.delete(contract)
    db.commit()
    return {"message": "Contract deleted successfully"}

@app.get("/api/v1/contracts/{contract_id}/status")
async def contract_status(
    contract_id: str,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user)
):
    """
    Story 008: lightweight polling endpoint for job status + best-effort ETA.
    (Also useful for a future CLI status command.)
    """
    contract = get_accessible_contract(db, contract_id, current_user)
    if not contract:
        raise HTTPException(status_code=404, detail="Contract not found")

    step = (contract.metadata_json or {}).get("processing_step") or ""

    def stage_from_step(s: str) -> dict:
        t = (s or "").lower()
        # 3-stage pipeline: Planning -> Thinking -> Executing
        if "segment" in t or "extract" in t:
            return {"key": "planning", "label": "Planning", "index": 1, "count": 3}
        if "analyz" in t or "risk" in t:
            return {"key": "thinking", "label": "Thinking", "index": 2, "count": 3}
        if "sav" in t:
            return {"key": "executing", "label": "Executing", "index": 3, "count": 3}
        return {"key": "processing", "label": "Processing", "index": 0, "count": 3}

    stage = stage_from_step(step)

    progress = None
    m = re.search(r"Clause\s+(\d+)\s+of\s+(\d+)", step, flags=re.I)
    if m:
        try:
            cur = int(m.group(1))
            total = int(m.group(2))
            if total > 0:
                progress = {"current": cur, "total": total, "ratio": max(0.0, min(1.0, cur / total))}
        except Exception:
            progress = None

    eta_seconds = None
    if contract.status == ContractStatus.PROCESSING:
        # Best-effort ETA: if we're in per-clause analysis, estimate from observed per-clause rate.
        if progress and progress.get("total") and progress.get("current"):
            cur = int(progress["current"])
            total = int(progress["total"])
            # Find recent analyzing events with clause counters.
            events = (
                db.query(ContractEvent)
                .filter(ContractEvent.contract_id == contract.id, ContractEvent.event_type == "status")
                .order_by(ContractEvent.created_at.asc())
                .limit(500)
                .all()
            )
            points = []
            for e in events:
                mm = re.search(r"Clause\s+(\d+)\s+of\s+(\d+)", e.message or "", flags=re.I)
                if not mm:
                    continue
                try:
                    i = int(mm.group(1))
                    n = int(mm.group(2))
                except Exception:
                    continue
                if n != total:
                    continue
                if i <= 0 or i > total:
                    continue
                points.append((e.created_at, i))

            if len(points) >= 2:
                t0, i0 = points[0]
                t1, i1 = points[-1]
                dt = max(0.001, (t1 - t0).total_seconds())
                di = max(1, i1 - i0)
                rate = di / dt  # clauses/sec
                remaining = max(0, total - cur)
                eta_seconds = int(round(remaining / max(0.01, rate)))
            else:
                # Default to a conservative 2s/clause if we have no timing history yet.
                remaining = max(0, total - cur)
                eta_seconds = int(remaining * 2)
        else:
            # Coarse ETA when we don't have a clause counter.
            if stage["key"] == "planning":
                eta_seconds = 10
            elif stage["key"] == "executing":
                eta_seconds = 3
            else:
                eta_seconds = 20

    return {
        "contract_id": str(contract.id),
        "status": contract.status.value if hasattr(contract.status, "value") else str(contract.status),
        "stage": stage,
        "processing_step": step or None,
        "progress": progress,
        "eta_seconds": eta_seconds,
        "created_at": contract.created_at.isoformat() if contract.created_at else None,
        "updated_at": contract.updated_at.isoformat() if contract.updated_at else None,
    }

@app.get("/api/v1/contracts/{contract_id}/clauses")
async def get_contract_clauses(
    contract_id: str,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user)
):
    contract = get_accessible_contract(db, contract_id, current_user)
    if not contract:
        raise HTTPException(status_code=404, detail="Contract not found")
        
    clauses = db.query(ContractClause).filter(ContractClause.contract_id == contract_id).all()
    
    return {
        "contract": {
            "id": str(contract.id),
            "filename": contract.filename,
            "status": contract.status,
            "metadata": contract.metadata_json
        },
        "clauses": [
            {
                "id": str(c.id),
                "clause_type": c.clause_type,
                "text_content": c.text_content,
                "risk_level": c.risk_level.value if c.risk_level else "LOW",
                "risk_reasoning": (
                    _first_sentence(c.risk_reasoning)
                    if (c.risk_level and c.risk_level.value in {"HIGH", "CRITICAL"} and (c.risk_reasoning or "").strip())
                    else c.risk_reasoning
                ),
                "redline_suggestion": (
                    c.redline_suggestion
                    if (c.redline_suggestion or "").strip()
                    else (
                        _heuristic_redline(
                            c.clause_type,
                            c.text_content,
                            c.risk_level.value if c.risk_level else "LOW",
                        )
                        if (c.risk_level and c.risk_level.value in {"HIGH", "CRITICAL"})
                        else None
                    )
                ),
                # Backfill technical details for older clauses that predate Story 007 (no LLM call).
                "risk_debug_json": (
                    (c.risk_debug_json or {})
                    if (c.risk_debug_json or {})
                    else (_heuristic_risk(c.clause_type, c.text_content).debug_json or {})
                ),
            } for c in clauses
        ]
    }

@app.get("/api/v1/contracts")
async def list_contracts(
    q: str | None = None,
    contract_type: str | None = None,
    status: str | None = None,
    expiry_before: str | None = None,
    expiry_after: str | None = None,
    auto_renewal: bool | None = None,
    min_value: float | None = None,
    max_value: float | None = None,
    business_unit: str | None = None,
    max_completeness: float | None = None,
    sort: str = "created_at",
    order: str = "desc",
    limit: int = 100,
    offset: int = 0,
    scope: str = "bu",
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user)
):
    """Repository list with deterministic filters + pagination (no LLM).

    scope: mine (own uploads) | bu (default: my business unit) | org
    (whole organization — foreign-BU rows are discovery-projected).
    """
    from .search import SearchFilters, build_query

    filters = SearchFilters(
        text_terms=q,
        contract_types=[contract_type] if contract_type else [],
        status=status,
        expiry_before=expiry_before or None,
        expiry_after=expiry_after or None,
        auto_renewal=auto_renewal,
        min_value=min_value,
        max_value=max_value,
        business_units=[business_unit] if business_unit else [],
        max_completeness=max_completeness,
        sort_by=sort,
        sort_dir=order,
        limit=min(max(int(limit or 100), 1), 200),
    )
    from .models import BusinessUnit
    if scope == "org" and current_user.org_id:
        base = (db.query(Contract)
                .join(BusinessUnit, Contract.business_unit_id == BusinessUnit.id)
                .filter(BusinessUnit.org_id == current_user.org_id))
    elif scope == "mine":
        base = db.query(Contract).filter(Contract.user_id == current_user.id)
    else:
        base = db.query(Contract).filter(bu_scope_criterion(current_user))
    query = build_query(base, filters)
    total = query.count()
    contracts = query.offset(max(int(offset or 0), 0)).limit(filters.limit).all()

    result = []
    for c in contracts:
        if not can_read_full(current_user, c):
            # Discovery-level projection: existence + key terms, no content/risk.
            result.append({
                "id": str(c.id),
                "filename": c.filename,
                "status": c.status.value,
                "metadata_json": {},
                "company": c.company,
                "counterparty": c.counterparty,
                "contract_type": c.contract_type,
                "effective_date": c.effective_date.isoformat() if c.effective_date else None,
                "expiry_date": c.expiry_date.isoformat() if c.expiry_date else None,
                "business_unit": c.business_unit,
                "discovery_only": True,
                "overall_risk": None,
                "created_at": c.created_at.isoformat(),
            })
            continue
        # Determine overall risk score from metadata
        overall_risk = "LOW"
        meta = c.metadata_json or {}
        risk_counts = meta.get("risk_counts", {})
        if risk_counts.get("CRITICAL", 0) > 0:
            overall_risk = "CRITICAL"
        elif risk_counts.get("HIGH", 0) > 0:
            overall_risk = "HIGH"
        elif risk_counts.get("MEDIUM", 0) > 0:
            overall_risk = "MEDIUM"

        result.append({
            "id": str(c.id),
            "filename": c.filename,
            "status": c.status.value,
            # raw_text is heavy and never read by list consumers — detail endpoint serves it
            "metadata_json": {k: v for k, v in meta.items() if k != "raw_text"},
            "company": c.company,
            "counterparty": c.counterparty,
            "contract_type": c.contract_type,
            "effective_date": c.effective_date.isoformat() if c.effective_date else None,
            "expiry_date": c.expiry_date.isoformat() if c.expiry_date else None,
            "auto_renewal": c.auto_renewal,
            "total_value": float(c.total_value) if c.total_value is not None else None,
            "currency": c.currency,
            "business_unit": c.business_unit,
            "completeness_score": c.completeness_score,
            "overall_risk": overall_risk if c.status == ContractStatus.COMPLETED else None,
            "created_at": c.created_at.isoformat()
        })

    return {"contracts": result, "total": total}


class SearchQueryIn(BaseModel):
    question: str
    limit: int | None = 25


@app.post("/api/v1/search/query")
async def search_query(
    payload: SearchQueryIn,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """Natural-language repository search — Gemini parses the question into
    validated structured filters; deterministic ILIKE fallback on any failure."""
    from .search import (SearchFilters, build_query, apply_risk_floor,
                         contract_row, parse_question_to_filters, fallback_filters)

    question = (payload.question or "").strip()
    if not question:
        raise HTTPException(status_code=400, detail="question is required")

    known_bus = [r[0] for r in db.query(Contract.business_unit)
                 .filter(bu_scope_criterion(current_user), Contract.business_unit.isnot(None))
                 .distinct().all()]

    route = "metadata"
    try:
        filters = await parse_question_to_filters(question, known_bus)
    except Exception as e:
        print(f"NL search parse failed, using keyword fallback: {e}")
        filters = fallback_filters(question)
        route = "fallback_ilike"
    if payload.limit:
        filters.limit = max(1, min(int(payload.limit), 100))

    base = db.query(Contract).filter(bu_scope_criterion(current_user))
    rows = build_query(base, filters).limit(filters.limit).all()
    rows = apply_risk_floor(rows, filters.risk_at_least)

    return {
        "route": route,
        "filters_applied": filters.applied(),
        "explanation": filters.describe(),
        "results": [contract_row(c) for c in rows],
        "total": len(rows),
    }


@app.get("/api/v1/saved-searches")
async def list_saved_searches(
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    from .models import SavedSearch
    rows = (db.query(SavedSearch).filter(SavedSearch.user_id == current_user.id)
            .order_by(SavedSearch.created_at.desc()).limit(30).all())
    return {"saved_searches": [
        {"id": str(s.id), "name": s.name, "question": s.question,
         "filters": s.filters_json or {}, "created_at": s.created_at.isoformat()}
        for s in rows
    ]}


class SavedSearchIn(BaseModel):
    name: str
    question: str | None = None
    filters: dict = {}


@app.post("/api/v1/saved-searches")
async def create_saved_search(
    payload: SavedSearchIn,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    from .models import SavedSearch
    name = (payload.name or "").strip()
    if not name:
        raise HTTPException(status_code=400, detail="name is required")
    s = SavedSearch(user_id=current_user.id, name=name[:80],
                    question=(payload.question or "").strip() or None,
                    filters_json=payload.filters or {})
    db.add(s)
    db.commit()
    db.refresh(s)
    return {"id": str(s.id), "name": s.name}


@app.delete("/api/v1/saved-searches/{search_id}")
async def delete_saved_search(
    search_id: str,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    from .models import SavedSearch
    s = db.query(SavedSearch).filter(SavedSearch.id == search_id,
                                     SavedSearch.user_id == current_user.id).first()
    if not s:
        raise HTTPException(status_code=404, detail="Saved search not found")
    db.delete(s)
    db.commit()
    return {"message": "Deleted"}


@app.get("/api/v1/risks")
async def list_all_risks(
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user)
):
    """
    Fetch all high and critical severity risk clauses across all completed contracts owned by the current user.
    """
    contracts = (
        db.query(Contract)
        .filter(bu_scope_criterion(current_user), Contract.status == ContractStatus.COMPLETED)
        .all()
    )
    contract_ids = [c.id for c in contracts]
    contract_map = {str(c.id): c for c in contracts}
    
    if not contract_ids:
        return {"risks": []}
        
    # Query clauses with HIGH or CRITICAL risk
    clauses = (
        db.query(ContractClause)
        .filter(
            ContractClause.contract_id.in_(contract_ids),
            ContractClause.risk_level.in_([RiskLevel.HIGH, RiskLevel.CRITICAL])
        )
        .all()
    )
    
    # Sort clauses: CRITICAL first, then HIGH
    severity_order = {"CRITICAL": 0, "HIGH": 1}
    clauses = sorted(
        clauses,
        key=lambda c: severity_order.get(c.risk_level.value if c.risk_level else "HIGH", 9)
    )
    
    result = []
    for c in clauses:
        contract_obj = contract_map.get(str(c.contract_id))
        if not contract_obj:
            continue
            
        # Resolve redline suggestion
        redline = c.redline_suggestion
        if not redline or not redline.strip():
            redline = _heuristic_redline(c.clause_type, c.text_content, c.risk_level.value if c.risk_level else "LOW")
            
        result.append({
            "id": str(c.id),
            "contract_id": str(c.contract_id),
            "contract_filename": contract_obj.filename,
            "clause_type": c.clause_type,
            "text_content": c.text_content,
            "risk_level": c.risk_level.value if c.risk_level else "LOW",
            "risk_reasoning": c.risk_reasoning,
            "redline_suggestion": redline,
            "created_at": contract_obj.created_at.isoformat()
        })
        
    return {"risks": result}


@app.get("/api/v1/contracts/{contract_id}")
async def get_contract(
    contract_id: str,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user)
):
    contract = get_accessible_contract(db, contract_id, current_user)
    if not contract:
        raise HTTPException(status_code=404, detail="Contract not found")
        
    overall_risk = "LOW"
    risk_counts = (contract.metadata_json or {}).get("risk_counts", {})
    if risk_counts.get("CRITICAL", 0) > 0:
        overall_risk = "CRITICAL"
    elif risk_counts.get("HIGH", 0) > 0:
        overall_risk = "HIGH"
    elif risk_counts.get("MEDIUM", 0) > 0:
        overall_risk = "MEDIUM"

    return {
        "contract": {
            "id": str(contract.id),
            "filename": contract.filename,
            "status": contract.status.value,
            "metadata_json": contract.metadata_json,
            "overall_risk": overall_risk if contract.status == ContractStatus.COMPLETED else None,
            "created_at": contract.created_at.isoformat()
        }
    }


# ---------------------------------------------------------------------------
# Story 011 / 012: Structured report endpoint (used by CLI analyze + report)
# ---------------------------------------------------------------------------

def _compute_overall_risk(risk_counts: dict) -> str:
    if risk_counts.get("CRITICAL", 0) > 0:
        return "CRITICAL"
    if risk_counts.get("HIGH", 0) > 0:
        return "HIGH"
    if risk_counts.get("MEDIUM", 0) > 0:
        return "MEDIUM"
    return "LOW"


def _one_line_summary(overall_risk: str, risk_counts: dict, top_risks: list) -> str:
    """Story 011: generate a one-line routing summary."""
    total_flagged = risk_counts.get("CRITICAL", 0) + risk_counts.get("HIGH", 0)
    if total_flagged == 0:
        return "No high-risk clauses detected; routine review recommended before signing."
    clause_types = [r.get("clause_type", "") for r in top_risks if r.get("risk_level") in {"HIGH", "CRITICAL"}]
    types_str = ", ".join(clause_types[:3]) if clause_types else "flagged clauses"
    return (
        f"This contract contains {total_flagged} high-risk clause{'s' if total_flagged != 1 else ''} "
        f"({types_str}) requiring legal review before signing."
    )


@app.get("/api/v1/contracts/{contract_id}/report")
async def get_contract_report(
    contract_id: str,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user)
):
    """
    Structured report for CLI display (Story 011) and PDF export (Story 012).
    Returns overall risk, one-line summary, and flagged clauses (HIGH + CRITICAL).
    """
    contract = get_accessible_contract(db, contract_id, current_user)
    if not contract:
        raise HTTPException(status_code=404, detail="Contract not found")
    if contract.status != ContractStatus.COMPLETED:
        raise HTTPException(status_code=409, detail=f"Contract analysis is not complete (status: {contract.status.value})")

    meta = contract.metadata_json or {}
    risk_counts = meta.get("risk_counts", {})
    top_risks = meta.get("top_risks", [])
    overall_risk = _compute_overall_risk(risk_counts)
    summary = _one_line_summary(overall_risk, risk_counts, top_risks)

    clauses = (
        db.query(ContractClause)
        .filter(ContractClause.contract_id == contract_id)
        .all()
    )

    severity_order = {"CRITICAL": 0, "HIGH": 1, "MEDIUM": 2, "LOW": 3}
    flagged = sorted(
        [c for c in clauses if c.risk_level and c.risk_level.value in {"HIGH", "CRITICAL"}],
        key=lambda c: severity_order.get(c.risk_level.value, 9),
    )

    def _resolve_redline(c: ContractClause) -> str | None:
        if (c.redline_suggestion or "").strip():
            return c.redline_suggestion
        if c.risk_level and c.risk_level.value in {"HIGH", "CRITICAL"}:
            return _heuristic_redline(c.clause_type, c.text_content, c.risk_level.value)
        return None

    return {
        "contract_id": contract_id,
        "filename": contract.filename,
        "overall_risk": overall_risk,
        "risk_counts": risk_counts,
        "summary": summary,
        "flagged_clauses": [
            {
                "id": str(c.id),
                "clause_type": c.clause_type,
                "risk_level": c.risk_level.value,
                "risk_reasoning": (
                    _first_sentence(c.risk_reasoning)
                    if (c.risk_reasoning or "").strip()
                    else None
                ),
                "redline_suggestion": _resolve_redline(c),
                "text_excerpt": (c.text_content or "")[:800],
            }
            for c in flagged
        ],
    }


# ---------------------------------------------------------------------------
# Stories 013 & 014: Feedback endpoint
# ---------------------------------------------------------------------------

class FeedbackIn(BaseModel):
    is_risky: bool
    note: str | None = None


@app.post("/api/v1/feedback/{contract_id}/{clause_id}")
async def submit_feedback(
    contract_id: str,
    clause_id: str,
    payload: FeedbackIn,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user)
):
    """
    Record a user correction without re-scoring. Stories 013 & 014.
    Returns a one-line confirmation and the feedback ID.
    """
    contract = get_accessible_contract(db, contract_id, current_user)
    if not contract:
        raise HTTPException(status_code=404, detail="Contract not found")

    clause = db.query(ContractClause).filter(
        ContractClause.id == clause_id,
        ContractClause.contract_id == contract_id,
    ).first()
    if not clause:
        raise HTTPException(status_code=404, detail="Clause not found")

    feedback = ClauseFeedback(
        contract_id=contract_id,
        clause_id=clause_id,
        is_risky=payload.is_risky,
        note=payload.note,
    )
    db.add(feedback)
    log_contract_event(
        db,
        contract_id,
        "feedback",
        f"Feedback recorded: clause {clause_id} marked {'risky' if payload.is_risky else 'not-risky'}",
        {"clause_id": clause_id, "is_risky": payload.is_risky, "note": payload.note},
    )
    db.commit()
    db.refresh(feedback)

    label = "risky" if payload.is_risky else "not-risky"
    return {
        "feedback_id": str(feedback.id),
        "message": f"Feedback recorded: clause marked {label}.",
    }


# ---------------------------------------------------------------------------
# User Stories 011: Auth Schemas and Endpoints
# ---------------------------------------------------------------------------

class UserSignupIn(BaseModel):
    email: str
    password: str

class UserLoginIn(BaseModel):
    email: str
    password: str

@app.get("/api/v1/auth/signup-status")
async def signup_status():
    return {"signup_disabled": is_signup_disabled()}

@app.post("/api/v1/auth/signup")
async def signup(payload: UserSignupIn, db: Session = Depends(get_db)):
    if is_signup_disabled():
        raise HTTPException(status_code=403, detail="User registration is currently disabled.")
    
    # Normalise email: strip and lower
    email_clean = (payload.email or "").strip().lower()
    if not email_clean or "@" not in email_clean:
        raise HTTPException(status_code=400, detail="Invalid email address.")
    
    password = payload.password or ""
    if len(password) < 8:
        raise HTTPException(status_code=400, detail="Password must be at least 8 characters long.")
    if not re.search(r'[A-Z]', password):
        raise HTTPException(status_code=400, detail="Password must contain at least one uppercase letter.")
    if not re.search(r'[a-z]', password):
        raise HTTPException(status_code=400, detail="Password must contain at least one lowercase letter.")
    if not re.search(r'[0-9]', password):
        raise HTTPException(status_code=400, detail="Password must contain at least one number.")
    if not re.search(r'[^a-zA-Z0-9\s]', password):
        raise HTTPException(status_code=400, detail="Password must contain at least one special character.")

    existing = db.query(User).filter(User.email == email_clean).first()
    if existing:
        raise HTTPException(status_code=400, detail="A user with this email already exists.")
    
    hashed = get_password_hash(payload.password)
    user = User(email=email_clean, hashed_password=hashed)
    db.add(user)
    db.commit()
    db.refresh(user)
    
    token = create_access_token(data={"sub": str(user.id)}, expires_delta=timedelta(minutes=ACCESS_TOKEN_EXPIRE_MINUTES))
    return {
        "access_token": token,
        "token_type": "bearer",
        "user": {
            "id": str(user.id),
            "email": user.email
        }
    }

@app.post("/api/v1/auth/login")
async def login(payload: UserLoginIn, db: Session = Depends(get_db)):
    email_clean = (payload.email or "").strip().lower()
    user = db.query(User).filter(User.email == email_clean).first()
    if not user or not verify_password(payload.password, user.hashed_password):
        raise HTTPException(status_code=401, detail="Incorrect email or password.")
    
    token = create_access_token(data={"sub": str(user.id)}, expires_delta=timedelta(minutes=ACCESS_TOKEN_EXPIRE_MINUTES))
    return {
        "access_token": token,
        "token_type": "bearer",
        "user": {
            "id": str(user.id),
            "email": user.email
        }
    }

@app.get("/api/v1/auth/me")
async def get_me(
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    from .models import BusinessUnit, Organization
    bu = db.query(BusinessUnit).filter(BusinessUnit.id == current_user.business_unit_id).first() if current_user.business_unit_id else None
    org = db.query(Organization).filter(Organization.id == current_user.org_id).first() if current_user.org_id else None
    return {
        "id": str(current_user.id),
        "email": current_user.email,
        "role": getattr(current_user, "role", None) or "member",
        "business_unit": bu.name if bu else None,
        "business_unit_id": str(bu.id) if bu else None,
        "organization": org.name if org else None,
    }


# ---------------------------------------------------------------------------
# Chat with Contract — RAG-powered Q&A using clause context
# ---------------------------------------------------------------------------

class ChatMessageIn(BaseModel):
    question: str
    history: list[dict] = []
    session_id: str | None = None


class ChatSourceOut(BaseModel):
    clause_type: str
    text_excerpt: str
    risk_level: str


class ChatResponseOut(BaseModel):
    answer: str
    sources: list[ChatSourceOut]


class ReminderOut(BaseModel):
    id: str
    contract_id: str
    reminder_type: str
    status: str
    due_date: str
    title: str
    body: str | None = None
    letter: dict | None = None


class CreateReminderIn(BaseModel):
    reminder_type: str
    due_date: str
    title: str
    body: str | None = None


class TemplateCreateIn(BaseModel):
    name: str
    description: str | None = None
    raw_text: str


class TemplateCompareIn(BaseModel):
    template_id: str


class VendorEmailDraftIn(BaseModel):
    tone: str | None = "professional"
    include: str | None = "unresolved"  # unresolved | all

# NOTE: the per-contract chat endpoint was removed — Jaggaer Assist
# (POST /api/v1/assistant/chat with `context`) is the single chat surface.


# ---------------------------------------------------------------------------
# Calendar — contract expiry and renewal timeline
# ---------------------------------------------------------------------------

@app.get("/api/v1/calendar")
async def get_calendar(
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user)
):
    """
    Returns all completed contracts with expiry/renewal date information,
    sorted by earliest upcoming date, with urgency classification.
    """
    from datetime import date

    contracts = (
        db.query(Contract)
        .filter(bu_scope_criterion(current_user))
        .order_by(Contract.created_at.desc())
        .all()
    )

    today = date.today()
    items = []

    for c in contracts:
        meta = c.metadata_json or {}
        expiry_date_str = meta.get("expiry_date")
        contract_date_str = meta.get("contract_date")
        renewal_notice_days = meta.get("renewal_notice_days")
        contract_term = meta.get("contract_term")

        # Compute days until expiry
        days_until_expiry = None
        expiry_date_parsed = None
        if expiry_date_str:
            try:
                expiry_date_parsed = date.fromisoformat(expiry_date_str)
                days_until_expiry = (expiry_date_parsed - today).days
            except Exception:
                pass

        # Compute urgency level
        if days_until_expiry is not None:
            if days_until_expiry < 0:
                urgency = "expired"
            elif days_until_expiry <= 30:
                urgency = "critical"
            elif days_until_expiry <= 60:
                urgency = "warning"
            elif days_until_expiry <= 90:
                urgency = "soon"
            else:
                urgency = "safe"
        else:
            urgency = "unknown"

        # Compute renewal deadline
        renewal_deadline_str = None
        if expiry_date_parsed and renewal_notice_days:
            from datetime import timedelta
            renewal_deadline = expiry_date_parsed - timedelta(days=renewal_notice_days)
            renewal_deadline_str = renewal_deadline.isoformat()

        # Derive overall risk
        risk_counts = meta.get("risk_counts", {})
        if risk_counts.get("CRITICAL", 0) > 0:
            overall_risk = "CRITICAL"
        elif risk_counts.get("HIGH", 0) > 0:
            overall_risk = "HIGH"
        elif risk_counts.get("MEDIUM", 0) > 0:
            overall_risk = "MEDIUM"
        else:
            overall_risk = "LOW"

        items.append({
            "id": str(c.id),
            "filename": c.filename,
            "status": c.status.value,
            "contract_type": meta.get("contract_type"),
            "company": meta.get("company"),
            "contract_date": contract_date_str,
            "expiry_date": expiry_date_str,
            "days_until_expiry": days_until_expiry,
            "renewal_notice_days": renewal_notice_days,
            "renewal_deadline": renewal_deadline_str,
            "contract_term": contract_term,
            "urgency": urgency,
            "overall_risk": overall_risk if c.status.value == "COMPLETED" else None,
            "created_at": c.created_at.isoformat(),
        })

    # Sort: expired first (ascending days), then upcoming (ascending), then unknown
    def sort_key(item):
        d = item["days_until_expiry"]
        if d is None:
            return (2, 0)
        if d < 0:
            return (0, d)  # expired, most recent first
        return (1, d)  # upcoming, soonest first

    items.sort(key=sort_key)

    # Fetch upcoming reminders (next 90 days) for bannering in the UI
    try:
        horizon = datetime.now(timezone.utc) + timedelta(days=90)
        reminders = (
            db.query(ContractReminder)
            .filter(ContractReminder.user_id == current_user.id, ContractReminder.status == "OPEN", ContractReminder.due_date <= horizon)
            .order_by(ContractReminder.due_date.asc())
            .limit(50)
            .all()
        )
        reminder_items = [
            {
                "id": str(r.id),
                "contract_id": str(r.contract_id),
                "reminder_type": str(r.reminder_type),
                "due_date": r.due_date.isoformat(),
                "title": r.title,
            }
            for r in reminders
        ]
    except Exception:
        reminder_items = []

    return {"items": items, "reminders": reminder_items}


# ---------------------------------------------------------------------------
# Vendors — group contracts by counterparty
# ---------------------------------------------------------------------------

@app.get("/api/v1/vendors")
async def get_vendors(
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user)
):
    """
    Groups all user contracts by vendor/counterparty name.
    Returns aggregated risk info per vendor.
    """
    contracts = (
        db.query(Contract)
        .filter(bu_scope_criterion(current_user))
        .order_by(Contract.created_at.desc())
        .all()
    )

    vendor_map: dict = {}
    severity = {"LOW": 0, "MEDIUM": 1, "HIGH": 2, "CRITICAL": 3}

    for c in contracts:
        meta = c.metadata_json or {}
        vendor_name = (meta.get("company") or "").strip() or "Unknown Vendor"

        risk_counts = meta.get("risk_counts", {})
        if risk_counts.get("CRITICAL", 0) > 0:
            overall_risk = "CRITICAL"
        elif risk_counts.get("HIGH", 0) > 0:
            overall_risk = "HIGH"
        elif risk_counts.get("MEDIUM", 0) > 0:
            overall_risk = "MEDIUM"
        else:
            overall_risk = "LOW"

        if vendor_name not in vendor_map:
            vendor_map[vendor_name] = {
                "name": vendor_name,
                "contracts": [],
                "worst_risk": "LOW",
                "total_critical": 0,
                "total_high": 0,
            }

        vendor_map[vendor_name]["contracts"].append({
            "id": str(c.id),
            "filename": c.filename,
            "status": c.status.value,
            "overall_risk": overall_risk if c.status.value == "COMPLETED" else None,
            "contract_type": meta.get("contract_type"),
            "expiry_date": meta.get("expiry_date"),
            "created_at": c.created_at.isoformat(),
        })

        vendor_map[vendor_name]["total_critical"] += risk_counts.get("CRITICAL", 0)
        vendor_map[vendor_name]["total_high"] += risk_counts.get("HIGH", 0)

        if severity.get(overall_risk, 0) > severity.get(vendor_map[vendor_name]["worst_risk"], 0):
            if c.status.value == "COMPLETED":
                vendor_map[vendor_name]["worst_risk"] = overall_risk

    # Sort vendors: most risky first, then by contract count
    vendors = list(vendor_map.values())
    vendors.sort(key=lambda v: (-severity.get(v["worst_risk"], 0), -len(v["contracts"])))

    return {"vendors": vendors}


# ---------------------------------------------------------------------------
# Clause repository search (cross-contract semantic search)
# ---------------------------------------------------------------------------

class ClauseSearchIn(BaseModel):
    query: str
    top_k: int = 12


@app.post("/api/v1/clauses/search")
async def clause_repository_search(
    payload: ClauseSearchIn,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    q = (payload.query or "").strip()
    if not q:
        raise HTTPException(status_code=400, detail="query is required")
    top_k = max(1, min(int(payload.top_k or 12), 25))

    import openai
    client = openai.AsyncOpenAI(api_key=os.getenv("OPENAI_API_KEY"))
    try:
        emb = await client.embeddings.create(model=_openai_embedding_model(), dimensions=_embedding_dimensions(), input=q)
        q_embedding = emb.data[0].embedding
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Embedding failed: {str(e)}")

    try:
        rows = (
            db.query(ContractClause, Contract)
            .join(Contract, Contract.id == ContractClause.contract_id)
            .filter(bu_scope_criterion(current_user), ContractClause.embedding.isnot(None))
            .order_by(ContractClause.embedding.cosine_distance(q_embedding))
            .limit(top_k)
            .all()
        )
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Vector search failed: {str(e)}")

    results = []
    analytics = {"by_clause_type": {}, "by_risk_level": {}}
    for clause, contract in rows:
        risk = clause.risk_level.value if clause.risk_level else "LOW"
        ctype = (clause.clause_type or "Unknown").strip()
        analytics["by_clause_type"][ctype] = analytics["by_clause_type"].get(ctype, 0) + 1
        analytics["by_risk_level"][risk] = analytics["by_risk_level"].get(risk, 0) + 1
        results.append(
            {
                "contract_id": str(contract.id),
                "contract_filename": contract.filename,
                "clause_id": str(clause.id),
                "clause_type": clause.clause_type,
                "risk_level": risk,
                "text_excerpt": (clause.text_content or "")[:420],
            }
        )

    return {"results": results, "analytics": analytics}


# ---------------------------------------------------------------------------
# Global assistant chat (bottom-left assistant)
# ---------------------------------------------------------------------------

class AssistantContextIn(BaseModel):
    contract_id: str | None = None
    contract_name: str | None = None


class AssistantChatIn(BaseModel):
    question: str
    history: list[dict] = []          # legacy fallback; server history wins when a conversation exists
    session_id: str | None = None
    conversation_id: str | None = None
    context: AssistantContextIn | None = None


def _load_or_create_conversation(db, current_user, payload: AssistantChatIn, question: str):
    """Server-side conversation record (governed chat). Returns (convo, history_messages)."""
    from .models import AssistConversation, AssistMessage

    convo = None
    if payload.conversation_id:
        convo = (
            db.query(AssistConversation)
            .filter(AssistConversation.id == payload.conversation_id,
                    AssistConversation.user_id == current_user.id)
            .first()
        )
        if not convo:
            raise HTTPException(status_code=404, detail="Conversation not found")
    if convo is None:
        ctx_id = payload.context.contract_id if payload.context else None
        convo = AssistConversation(
            user_id=current_user.id,
            title=(question[:44] + "…") if len(question) > 44 else question,
            context_contract_id=ctx_id,
        )
        db.add(convo)
        db.flush()

    prior = (
        db.query(AssistMessage)
        .filter(AssistMessage.conversation_id == convo.id)
        .order_by(AssistMessage.created_at.desc())
        .limit(16)
        .all()
    )
    history_messages = [
        {"role": m.role, "content": m.content}
        for m in reversed(prior)
        if m.role in {"user", "assistant"} and m.content
    ]
    if not history_messages:
        # legacy clients that still send history inline
        for h in (payload.history or [])[-8:]:
            if h.get("role") in {"user", "assistant"} and h.get("content"):
                history_messages.append({"role": h["role"], "content": h["content"]})
    return convo, history_messages


def _persist_turn(db, convo, question: str, answer: str, meta: dict):
    from .models import AssistMessage
    db.add(AssistMessage(conversation_id=convo.id, role="user", content=question, meta_json={}))
    db.add(AssistMessage(conversation_id=convo.id, role="assistant", content=answer, meta_json=meta))
    convo.updated_at = datetime.utcnow()
    db.commit()


@app.post("/api/v1/assistant/chat")
async def assistant_chat(
    payload: AssistantChatIn,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    question = (payload.question or "").strip()
    if not question:
        raise HTTPException(status_code=400, detail="question is required")

    from .chat_service import FOLLOWUP_INSTRUCTION, extract_followups, new_session_id, EMAIL_INTENT_RE
    from .search import (SEARCH_TOOL, SearchFilters, build_query, apply_risk_floor,
                         contract_row)

    convo, history_messages = _load_or_create_conversation(db, current_user, payload, question)

    # --- Page context: contract-scoped when the user is viewing a contract ---
    ctx_contract = None
    if payload.context and payload.context.contract_id:
        ctx_contract = (
            db.query(Contract)
            .filter(Contract.id == payload.context.contract_id)
            .first()
        )
        # silently ignored when not accessible (owner / BU-mate / org admin only)
        if ctx_contract is not None and not can_read_full(current_user, ctx_contract):
            ctx_contract = None

    import openai
    client = openai.AsyncOpenAI(api_key=os.getenv("OPENAI_API_KEY"))
    try:
        emb = await client.embeddings.create(model=_openai_embedding_model(), dimensions=_embedding_dimensions(), input=question)
        q_embedding = emb.data[0].embedding
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Embedding failed: {str(e)}")

    # Blended retrieval: current-contract clauses first, portfolio second.
    matches = []
    ctx_match_count = 0
    if ctx_contract is not None:
        ctx_matches = (
            db.query(ContractClause, Contract)
            .join(Contract, Contract.id == ContractClause.contract_id)
            .filter(Contract.id == ctx_contract.id, ContractClause.embedding.isnot(None))
            .order_by(ContractClause.embedding.cosine_distance(q_embedding))
            .limit(5)
            .all()
        )
        ctx_match_count = len(ctx_matches)
        matches.extend(ctx_matches)
    portfolio_q = (
        db.query(ContractClause, Contract)
        .join(Contract, Contract.id == ContractClause.contract_id)
        .filter(bu_scope_criterion(current_user), ContractClause.embedding.isnot(None))
    )
    if ctx_contract is not None:
        portfolio_q = portfolio_q.filter(Contract.id != ctx_contract.id)
    matches.extend(
        portfolio_q.order_by(ContractClause.embedding.cosine_distance(q_embedding))
        .limit(3 if ctx_contract is not None else 6)
        .all()
    )

    context_parts = []
    sources = []
    for i, (clause, contract) in enumerate(matches):
        risk = clause.risk_level.value if clause.risk_level else "LOW"
        if ctx_contract is not None and i == 0:
            context_parts.append(f"=== CURRENT CONTRACT ({ctx_contract.filename}) ===")
        if ctx_contract is not None and i == ctx_match_count and ctx_match_count > 0:
            context_parts.append("=== OTHER CONTRACTS IN PORTFOLIO ===")
        context_parts.append(f"[{contract.filename} :: {clause.clause_type} | Risk: {risk}]\n{(clause.text_content or '')[:900]}")
        sources.append(
            {
                "contract_id": str(contract.id),
                "contract_name": contract.filename,
                "contract_filename": contract.filename,
                "clause_id": str(clause.id),
                "clause_type": clause.clause_type,
                "risk_level": risk,
                "text_excerpt": (clause.text_content or "")[:220],
            }
        )

    system_prompt = (
        "You are Jaggaer Assist, the ContractsPulse copilot for procurement managers. "
        "You help answer questions and find relevant clause examples across the user's contracts. "
        "Use ONLY the provided clause excerpts. If you lack context, ask a follow-up question or say you can't confirm. "
        "When you cite a clause, mention the contract filename. "
        "If the user asks to FIND or FILTER contracts by attributes (type, vendor, dates, value, "
        "renewal, business unit), call the search_contracts tool instead of answering from excerpts."
        + (f" The user is currently viewing contract '{ctx_contract.filename}'. Prefer it when answering; "
           "cite other contracts only for comparison." if ctx_contract is not None else "")
        + FOLLOWUP_INSTRUCTION
    )

    separator = "\n\n---\n\n"
    joined_context = separator.join(context_parts) if context_parts else "(none)"
    user_message = f"Relevant Clauses:\n{joined_context}\n\nQuestion: {question}"
    messages = [{"role": "system", "content": system_prompt}, *history_messages, {"role": "user", "content": user_message}]

    try:
        primary_model = _openai_assistant_model()
        try:
            resp = await client.chat.completions.create(
                model=primary_model, messages=messages,
                tools=[SEARCH_TOOL], tool_choice="auto",
                max_tokens=4096, temperature=0.2,
            )
        except Exception:
            fallback_model = os.getenv("OPENAI_MODEL_ASSISTANT_FALLBACK", "gpt-4.1-mini").strip()
            resp = await client.chat.completions.create(
                model=fallback_model, messages=messages,
                tools=[SEARCH_TOOL], tool_choice="auto",
                max_tokens=4096, temperature=0.2,
            )
        msg = resp.choices[0].message
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Assistant chat failed: {str(e)}")

    # --- Metadata route: the model chose structured search over RAG ---
    if msg.tool_calls:
        import json as _json
        try:
            args = _json.loads(msg.tool_calls[0].function.arguments or "{}")
            filters = SearchFilters(**{k: v for k, v in args.items() if k in SearchFilters.model_fields})
        except Exception:
            filters = SearchFilters()
        base = db.query(Contract).filter(bu_scope_criterion(current_user))
        rows = build_query(base, filters).limit(filters.limit).all()
        rows = apply_risk_floor(rows, filters.risk_at_least)
        results = [contract_row(c) for c in rows]

        n = len(results)
        answer = (
            f"**Found {n} contract{'s' if n != 1 else ''}** — {filters.describe()}."
            + ("" if n else " Try loosening the filters or asking differently.")
        )
        actions = [
            {"type": "open_contract", "label": f"Open {r['filename'][:30]}{'…' if len(r['filename']) > 30 else ''}", "contract_id": r["id"]}
            for r in results[:5]
        ]
        response_meta = {
            "sources": [],
            "actions": actions,
            "suggested_questions": [],
            "route": "metadata",
            "query_scope": "portfolio",
            "filters_applied": filters.applied(),
            "results": results,
        }
        _persist_turn(db, convo, question, answer, response_meta)
        return {
            "answer": answer,
            "conversation_id": str(convo.id),
            "conversation_mode": "multi_turn" if history_messages else "single_turn",
            "session_id": new_session_id(payload.session_id),
            **response_meta,
        }

    # --- RAG route ---
    answer, followups = extract_followups(msg.content or "")

    actions: list[dict] = []
    seen_contracts: set[str] = set()
    for clause, contract in matches:
        cid = str(contract.id)
        if cid in seen_contracts:
            continue
        seen_contracts.add(cid)
        name = contract.filename or "contract"
        short = name if len(name) <= 30 else name[:30] + "…"
        actions.append({"type": "open_contract", "label": f"Open {short}", "contract_id": cid})
        if (contract.metadata_json or {}).get("deviation_analysis"):
            actions.append({"type": "view_deviations", "label": f"Deviations — {short}", "contract_id": cid})
        if len(seen_contracts) >= 3:
            break
    redlined = next(((cl, co) for cl, co in matches if (cl.redline_suggestion or "").strip()), None)
    if redlined:
        actions.append({
            "type": "copy_redline",
            "label": f"Copy redline — {redlined[0].clause_type}",
            "contract_id": str(redlined[1].id),
            "clause_type": redlined[0].clause_type,
            "text": redlined[0].redline_suggestion,
        })
    if EMAIL_INTENT_RE.search(question) and matches:
        actions.append({
            "type": "draft_email",
            "label": "Draft vendor email",
            "contract_id": str(ctx_contract.id) if ctx_contract is not None else str(matches[0][1].id),
        })

    if ctx_contract is not None:
        cited_ids = {s["contract_id"] for s in sources}
        query_scope = "contract" if cited_ids <= {str(ctx_contract.id)} else "contract+portfolio"
    else:
        query_scope = "portfolio"

    response_meta = {
        "sources": sources,
        "actions": actions,
        "suggested_questions": followups,
        "route": "rag",
        "query_scope": query_scope,
    }
    _persist_turn(db, convo, question, answer, response_meta)
    return {
        "answer": answer,
        "conversation_id": str(convo.id),
        "conversation_mode": "multi_turn" if history_messages else "single_turn",
        "session_id": new_session_id(payload.session_id),
        **response_meta,
    }


# ---------------------------------------------------------------------------
# Governed conversations — server-persisted, owner-scoped
# ---------------------------------------------------------------------------

@app.get("/api/v1/conversations")
async def list_conversations(
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    from .models import AssistConversation, AssistMessage
    rows = (
        db.query(AssistConversation)
        .filter(AssistConversation.user_id == current_user.id)
        .order_by(AssistConversation.updated_at.desc())
        .limit(50)
        .all()
    )
    counts = dict(
        db.query(AssistMessage.conversation_id, func.count(AssistMessage.id))
        .filter(AssistMessage.conversation_id.in_([r.id for r in rows]))
        .group_by(AssistMessage.conversation_id)
        .all()
    ) if rows else {}
    return {"conversations": [
        {
            "id": str(r.id),
            "title": r.title,
            "context_contract_id": str(r.context_contract_id) if r.context_contract_id else None,
            "message_count": int(counts.get(r.id, 0)),
            "updated_at": r.updated_at.isoformat() if r.updated_at else None,
        }
        for r in rows
    ]}


@app.get("/api/v1/conversations/{conversation_id}")
async def get_conversation(
    conversation_id: str,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    from .models import AssistConversation, AssistMessage
    convo = (
        db.query(AssistConversation)
        .filter(AssistConversation.id == conversation_id,
                AssistConversation.user_id == current_user.id)
        .first()
    )
    if not convo:
        raise HTTPException(status_code=404, detail="Conversation not found")
    messages = (
        db.query(AssistMessage)
        .filter(AssistMessage.conversation_id == convo.id)
        .order_by(AssistMessage.created_at)
        .all()
    )
    return {
        "conversation": {
            "id": str(convo.id),
            "title": convo.title,
            "context_contract_id": str(convo.context_contract_id) if convo.context_contract_id else None,
            "updated_at": convo.updated_at.isoformat() if convo.updated_at else None,
        },
        "messages": [
            {"id": str(m.id), "role": m.role, "content": m.content,
             "meta_json": m.meta_json or {}, "created_at": m.created_at.isoformat()}
            for m in messages
        ],
    }


class ConversationPatchIn(BaseModel):
    title: str


@app.patch("/api/v1/conversations/{conversation_id}")
async def rename_conversation(
    conversation_id: str,
    payload: ConversationPatchIn,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    from .models import AssistConversation
    convo = (
        db.query(AssistConversation)
        .filter(AssistConversation.id == conversation_id,
                AssistConversation.user_id == current_user.id)
        .first()
    )
    if not convo:
        raise HTTPException(status_code=404, detail="Conversation not found")
    title = (payload.title or "").strip()
    if title:
        convo.title = title[:120]
        db.commit()
    return {"id": str(convo.id), "title": convo.title}


@app.delete("/api/v1/conversations/{conversation_id}")
async def delete_conversation(
    conversation_id: str,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    from .models import AssistConversation
    convo = (
        db.query(AssistConversation)
        .filter(AssistConversation.id == conversation_id,
                AssistConversation.user_id == current_user.id)
        .first()
    )
    if not convo:
        raise HTTPException(status_code=404, detail="Conversation not found")
    db.delete(convo)
    db.commit()
    return {"message": "Conversation deleted"}


# ---------------------------------------------------------------------------
# Contract template library + comparison
# ---------------------------------------------------------------------------

@app.get("/api/v1/templates")
async def list_templates(
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    rows = (
        db.query(ContractTemplate)
        .filter(ContractTemplate.user_id == current_user.id)
        .order_by(ContractTemplate.created_at.desc())
        .all()
    )
    counts = dict(
        db.query(TemplateClause.template_id, func.count(TemplateClause.id))
        .filter(TemplateClause.template_id.in_([t.id for t in rows]))
        .group_by(TemplateClause.template_id)
        .all()
    ) if rows else {}
    return {
        "templates": [
            {
                "id": str(t.id),
                "name": t.name,
                "description": t.description,
                "status": t.status or "PENDING",
                "clause_count": int(counts.get(t.id, 0)),
                "created_at": t.created_at.isoformat(),
            }
            for t in rows
        ]
    }


@app.post("/api/v1/templates")
async def create_template(
    payload: TemplateCreateIn,
    background_tasks: BackgroundTasks,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    name = (payload.name or "").strip()
    raw_text = (payload.raw_text or "").strip()
    if not name:
        raise HTTPException(status_code=400, detail="name is required")
    if not raw_text:
        raise HTTPException(status_code=400, detail="raw_text is required")

    t = ContractTemplate(
        user_id=current_user.id,
        name=name,
        description=(payload.description or "").strip() or None,
        raw_text=raw_text,
        status="PENDING",
    )
    db.add(t)
    db.commit()
    db.refresh(t)
    background_tasks.add_task(segment_and_embed_template, str(t.id))
    return {"id": str(t.id), "status": "PENDING"}


@app.post("/api/v1/templates/upload")
async def upload_template(
    background_tasks: BackgroundTasks,
    file: UploadFile = File(...),
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """Upload a standard-template PDF ('our paper') and segment it in background."""
    file_bytes = await file.read()
    if not file_bytes:
        raise HTTPException(status_code=400, detail="Empty file")
    try:
        _, raw_text = await extract_text_from_pdf(file_bytes)
    except Exception as e:
        raise HTTPException(status_code=400, detail=f"Could not parse PDF: {e}")
    if not (raw_text or "").strip():
        raise HTTPException(status_code=400, detail="No extractable text in the PDF")

    name = (file.filename or "Template").rsplit(".", 1)[0].replace("_", " ").strip() or "Template"
    t = ContractTemplate(
        user_id=current_user.id,
        name=name,
        description="Uploaded template file",
        raw_text=raw_text,
        status="PENDING",
    )
    db.add(t)
    db.commit()
    db.refresh(t)
    background_tasks.add_task(segment_and_embed_template, str(t.id))
    return {"id": str(t.id), "name": t.name, "status": "PENDING"}


@app.get("/api/v1/templates/{template_id}")
async def get_template(
    template_id: str,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    t = (
        db.query(ContractTemplate)
        .filter(ContractTemplate.id == template_id, ContractTemplate.user_id == current_user.id)
        .first()
    )
    if not t:
        raise HTTPException(status_code=404, detail="Template not found")
    clauses = (
        db.query(TemplateClause)
        .filter(TemplateClause.template_id == t.id)
        .order_by(TemplateClause.position_index)
        .all()
    )
    return {
        "id": str(t.id),
        "name": t.name,
        "description": t.description,
        "status": t.status or "PENDING",
        "created_at": t.created_at.isoformat(),
        "clauses": [
            {
                "id": str(c.id),
                "clause_type": c.clause_type,
                "text_content": c.text_content,
                "position_index": c.position_index,
                "has_embedding": c.embedding is not None,
            }
            for c in clauses
        ],
    }


@app.delete("/api/v1/templates/{template_id}")
async def delete_template(
    template_id: str,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    t = (
        db.query(ContractTemplate)
        .filter(ContractTemplate.id == template_id, ContractTemplate.user_id == current_user.id)
        .first()
    )
    if not t:
        raise HTTPException(status_code=404, detail="Template not found")
    db.delete(t)
    db.commit()
    return {"message": "Template deleted"}


async def segment_and_embed_template(template_id: str):
    """Background pipeline: segment the template into typed clauses
    (extractor agent with heuristic fallback) and embed them."""
    import asyncio
    from .database import SessionLocal
    from .agents import extractor_agent, _heuristic_segment_clauses

    db = SessionLocal()
    try:
        t = db.query(ContractTemplate).filter(ContractTemplate.id == template_id).first()
        if not t:
            return
        t.status = "PROCESSING"
        db.commit()

        clauses = None
        try:
            timeout_s = float(os.getenv("CONTRACT_ANALYSIS_TIMEOUT_S", "60"))
            run = await asyncio.wait_for(extractor_agent.run(t.raw_text), timeout=timeout_s)
            clauses = run.output.clauses
        except Exception as e:
            print(f"Template extractor failed ({template_id}): {e}; using heuristic segmentation")
            clauses = _heuristic_segment_clauses(t.raw_text)

        db.query(TemplateClause).filter(TemplateClause.template_id == template_id).delete()
        rows = []
        for idx, c in enumerate(clauses):
            row = TemplateClause(
                template_id=template_id,
                clause_type=c.clause_type,
                text_content=c.text_content,
                position_index=idx,
            )
            db.add(row)
            rows.append(row)
        db.commit()

        try:
            embeddings = await _embed_texts([(r.text_content or "")[:8000] for r in rows])
            if len(embeddings) == len(rows):
                for r, e in zip(rows, embeddings):
                    r.embedding = e
        except Exception as e:
            print(f"Template embedding failed ({template_id}): {e}")

        t.status = "READY"
        db.commit()
        print(f"Template {template_id} ready: {len(rows)} clauses segmented + embedded.")
    except Exception as e:
        print(f"Template pipeline failed ({template_id}): {e}")
        try:
            t = db.query(ContractTemplate).filter(ContractTemplate.id == template_id).first()
            if t:
                t.status = "FAILED"
                db.commit()
        except Exception:
            pass
    finally:
        db.close()


# ---------------------------------------------------------------------------
# First-party paper: deviation analysis vs a standard template
# ---------------------------------------------------------------------------

@app.post("/api/v1/contracts/{contract_id}/analyze-deviations")
async def analyze_contract_deviations(
    contract_id: str,
    payload: TemplateCompareIn,
    background_tasks: BackgroundTasks,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """Kick off deviation analysis of this contract against a standard template.

    Runs in the background; poll GET /contracts/{id}/deviations for the result.
    """
    contract = get_accessible_contract(db, contract_id, current_user)
    if not contract:
        raise HTTPException(status_code=404, detail="Contract not found")
    if contract.status != ContractStatus.COMPLETED:
        raise HTTPException(status_code=409, detail="Contract analysis must be completed first.")

    template = (
        db.query(ContractTemplate)
        .filter(ContractTemplate.id == payload.template_id, ContractTemplate.user_id == current_user.id)
        .first()
    )
    if not template:
        raise HTTPException(status_code=404, detail="Template not found")
    embedded_count = (
        db.query(TemplateClause)
        .filter(TemplateClause.template_id == template.id, TemplateClause.embedding.isnot(None))
        .count()
    )
    if (template.status or "") != "READY" or embedded_count == 0:
        raise HTTPException(status_code=409, detail="Template is still processing — try again shortly.")

    meta = dict(contract.metadata_json or {})
    meta["deviation_status"] = "RUNNING"
    meta["paper_mode"] = "FIRST_PARTY"
    meta["paper_mode_source"] = "user"
    meta["matched_template_id"] = str(template.id)
    meta["processing_step"] = f"Aligning clauses against standard template '{template.name}'..."
    contract.metadata_json = meta
    log_contract_event(db, contract_id, "deviation", f"Deviation analysis started vs template '{template.name}'", {"template_id": str(template.id)})
    db.commit()

    background_tasks.add_task(run_deviation_analysis_background, contract_id, str(template.id))
    return {"status": "RUNNING", "contract_id": contract_id, "template_id": str(template.id)}


@app.get("/api/v1/contracts/{contract_id}/deviations")
async def get_contract_deviations(
    contract_id: str,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    contract = get_accessible_contract(db, contract_id, current_user)
    if not contract:
        raise HTTPException(status_code=404, detail="Contract not found")
    meta = contract.metadata_json or {}
    return {
        "status": meta.get("deviation_status"),
        "paper_mode": meta.get("paper_mode"),
        "matched_template_id": meta.get("matched_template_id"),
        "deviation_analysis": meta.get("deviation_analysis"),
    }


async def run_deviation_analysis_background(contract_id: str, template_id: str):
    """Background stage: align → deviation agent → persist, mirroring the
    redline-verification pipeline (status callbacks + heuristic fallback)."""
    from .database import SessionLocal
    from .deviation import align_clauses
    from .agents import analyze_template_deviations

    async def update_step(step_msg: str):
        db_tmp = SessionLocal()
        try:
            c = db_tmp.query(Contract).filter(Contract.id == contract_id).first()
            if c:
                existing = dict(c.metadata_json or {})
                existing["processing_step"] = step_msg
                c.metadata_json = existing
                log_contract_event(db_tmp, contract_id, "status", step_msg)
                db_tmp.commit()
        finally:
            db_tmp.close()

    db = SessionLocal()
    try:
        contract = db.query(Contract).filter(Contract.id == contract_id).first()
        template = db.query(ContractTemplate).filter(ContractTemplate.id == template_id).first()
        if not contract or not template:
            return

        template_clauses = (
            db.query(TemplateClause)
            .filter(TemplateClause.template_id == template_id)
            .order_by(TemplateClause.position_index)
            .all()
        )
        contract_clauses = db.query(ContractClause).filter(ContractClause.contract_id == contract_id).all()

        await update_step("Aligning clauses against the standard template...")
        alignment = align_clauses(template_clauses, contract_clauses)

        try:
            items = await analyze_template_deviations(alignment, use_llm=True, status_callback=update_step)
            mode = "llm"
        except Exception as e:
            print(f"Deviation LLM pass failed, using heuristic: {e}")
            items = await analyze_template_deviations(alignment, use_llm=False)
            mode = "heuristic_fallback"

        summary = alignment.summary()
        summary["standard"] = sum(1 for i in items if i.get("playbook_verdict") == "STANDARD")
        summary["off_playbook"] = sum(1 for i in items if i.get("playbook_verdict") == "OFF_PLAYBOOK")
        summary["escalations"] = sum(1 for i in items if i.get("escalate"))

        db2 = SessionLocal()
        try:
            c2 = db2.query(Contract).filter(Contract.id == contract_id).first()
            meta = dict(c2.metadata_json or {})
            meta["deviation_analysis"] = {
                "template_id": template_id,
                "template_name": template.name,
                "paper_mode": "FIRST_PARTY",
                "analysis_mode": mode,
                "generated_at": datetime.now(timezone.utc).isoformat(),
                "summary": summary,
                "items": items,
            }
            meta["deviation_status"] = "COMPLETED"
            meta["processing_step"] = "Deviation analysis complete."
            c2.metadata_json = meta
            log_contract_event(db2, contract_id, "deviation", "Deviation analysis completed", {"summary": summary, "mode": mode})
            db2.commit()
        finally:
            db2.close()
        print(f"Deviation analysis complete for {contract_id}: {summary}")
    except Exception as e:
        print(f"Deviation analysis failed for {contract_id}: {e}")
        db3 = SessionLocal()
        try:
            c3 = db3.query(Contract).filter(Contract.id == contract_id).first()
            if c3:
                meta = dict(c3.metadata_json or {})
                meta["deviation_status"] = "FAILED"
                meta["processing_step"] = f"Deviation analysis failed: {type(e).__name__}"
                c3.metadata_json = meta
                log_contract_event(db3, contract_id, "error", "Deviation analysis failed", {"error": str(e) or type(e).__name__})
                db3.commit()
        finally:
            db3.close()
    finally:
        db.close()


# ---------------------------------------------------------------------------
# Obligations — get or generate obligations for a contract
# ---------------------------------------------------------------------------

@app.get("/api/v1/contracts/{contract_id}/obligations")
async def get_obligations(
    contract_id: str,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user)
):
    """Return stored obligations for a contract."""
    contract = get_accessible_contract(db, contract_id, current_user)
    if not contract:
        raise HTTPException(status_code=404, detail="Contract not found")

    obligations = (contract.metadata_json or {}).get("obligations", None)
    return {
        "obligations": obligations,  # None = not yet generated; [] = generated but empty
        "generated": obligations is not None,
    }


# ---------------------------------------------------------------------------
# Renewal/notice automation — reminders + letter generation
# ---------------------------------------------------------------------------

def _parse_iso_date(dt_str: str):
    try:
        return datetime.fromisoformat(dt_str.replace("Z", "+00:00"))
    except Exception:
        return None


def _generate_renewal_notice_letter(contract: Contract) -> dict:
    meta = contract.metadata_json or {}
    vendor = meta.get("company") or "Counterparty"
    expiry_date = meta.get("expiry_date") or ""
    renewal_notice_days = meta.get("renewal_notice_days") or ""
    filename = contract.filename

    subject = f"Notice of Non-Renewal / Opt-Out — {filename}"
    body = (
        f"To: {vendor}\n"
        f"Re: {filename}\n\n"
        f"This letter provides written notice that we elect not to renew / to opt out of any automatic renewal under the Agreement.\n\n"
        f"Please confirm in writing that the Agreement will terminate at the end of the current term. "
        f"Our records indicate an expiry/term end date of {expiry_date} and a notice period of {renewal_notice_days} days (if applicable).\n\n"
        f"Sincerely,\n"
        f"[Your Name]\n"
        f"[Title]\n"
        f"[Company]\n"
    )
    return {"subject": subject, "body": body}


def _format_redline_items_for_email(resolutions: list[dict], include: str) -> tuple[str, list[dict]]:
    include = (include or "unresolved").lower().strip()
    if include not in {"unresolved", "all"}:
        include = "unresolved"

    def is_included(r: dict) -> bool:
        if include == "all":
            return True
        return (r.get("status") or "").upper() in {"UNRESOLVED", "PARTIALLY_RESOLVED"}

    items = [r for r in (resolutions or []) if is_included(r)]
    bullets = []
    for r in items:
        clause_type = r.get("clause_type") or "Clause"
        status = (r.get("status") or "UNRESOLVED").replace("_", " ").title()
        originally = (r.get("parent_risk_level") or "").title()
        proposed = (r.get("parent_redline_suggestion") or "").strip()
        bullets.append(
            {
                "clause_type": clause_type,
                "status": status,
                "original_risk_level": originally,
                "proposed_redline": proposed[:900],
            }
        )
    lines = []
    for b in bullets:
        lines.append(f"- {b['clause_type']} (status: {b['status']}; originally {b['original_risk_level']})")
        if b.get("proposed_redline"):
            lines.append(f"  Suggested language: {b['proposed_redline']}")
    text = "\n".join(lines) or "- (none)"
    return text, bullets


def _format_proposed_redlines_for_email(clauses: list, include: str) -> tuple[str, list[dict]]:
    """Build email bullets from a contract's own proposed redlines.

    Used for first-version contracts (no counterparty edits to verify yet), so the
    vendor email requests the AI-recommended redlines directly from the clause analysis.
    include='unresolved' -> only HIGH/CRITICAL clauses; include='all' -> any clause with a redline.
    """
    include = (include or "unresolved").lower().strip()
    if include not in {"unresolved", "all"}:
        include = "unresolved"

    severity = {"CRITICAL": 3, "HIGH": 2, "MEDIUM": 1, "LOW": 0}
    bullets = []
    for c in (clauses or []):
        risk = c.risk_level.value if hasattr(c.risk_level, "value") else (str(c.risk_level) if c.risk_level else "LOW")
        redline = (c.redline_suggestion or "").strip()
        if not redline and risk in {"HIGH", "CRITICAL"}:
            redline = (_heuristic_redline(c.clause_type, c.text_content, risk) or "").strip()
        if not redline:
            continue
        if include == "unresolved" and risk not in {"HIGH", "CRITICAL"}:
            continue
        bullets.append(
            {
                "clause_type": c.clause_type or "Clause",
                "status": "Proposed",
                "original_risk_level": risk.title(),
                "proposed_redline": redline[:900],
            }
        )

    bullets.sort(key=lambda b: severity.get((b["original_risk_level"] or "").upper(), 0), reverse=True)
    lines = []
    for b in bullets:
        lines.append(f"- {b['clause_type']} (risk: {b['original_risk_level']})")
        if b.get("proposed_redline"):
            lines.append(f"  Suggested language: {b['proposed_redline']}")
    text = "\n".join(lines) or "- (none)"
    return text, bullets


@app.post("/api/v1/contracts/{contract_id}/redlines/email")
async def generate_vendor_redlines_email(
    contract_id: str,
    payload: VendorEmailDraftIn,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """
    Generate (but do not send) an email draft to the vendor summarizing proposed redlines
    and requested changes based on redline verification results.
    """
    contract = get_accessible_contract(db, contract_id, current_user)
    if not contract:
        raise HTTPException(status_code=404, detail="Contract not found")

    meta = contract.metadata_json or {}
    vendor = (meta.get("company") or "Vendor").strip()
    resolutions = meta.get("redline_resolutions") or []
    if not isinstance(resolutions, list):
        resolutions = []

    include = (payload.include or "unresolved").lower().strip()
    if resolutions:
        bullets_text, bullets = _format_redline_items_for_email(resolutions, include=include)
    else:
        # First-version contract (no counterparty edits yet): request our proposed redlines.
        clauses = db.query(ContractClause).filter(ContractClause.contract_id == contract_id).all()
        bullets_text, bullets = _format_proposed_redlines_for_email(clauses, include=include)

    import openai
    client = openai.AsyncOpenAI(api_key=os.getenv("OPENAI_API_KEY"))

    system_prompt = (
        "You are an experienced procurement professional writing a concise email to a vendor. "
        "Your goal is to clearly request specific contract edits (redlines) without being adversarial. "
        "For each item you are given the clause and the specific suggested language we want — incorporate that "
        "actual language so the vendor knows exactly what change is being requested. Be concrete, not generic. "
        "Do not claim legal conclusions. Do not invent contract terms beyond what is provided. "
        "Do NOT include a signature block: no placeholder name (e.g. '[Your Name]'), job title, company name, or "
        "contact details. End with a simple closing such as 'Best regards,' on its own line. "
        "Output ONLY JSON with keys: subject, body."
    )

    tone = (payload.tone or "professional").strip().lower()
    filename = contract.filename
    version_num = (meta.get("version_number") or "")
    subject_hint = f"Redlines / Requested Revisions — {filename}"

    user_prompt = (
        f"Vendor: {vendor}\n"
        f"Contract: {filename}\n"
        f"Contract Version: {version_num}\n"
        f"Include: {include}\n"
        f"Tone: {tone}\n\n"
        f"Requested items (each may include the specific suggested language to request):\n{bullets_text}\n\n"
        "Write an email that:\n"
        "- Opens with brief appreciation and context\n"
        "- For each item, names the clause and states the specific change we're requesting, referencing the suggested language provided above\n"
        "- Asks for confirmation and a timeline\n"
        "- Mentions we can hop on a quick call if helpful\n"
        "- Ends with a simple closing only (e.g. 'Best regards,'); do NOT add a name, job title, or company\n"
        f"Subject should be similar to: {subject_hint}\n"
    )

    try:
        try:
            resp = await client.chat.completions.create(
                model=_openai_chat_model(),
                messages=[{"role": "system", "content": system_prompt}, {"role": "user", "content": user_prompt}],
                max_tokens=4096,
                temperature=0.2,
                response_format={"type": "json_object"},
            )
        except Exception:
            resp = await client.chat.completions.create(
                model=os.getenv("OPENAI_MODEL_CHAT_FALLBACK", "gpt-4.1").strip(),
                messages=[{"role": "system", "content": system_prompt}, {"role": "user", "content": user_prompt}],
                max_tokens=4096,
                temperature=0.2,
                response_format={"type": "json_object"},
            )
        content = resp.choices[0].message.content or "{}"
        import json
        draft = json.loads(content)
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Email generation failed: {str(e)}")

    subject = (draft.get("subject") or subject_hint).strip()
    body = (draft.get("body") or "").strip()
    if not body:
        body = (
            f"Hi {vendor},\n\n"
            f"Sharing a few requested edits for {filename}:\n{bullets_text}\n\n"
            "Thanks,\n"
        )

    return {
        "email": {"subject": subject, "body": body},
        "items": bullets,
        "generated_by_ai": True,
    }


@app.get("/api/v1/contracts/{contract_id}/reminders")
async def list_contract_reminders(
    contract_id: str,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    contract = get_accessible_contract(db, contract_id, current_user)
    if not contract:
        raise HTTPException(status_code=404, detail="Contract not found")

    rows = (
        db.query(ContractReminder)
        .filter(ContractReminder.contract_id == contract_id, ContractReminder.user_id == current_user.id)
        .order_by(ContractReminder.due_date.asc())
        .all()
    )
    return {
        "reminders": [
            ReminderOut(
                id=str(r.id),
                contract_id=str(r.contract_id),
                reminder_type=str(r.reminder_type),
                status=str(r.status),
                due_date=r.due_date.isoformat(),
                title=r.title,
                body=r.body,
                letter=r.letter_json or None,
            ).model_dump()
            for r in rows
        ]
    }


@app.post("/api/v1/contracts/{contract_id}/reminders")
async def create_contract_reminder(
    contract_id: str,
    payload: CreateReminderIn,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    contract = get_accessible_contract(db, contract_id, current_user)
    if not contract:
        raise HTTPException(status_code=404, detail="Contract not found")

    due_dt = _parse_iso_date(payload.due_date)
    if not due_dt:
        raise HTTPException(status_code=400, detail="Invalid due_date; expected ISO datetime")

    reminder = ContractReminder(
        user_id=current_user.id,
        contract_id=contract.id,
        reminder_type=payload.reminder_type,
        status="OPEN",
        due_date=due_dt,
        title=(payload.title or "").strip() or "Reminder",
        body=(payload.body or "").strip() or None,
        letter_json={},
    )
    db.add(reminder)
    db.commit()
    db.refresh(reminder)

    return {"id": str(reminder.id)}


@app.post("/api/v1/contracts/{contract_id}/letters/renewal-notice")
async def generate_renewal_notice_letter(
    contract_id: str,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    contract = get_accessible_contract(db, contract_id, current_user)
    if not contract:
        raise HTTPException(status_code=404, detail="Contract not found")

    letter = _generate_renewal_notice_letter(contract)
    return {"letter": letter}


@app.post("/api/v1/contracts/{contract_id}/obligations/generate")
async def generate_obligations_on_demand(
    contract_id: str,
    background_tasks: BackgroundTasks,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user)
):
    """Generate obligations on-demand for contracts that were analyzed before this feature."""
    contract = get_accessible_contract(db, contract_id, current_user)
    if not contract:
        raise HTTPException(status_code=404, detail="Contract not found")
    if contract.status != ContractStatus.COMPLETED:
        raise HTTPException(status_code=409, detail="Contract must be completed to generate obligations.")

    raw_text = (contract.metadata_json or {}).get("raw_text", "")
    if not raw_text:
        raise HTTPException(status_code=400, detail="Raw text not available for this contract.")

    async def _generate(cid: str, text: str):
        from .database import SessionLocal
        db2 = SessionLocal()
        try:
            result = await extract_contract_obligations(text)
            obligations_data = [
                {
                    "title": o.title,
                    "description": o.description,
                    "party_responsible": o.party_responsible,
                    "due_trigger": o.due_trigger,
                    "category": o.category,
                }
                for o in result.obligations
            ]
            c = db2.query(Contract).filter(Contract.id == cid).first()
            if c:
                existing = c.metadata_json or {}
                existing["obligations"] = obligations_data
                c.metadata_json = existing
                db2.commit()
        except Exception as e:
            print(f"On-demand obligation generation failed: {e}")
        finally:
            db2.close()

    background_tasks.add_task(_generate, contract_id, raw_text)
    return {"message": "Obligation generation started.", "status": "generating"}


# ---------------------------------------------------------------------------
# Admin: one-shot LLM metadata backfill for pre-existing contracts
# ---------------------------------------------------------------------------

@app.post("/api/v1/admin/backfill-metadata")
async def backfill_metadata(
    force: bool = False,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """Run LLM metadata extraction over completed contracts that lack it.

    Idempotent: skips contracts that already have a contract_type unless force=true.
    """
    q = db.query(Contract).filter(
        Contract.user_id == current_user.id,
        Contract.status == ContractStatus.COMPLETED,
    )
    if not force:
        q = q.filter(Contract.contract_type.is_(None))
    rows = q.all()

    processed, failed, skipped = 0, 0, 0
    for contract in rows:
        raw_text = (contract.metadata_json or {}).get("raw_text") or ""
        if not raw_text.strip():
            skipped += 1
            continue
        before = contract.contract_type
        await _extract_and_save_metadata(contract, raw_text)
        if contract.contract_type or before:
            processed += 1
        else:
            failed += 1
        db.commit()

    return {"processed": processed, "failed": failed, "skipped": skipped, "total_candidates": len(rows)}


# ---------------------------------------------------------------------------
# Organization: business units, cross-BU agreement discovery, chat audit
# ---------------------------------------------------------------------------

@app.get("/api/v1/business-units")
async def list_business_units(
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    from .models import BusinessUnit
    if not current_user.org_id:
        return {"business_units": []}
    rows = (
        db.query(BusinessUnit)
        .filter(BusinessUnit.org_id == current_user.org_id)
        .order_by(BusinessUnit.name)
        .all()
    )
    return {"business_units": [{"id": str(b.id), "name": b.name} for b in rows]}


@app.get("/api/v1/org/agreements")
async def org_agreements(
    vendor: str | None = None,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """Cross-business discovery: which vendors does the WHOLE organization
    already have paper with, and in which business units?

    Discovery-level projection only — no clause/risk data for foreign BUs.
    """
    from .models import BusinessUnit
    if not current_user.org_id:
        return {"vendors": []}

    q = (
        db.query(Contract, BusinessUnit)
        .join(BusinessUnit, Contract.business_unit_id == BusinessUnit.id)
        .filter(BusinessUnit.org_id == current_user.org_id)
    )
    if vendor:
        term = f"%{vendor}%"
        q = q.filter(or_(Contract.counterparty.ilike(term), Contract.company.ilike(term),
                         Contract.filename.ilike(term)))
    rows = q.order_by(Contract.created_at.desc()).limit(500).all()

    groups: dict = {}
    for contract, bu in rows:
        name = (contract.counterparty or contract.company or "Unknown vendor").strip()
        key = name.lower()
        g = groups.setdefault(key, {"vendor": name, "business_units": set(), "contracts": []})
        g["business_units"].add(bu.name)
        g["contracts"].append({
            "id": str(contract.id),
            "filename": contract.filename,
            "contract_type": contract.contract_type,
            "business_unit": bu.name,
            "effective_date": contract.effective_date.isoformat() if contract.effective_date else None,
            "expiry_date": contract.expiry_date.isoformat() if contract.expiry_date else None,
            "status": contract.status.value if contract.status else None,
            "is_mine": can_read_full(current_user, contract),
        })

    vendors = []
    for g in groups.values():
        vendors.append({
            "vendor": g["vendor"],
            "bu_count": len(g["business_units"]),
            "business_units": sorted(g["business_units"]),
            "contract_count": len(g["contracts"]),
            "contracts": g["contracts"],
        })
    vendors.sort(key=lambda v: (-v["bu_count"], -v["contract_count"]))
    return {"vendors": vendors}


def _require_org_admin(current_user: User):
    if (getattr(current_user, "role", None) or "member") != "org_admin":
        raise HTTPException(status_code=403, detail="Requires organization admin role")


@app.get("/api/v1/admin/conversations")
async def admin_list_conversations(
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """Org-admin governance: audit view over all users' Assist conversations."""
    from .models import AssistConversation, AssistMessage, BusinessUnit
    _require_org_admin(current_user)
    rows = (
        db.query(AssistConversation, User)
        .join(User, AssistConversation.user_id == User.id)
        .filter(User.org_id == current_user.org_id)
        .order_by(AssistConversation.updated_at.desc())
        .limit(200)
        .all()
    )
    counts = dict(
        db.query(AssistMessage.conversation_id, func.count(AssistMessage.id))
        .filter(AssistMessage.conversation_id.in_([c.id for c, _ in rows]))
        .group_by(AssistMessage.conversation_id)
        .all()
    ) if rows else {}
    bu_names = {str(b.id): b.name for b in db.query(BusinessUnit).filter(BusinessUnit.org_id == current_user.org_id).all()}
    return {"conversations": [
        {
            "id": str(c.id),
            "title": c.title,
            "owner_email": u.email,
            "business_unit": bu_names.get(str(u.business_unit_id)) if u.business_unit_id else None,
            "message_count": int(counts.get(c.id, 0)),
            "updated_at": c.updated_at.isoformat() if c.updated_at else None,
        }
        for c, u in rows
    ]}


@app.get("/api/v1/admin/conversations/{conversation_id}")
async def admin_get_conversation(
    conversation_id: str,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    from .models import AssistConversation, AssistMessage
    _require_org_admin(current_user)
    convo = (
        db.query(AssistConversation, User)
        .join(User, AssistConversation.user_id == User.id)
        .filter(AssistConversation.id == conversation_id, User.org_id == current_user.org_id)
        .first()
    )
    if not convo:
        raise HTTPException(status_code=404, detail="Conversation not found")
    c, u = convo
    messages = (
        db.query(AssistMessage)
        .filter(AssistMessage.conversation_id == c.id)
        .order_by(AssistMessage.created_at)
        .all()
    )
    return {
        "conversation": {"id": str(c.id), "title": c.title, "owner_email": u.email,
                         "updated_at": c.updated_at.isoformat() if c.updated_at else None},
        "messages": [
            {"id": str(m.id), "role": m.role, "content": m.content,
             "meta_json": m.meta_json or {}, "created_at": m.created_at.isoformat()}
            for m in messages
        ],
    }


# ---------------------------------------------------------------------------
# Clause intelligence: coverage gaps, approved precedents, ambiguity flags
# ---------------------------------------------------------------------------

@app.get("/api/v1/contracts/{contract_id}/insights")
async def contract_insights(
    contract_id: str,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    from .insights import compute_insights
    contract = get_accessible_contract(db, contract_id, current_user)
    return compute_insights(db, contract, current_user)


# ---------------------------------------------------------------------------
# Supplier snapshot — performance visibility during intake
# ---------------------------------------------------------------------------

@app.get("/api/v1/vendors/snapshot")
async def vendor_snapshot(
    name: str,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """Cross-BU supplier history for intake decisions: agreements by unit,
    committed value, risk profile, deviation posture, expiring paper."""
    from .models import BusinessUnit
    term = f"%{(name or '').strip()}%"
    if not term.strip('%'):
        raise HTTPException(status_code=400, detail="name is required")

    q = db.query(Contract).filter(or_(Contract.counterparty.ilike(term),
                                      Contract.company.ilike(term),
                                      Contract.filename.ilike(term)))
    if current_user.org_id:
        q = (q.join(BusinessUnit, Contract.business_unit_id == BusinessUnit.id)
             .filter(BusinessUnit.org_id == current_user.org_id))
    else:
        q = q.filter(Contract.user_id == current_user.id)
    rows = q.all()
    if not rows:
        return {"vendor": name, "found": False}

    from datetime import date, timedelta
    sev = {"LOW": 0, "MEDIUM": 1, "HIGH": 2, "CRITICAL": 3}
    total_value = 0.0
    worst = "LOW"
    off_playbook = 0
    deviation_runs = 0
    by_bu: dict = {}
    expiring = []
    for c in rows:
        if c.total_value is not None:
            total_value += float(c.total_value)
        counts = (c.metadata_json or {}).get("risk_counts") or {}
        for lvl in ("CRITICAL", "HIGH", "MEDIUM"):
            if counts.get(lvl):
                if sev[lvl] > sev[worst]:
                    worst = lvl
                break
        dev = (c.metadata_json or {}).get("deviation_analysis") or {}
        if dev:
            deviation_runs += 1
            off_playbook += int((dev.get("summary") or {}).get("off_playbook") or 0)
        bu = c.business_unit or "Unassigned"
        by_bu[bu] = by_bu.get(bu, 0) + 1
        if c.expiry_date and c.expiry_date <= date.today() + timedelta(days=180):
            expiring.append({"id": str(c.id), "filename": c.filename,
                             "expiry_date": c.expiry_date.isoformat(),
                             "business_unit": bu})

    return {
        "vendor": name,
        "found": True,
        "agreement_count": len(rows),
        "business_units": [{"name": k, "count": v} for k, v in sorted(by_bu.items())],
        "total_committed_value": round(total_value, 2) or None,
        "worst_risk": worst,
        "deviation_runs": deviation_runs,
        "off_playbook_deviations": off_playbook,
        "expiring_within_180d": expiring[:5],
    }


# ---------------------------------------------------------------------------
# AI Portfolio Review — replaces the manual periodic review
# ---------------------------------------------------------------------------

@app.post("/api/v1/portfolio/review")
async def generate_portfolio_review(
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """Gather deterministic portfolio facts, have Gemini write the executive
    review with a prioritized action list."""
    from datetime import date, timedelta
    contracts = (db.query(Contract)
                 .filter(bu_scope_criterion(current_user))
                 .order_by(Contract.created_at.desc())
                 .limit(200).all())

    today = date.today()
    facts = {
        "total_contracts": len(contracts),
        "expiring_90d": [],
        "auto_renew_notice": [],
        "off_playbook": [],
        "incomplete_metadata": [],
        "critical_risk": [],
        "rfq_bypass": [],
    }
    for c in contracts:
        meta = c.metadata_json or {}
        if c.expiry_date and today <= c.expiry_date <= today + timedelta(days=90):
            facts["expiring_90d"].append(f"{c.filename} expires {c.expiry_date.isoformat()}")
        if c.auto_renewal and c.expiry_date:
            notice_days = c.renewal_notice_days or 30
            deadline = c.expiry_date - timedelta(days=notice_days)
            if today <= deadline <= today + timedelta(days=120):
                facts["auto_renew_notice"].append(
                    f"{c.filename}: non-renewal notice due {deadline.isoformat()} ({notice_days}d before {c.expiry_date.isoformat()})")
        dev = (meta.get("deviation_analysis") or {}).get("summary") or {}
        if dev.get("off_playbook"):
            facts["off_playbook"].append(f"{c.filename}: {dev['off_playbook']} off-standard clauses ({dev.get('escalations', 0)} escalations)")
        if c.completeness_score is not None and c.completeness_score < 0.5:
            facts["incomplete_metadata"].append(f"{c.filename} ({int(c.completeness_score * 100)}% complete)")
        counts = meta.get("risk_counts") or {}
        if counts.get("CRITICAL"):
            facts["critical_risk"].append(f"{c.filename}: {counts['CRITICAL']} critical clauses")
        if meta.get("rfq_bypass_suspect"):
            facts["rfq_bypass"].append(c.filename)

    fact_lines = []
    for k, v in facts.items():
        if isinstance(v, list) and v:
            fact_lines.append(f"{k}: " + "; ".join(v[:8]))
    facts_text = "\n".join(fact_lines) or "(no findings)"

    import openai
    client = openai.AsyncOpenAI(api_key=os.getenv("OPENAI_API_KEY"))
    try:
        resp = await client.chat.completions.create(
            model=_openai_chat_model(),
            messages=[
                {"role": "system", "content": (
                    "You are a contract portfolio reviewer for a procurement organization. "
                    "Write a crisp executive review in Markdown from ONLY the facts provided: "
                    "a 2-3 sentence health summary, then '### Priority actions' as a numbered "
                    "list (most urgent first, each citing the specific contract and date), then "
                    "'### Watch list' bullets. No invented facts, no preamble.")},
                {"role": "user", "content": f"Today: {today.isoformat()}\nPortfolio: {facts['total_contracts']} contracts\n\nFindings:\n{facts_text}"},
            ],
            max_tokens=4096,
            temperature=0.2,
        )
        review_md = resp.choices[0].message.content or ""
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Review generation failed: {e}")

    return {
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "facts": {k: v for k, v in facts.items() if not isinstance(v, list) or v},
        "review_markdown": review_md,
    }


# ---------------------------------------------------------------------------
# Contract families — amendments & related documents
# ---------------------------------------------------------------------------

RELATIONSHIP_TYPES = {"AMENDS", "AMENDED_BY", "ORDER_UNDER", "MASTER_OF", "RENEWS", "INCORPORATES", "RELATED"}


@app.get("/api/v1/contracts/{contract_id}/related")
async def get_related_documents(
    contract_id: str,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """Persisted family links + auto-suggestions from extracted document references."""
    from .models import ContractRelationship
    contract = get_accessible_contract(db, contract_id, current_user)

    def project(c: Contract) -> dict:
        return {"id": str(c.id), "filename": c.filename, "contract_type": c.contract_type,
                "counterparty": c.counterparty or c.company,
                "business_unit": c.business_unit,
                "expiry_date": c.expiry_date.isoformat() if c.expiry_date else None}

    links = []
    linked_ids = {str(contract.id)}
    rels = (db.query(ContractRelationship)
            .filter(or_(ContractRelationship.contract_id == contract.id,
                        ContractRelationship.related_contract_id == contract.id))
            .all())
    for r in rels:
        other_id = r.related_contract_id if str(r.contract_id) == str(contract.id) else r.contract_id
        other = db.query(Contract).filter(Contract.id == other_id).first()
        if not other:
            continue
        outbound = str(r.contract_id) == str(contract.id)
        links.append({
            "relationship_id": str(r.id),
            "relationship_type": r.relationship_type if outbound else f"inverse:{r.relationship_type}",
            "source": r.source,
            "contract": project(other),
        })
        linked_ids.add(str(other_id))

    # Auto-suggestions from the metadata agent's extracted references
    suggestions = []
    refs = (contract.metadata_json or {}).get("document_references") or []
    scope = db.query(Contract).filter(bu_scope_criterion(current_user), Contract.id != contract.id).all()
    version_family = {(contract.metadata_json or {}).get("parent_contract_id")}
    for ref in refs[:8]:
        title_words = {w.lower() for w in re.findall(r"[A-Za-z]{4,}", ref.get("title") or "")}
        if not title_words:
            continue
        best, best_score = None, 0
        for cand in scope:
            if str(cand.id) in linked_ids or str(cand.id) in version_family:
                continue
            hay = f"{cand.filename} {cand.counterparty or ''} {cand.contract_type or ''}".lower()
            score = sum(1 for w in title_words if w in hay)
            if ref.get("doc_type") and cand.contract_type == ref.get("doc_type"):
                score += 2
            if score > best_score:
                best, best_score = cand, score
        if best is not None and best_score >= 2:
            rel = ref.get("relationship") or "RELATED"
            suggestions.append({
                "reference_title": ref.get("title"),
                "relationship_type": rel if rel in RELATIONSHIP_TYPES else "RELATED",
                "confidence_score": best_score,
                "contract": project(best),
            })
            linked_ids.add(str(best.id))

    # Same-counterparty nudge (weakest tier)
    vendor = (contract.counterparty or "").strip()
    if vendor:
        for cand in scope:
            if str(cand.id) in linked_ids:
                continue
            if cand.counterparty and vendor.lower().split(",")[0] in cand.counterparty.lower():
                suggestions.append({
                    "reference_title": f"Same counterparty: {vendor}",
                    "relationship_type": "RELATED",
                    "confidence_score": 1,
                    "contract": project(cand),
                })
                linked_ids.add(str(cand.id))
                if len(suggestions) >= 6:
                    break

    return {"links": links, "suggestions": suggestions[:6]}


class RelateIn(BaseModel):
    related_contract_id: str
    relationship_type: str = "RELATED"


@app.post("/api/v1/contracts/{contract_id}/related")
async def link_related_document(
    contract_id: str,
    payload: RelateIn,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    from .models import ContractRelationship
    contract = get_accessible_contract(db, contract_id, current_user)
    other = get_accessible_contract(db, payload.related_contract_id, current_user)
    rtype = payload.relationship_type if payload.relationship_type in RELATIONSHIP_TYPES else "RELATED"
    existing = (db.query(ContractRelationship)
                .filter(ContractRelationship.contract_id == contract.id,
                        ContractRelationship.related_contract_id == other.id).first())
    if existing:
        existing.relationship_type = rtype
        db.commit()
        return {"relationship_id": str(existing.id)}
    rel = ContractRelationship(contract_id=contract.id, related_contract_id=other.id,
                               relationship_type=rtype, source="user")
    db.add(rel)
    db.commit()
    db.refresh(rel)
    return {"relationship_id": str(rel.id)}


@app.delete("/api/v1/contracts/{contract_id}/related/{relationship_id}")
async def unlink_related_document(
    contract_id: str,
    relationship_id: str,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    from .models import ContractRelationship
    get_accessible_contract(db, contract_id, current_user)
    rel = db.query(ContractRelationship).filter(ContractRelationship.id == relationship_id).first()
    if not rel:
        raise HTTPException(status_code=404, detail="Link not found")
    db.delete(rel)
    db.commit()
    return {"message": "Unlinked"}
