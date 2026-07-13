import uuid
from datetime import datetime
from sqlalchemy import Column, String, DateTime, Date, ForeignKey, Text, Enum, Boolean, Integer, Numeric, Float
from sqlalchemy.dialects.postgresql import UUID, JSONB
from sqlalchemy.orm import relationship
from pgvector.sqlalchemy import Vector
import enum

from .database import Base


class Organization(Base):
    __tablename__ = "organizations"

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    name = Column(String, unique=True, nullable=False)
    created_at = Column(DateTime, default=datetime.utcnow)


class BusinessUnit(Base):
    __tablename__ = "business_units"

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    org_id = Column(UUID(as_uuid=True), ForeignKey("organizations.id", ondelete="CASCADE"), nullable=False, index=True)
    name = Column(String, nullable=False)
    created_at = Column(DateTime, default=datetime.utcnow)


class User(Base):
    __tablename__ = "users"

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    email = Column(String, unique=True, index=True, nullable=False)
    hashed_password = Column(String, nullable=False)
    org_id = Column(UUID(as_uuid=True), ForeignKey("organizations.id"), nullable=True)
    business_unit_id = Column(UUID(as_uuid=True), ForeignKey("business_units.id"), nullable=True)
    role = Column(String, nullable=False, default="member")  # member | bu_admin | org_admin
    created_at = Column(DateTime, default=datetime.utcnow)

    contracts = relationship("Contract", back_populates="user", cascade="all, delete-orphan")

class ContractStatus(str, enum.Enum):
    PENDING = "PENDING"
    PROCESSING = "PROCESSING"
    COMPLETED = "COMPLETED"
    FAILED = "FAILED"

class RiskLevel(str, enum.Enum):
    LOW = "LOW"
    MEDIUM = "MEDIUM"
    HIGH = "HIGH"
    CRITICAL = "CRITICAL"

class Contract(Base):
    __tablename__ = "contracts"

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    filename = Column(String, nullable=False)
    file_hash = Column(String, index=True, nullable=True)
    status = Column(Enum(ContractStatus), default=ContractStatus.PENDING)
    user_id = Column(UUID(as_uuid=True), ForeignKey("users.id", ondelete="CASCADE"), nullable=True)

    # Promoted, queryable business metadata (dual-written with metadata_json).
    # Nullable throughout: values are best-effort LLM/regex extractions.
    company = Column(String, nullable=True)             # party A / our side
    counterparty = Column(String, nullable=True)        # party B / vendor
    contract_type = Column(String, nullable=True)       # normalized enum-ish (MSA, NDA, ...)
    effective_date = Column(Date, nullable=True)
    expiry_date = Column(Date, nullable=True)
    auto_renewal = Column(Boolean, nullable=True)
    renewal_notice_days = Column(Integer, nullable=True)
    total_value = Column(Numeric(14, 2), nullable=True)
    currency = Column(String(3), nullable=True)
    payment_terms = Column(String, nullable=True)
    governing_law = Column(String, nullable=True)
    business_unit = Column(String, nullable=True)
    business_unit_id = Column(UUID(as_uuid=True), ForeignKey("business_units.id"), nullable=True, index=True)
    completeness_score = Column(Float, nullable=True)

    # Store aggregated analysis like overall risk score, key obligations, etc.
    metadata_json = Column(JSONB, default={})

    created_at = Column(DateTime, default=datetime.utcnow)
    updated_at = Column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)

    # Relationships
    clauses = relationship("ContractClause", back_populates="contract", cascade="all, delete-orphan")
    user = relationship("User", back_populates="contracts")


class ContractClause(Base):
    __tablename__ = "contract_clauses"

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    contract_id = Column(UUID(as_uuid=True), ForeignKey("contracts.id"), nullable=False)
    
    clause_type = Column(String, index=True) # e.g., "Indemnification", "Termination", "Payment Terms"
    text_content = Column(Text, nullable=False)
    
    # AI Risk Assessment
    risk_level = Column(Enum(RiskLevel), default=RiskLevel.LOW)
    risk_reasoning = Column(Text, nullable=True) # AI's explanation for the risk level
    redline_suggestion = Column(Text, nullable=True) # Suggested replacement text for risky clauses
    risk_debug_json = Column(JSONB, default={})  # Per-clause technical debug (dimension scores, confidence, model, latency)
    
    # 1536 is standard for OpenAI embeddings. Update to 768 if using local or some Gemini models.
    embedding = Column(Vector(1536)) 
    
    contract = relationship("Contract", back_populates="clauses")


class ContractEvent(Base):
    """
    Lightweight first-party tracing/logging for contract processing (Langfuse replacement).
    """
    __tablename__ = "contract_events"

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    contract_id = Column(UUID(as_uuid=True), ForeignKey("contracts.id"), nullable=False, index=True)

    event_type = Column(String, nullable=False)  # e.g. status, llm, error
    message = Column(Text, nullable=False)
    payload_json = Column(JSONB, default={})

    created_at = Column(DateTime, default=datetime.utcnow, index=True)


class ClauseFeedback(Base):
    """
    Phase 1 feedback loop — stores user corrections without re-scoring.
    Stories 013 & 014.
    """
    __tablename__ = "clause_feedback"

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    contract_id = Column(UUID(as_uuid=True), ForeignKey("contracts.id"), nullable=False, index=True)
    clause_id = Column(UUID(as_uuid=True), ForeignKey("contract_clauses.id"), nullable=False, index=True)
    is_risky = Column(Boolean, nullable=False)  # True = user says risky; False = user says not risky
    note = Column(Text, nullable=True)
    created_at = Column(DateTime, default=datetime.utcnow, index=True)


class ReminderType(str, enum.Enum):
    RENEWAL_NOTICE = "RENEWAL_NOTICE"
    EXPIRY_CHECKIN = "EXPIRY_CHECKIN"


class ReminderStatus(str, enum.Enum):
    OPEN = "OPEN"
    DONE = "DONE"


class ContractReminder(Base):
    __tablename__ = "contract_reminders"

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    user_id = Column(UUID(as_uuid=True), ForeignKey("users.id", ondelete="CASCADE"), nullable=False, index=True)
    contract_id = Column(UUID(as_uuid=True), ForeignKey("contracts.id", ondelete="CASCADE"), nullable=False, index=True)

    reminder_type = Column(Enum(ReminderType), nullable=False)
    status = Column(Enum(ReminderStatus), default=ReminderStatus.OPEN, nullable=False)

    due_date = Column(DateTime, nullable=False, index=True)
    title = Column(String, nullable=False)
    body = Column(Text, nullable=True)

    letter_json = Column(JSONB, default={})
    created_at = Column(DateTime, default=datetime.utcnow, index=True)


class ContractTemplate(Base):
    __tablename__ = "contract_templates"

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    user_id = Column(UUID(as_uuid=True), ForeignKey("users.id", ondelete="CASCADE"), nullable=False, index=True)

    name = Column(String, nullable=False)
    description = Column(Text, nullable=True)
    raw_text = Column(Text, nullable=False)
    # PENDING → PROCESSING → READY / FAILED (segmentation + embedding pipeline)
    status = Column(String, default="PENDING")

    created_at = Column(DateTime, default=datetime.utcnow, index=True)


class TemplateClause(Base):
    """A segmented clause of a standard template ('our paper' baseline),
    embedded for deviation alignment against incoming contract clauses."""
    __tablename__ = "template_clauses"

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    template_id = Column(UUID(as_uuid=True), ForeignKey("contract_templates.id", ondelete="CASCADE"), nullable=False, index=True)

    clause_type = Column(String, index=True)
    text_content = Column(Text, nullable=False)
    position_index = Column(Integer, default=0)

    embedding = Column(Vector(1536), nullable=True)
    created_at = Column(DateTime, default=datetime.utcnow)


class AssistConversation(Base):
    """Server-persisted Jaggaer Assist conversation — owned by a user."""
    __tablename__ = "assist_conversations"

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    user_id = Column(UUID(as_uuid=True), ForeignKey("users.id", ondelete="CASCADE"), nullable=False, index=True)
    title = Column(String, nullable=False, default="New conversation")
    context_contract_id = Column(UUID(as_uuid=True), ForeignKey("contracts.id", ondelete="SET NULL"), nullable=True)
    created_at = Column(DateTime, default=datetime.utcnow)
    updated_at = Column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)


class AssistMessage(Base):
    __tablename__ = "assist_messages"

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    conversation_id = Column(UUID(as_uuid=True), ForeignKey("assist_conversations.id", ondelete="CASCADE"), nullable=False, index=True)
    role = Column(String, nullable=False)  # user | assistant
    content = Column(Text, nullable=False)
    meta_json = Column(JSONB, default={})  # sources/actions/suggested_questions/route/query_scope
    created_at = Column(DateTime, default=datetime.utcnow, index=True)


class SavedSearch(Base):
    """Named, reusable repository search — the 'standard search process'."""
    __tablename__ = "saved_searches"

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    user_id = Column(UUID(as_uuid=True), ForeignKey("users.id", ondelete="CASCADE"), nullable=False, index=True)
    name = Column(String, nullable=False)
    question = Column(Text, nullable=True)      # original NL question, if any
    filters_json = Column(JSONB, default={})    # validated filter set to re-execute
    created_at = Column(DateTime, default=datetime.utcnow)


class ContractRelationship(Base):
    """Typed link between related documents (amendment, order form, master...)."""
    __tablename__ = "contract_relationships"

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    contract_id = Column(UUID(as_uuid=True), ForeignKey("contracts.id", ondelete="CASCADE"), nullable=False, index=True)
    related_contract_id = Column(UUID(as_uuid=True), ForeignKey("contracts.id", ondelete="CASCADE"), nullable=False, index=True)
    relationship_type = Column(String, nullable=False, default="RELATED")  # AMENDS|ORDER_UNDER|MASTER_OF|RENEWS|INCORPORATES|RELATED
    source = Column(String, nullable=False, default="user")  # user | auto
    created_at = Column(DateTime, default=datetime.utcnow)
