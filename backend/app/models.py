from datetime import UTC, datetime
from typing import Any
from uuid import uuid4

from sqlalchemy import DateTime, Float, Identity, Index, Integer, LargeBinary, String, Text
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.types import JSON
from sqlalchemy.orm import Mapped, mapped_column

from app.core.database import Base

JsonType = JSON().with_variant(JSONB, "postgresql")


def now_utc() -> datetime:
    return datetime.now(UTC)


class WorkspaceSnapshot(Base):
    """Versioned demo workspace aggregate; a normalized schema is a later step."""

    __tablename__ = "workspace_snapshots"

    id: Mapped[str] = mapped_column(String(80), primary_key=True, default="mde-ng-demo-workspace")
    revision: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    state: Mapped[dict[str, Any]] = mapped_column(JsonType, nullable=False, default=dict)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now_utc, nullable=False)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now_utc, onupdate=now_utc, nullable=False)


class ReferenceFile(Base):
    __tablename__ = "reference_files"

    path: Mapped[str] = mapped_column(String(1024), primary_key=True)
    content: Mapped[bytes] = mapped_column(LargeBinary, nullable=False)


class AuditEvent(Base):
    __tablename__ = "audit_events"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=lambda: str(uuid4()))
    sequence: Mapped[int] = mapped_column(Integer, Identity(), unique=True, nullable=False)
    occurred_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now_utc, nullable=False)
    actor_id: Mapped[str] = mapped_column(String(100), nullable=False)
    action: Mapped[str] = mapped_column(String(120), nullable=False)
    object_ref: Mapped[str] = mapped_column(String(240), nullable=False)
    detail: Mapped[str] = mapped_column(Text, nullable=False, default="")
    previous_hash: Mapped[str] = mapped_column(String(64), nullable=False, default="0" * 64)
    event_hash: Mapped[str] = mapped_column(String(64), nullable=False, default="0" * 64)


class OutboxJob(Base):
    """Durable placeholder for queue publication; workers must be idempotent."""

    __tablename__ = "outbox_jobs"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=lambda: str(uuid4()))
    task_name: Mapped[str] = mapped_column(String(120), nullable=False)
    payload: Mapped[dict[str, Any]] = mapped_column(JsonType, nullable=False, default=dict)
    status: Mapped[str] = mapped_column(String(24), nullable=False, default="queued")
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now_utc, nullable=False)


class IdempotencyRecord(Base):
    __tablename__ = "idempotency_records"

    key: Mapped[str] = mapped_column(String(160), primary_key=True)
    response_json: Mapped[dict[str, Any]] = mapped_column(JsonType, nullable=False, default=dict)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now_utc, nullable=False)


class Stage3Facility(Base):
    __tablename__ = "stage3_facilities"

    facility_id: Mapped[str] = mapped_column(String(64), primary_key=True)
    facility_name: Mapped[str] = mapped_column(String(240), nullable=False)
    facility_type: Mapped[str] = mapped_column(String(80), nullable=False, default="medical-clinic")
    city: Mapped[str] = mapped_column(String(120), nullable=False, default="")
    state_code: Mapped[str] = mapped_column(String(16), nullable=False, default="", index=True)
    postal_code: Mapped[str] = mapped_column(String(24), nullable=False, default="")
    latitude: Mapped[float | None] = mapped_column(Float, nullable=True)
    longitude: Mapped[float | None] = mapped_column(Float, nullable=True)
    time_zone: Mapped[str] = mapped_column(String(64), nullable=False, default="UTC")
    supported_pathways: Mapped[list[str]] = mapped_column(JsonType, nullable=False, default=list)
    accessibility_features: Mapped[list[str]] = mapped_column(JsonType, nullable=False, default=list)
    diagnostic_capabilities: Mapped[list[str]] = mapped_column(JsonType, nullable=False, default=list)
    status: Mapped[str] = mapped_column(String(32), nullable=False, default="active", index=True)
    data_json: Mapped[dict[str, Any]] = mapped_column(JsonType, nullable=False, default=dict)


class Stage3Examiner(Base):
    __tablename__ = "stage3_examiners"

    examiner_id: Mapped[str] = mapped_column(String(64), primary_key=True)
    doctor_name: Mapped[str] = mapped_column(String(200), nullable=False)
    primary_specialty: Mapped[str] = mapped_column(String(120), nullable=False, default="", index=True)
    specialties: Mapped[list[str]] = mapped_column(JsonType, nullable=False, default=list)
    credentials: Mapped[list[str]] = mapped_column(JsonType, nullable=False, default=list)
    license_number: Mapped[str] = mapped_column(String(80), nullable=False, default="")
    license_state: Mapped[str] = mapped_column(String(16), nullable=False, default="")
    license_expiry_date: Mapped[str] = mapped_column(String(32), nullable=False, default="", index=True)
    license_status: Mapped[str] = mapped_column(String(40), nullable=False, default="active", index=True)
    facility_ids: Mapped[list[str]] = mapped_column(JsonType, nullable=False, default=list)
    primary_facility_id: Mapped[str] = mapped_column(String(64), nullable=False, default="", index=True)
    supported_pathways: Mapped[list[str]] = mapped_column(JsonType, nullable=False, default=list)
    authorized_dbq_types: Mapped[list[str]] = mapped_column(JsonType, nullable=False, default=list)
    status: Mapped[str] = mapped_column(String(32), nullable=False, default="active", index=True)
    data_json: Mapped[dict[str, Any]] = mapped_column(JsonType, nullable=False, default=dict)


class Stage3TimeSlot(Base):
    __tablename__ = "stage3_time_slots"
    __table_args__ = (
        Index("ix_stage3_slots_fac_mod_status", "facility_id", "modality", "slot_status"),
        Index("ix_stage3_slots_exm_starts", "examiner_id", "starts_at"),
    )

    slot_id: Mapped[str] = mapped_column(String(96), primary_key=True)
    examiner_id: Mapped[str] = mapped_column(String(64), nullable=False, index=True)
    facility_id: Mapped[str] = mapped_column(String(64), nullable=False, index=True)
    starts_at: Mapped[str] = mapped_column(String(40), nullable=False, index=True)
    ends_at: Mapped[str] = mapped_column(String(40), nullable=False)
    local_starts_at: Mapped[str] = mapped_column(String(40), nullable=False, default="")
    local_ends_at: Mapped[str] = mapped_column(String(40), nullable=False, default="")
    time_zone: Mapped[str] = mapped_column(String(64), nullable=False, default="UTC")
    duration_minutes: Mapped[int] = mapped_column(Integer, nullable=False, default=60)
    modality: Mapped[str] = mapped_column(String(32), nullable=False, index=True)
    slot_status: Mapped[str] = mapped_column(String(32), nullable=False, default="available", index=True)


class Stage2ClinicalFact(Base):
    __tablename__ = "clinical_facts"
    __table_args__ = (Index("ix_clinical_facts_esr_doc", "esr_id", "document_id"),)

    fact_id: Mapped[str] = mapped_column(String(128), primary_key=True)
    esr_id: Mapped[str] = mapped_column(String(64), nullable=False, index=True)
    document_id: Mapped[str] = mapped_column(String(128), nullable=False, index=True)
    source_hash: Mapped[str] = mapped_column(String(128), nullable=False, default="")
    fact_json: Mapped[dict[str, Any]] = mapped_column(JsonType, nullable=False, default=dict)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now_utc, onupdate=now_utc, nullable=False)


class Stage2TriageDossier(Base):
    __tablename__ = "triage_dossiers"

    esr_id: Mapped[str] = mapped_column(String(64), primary_key=True)
    source_digest: Mapped[str] = mapped_column(String(128), nullable=False, index=True)
    ai_provider: Mapped[str] = mapped_column(String(80), nullable=False, default="local")
    case_overview: Mapped[str] = mapped_column(Text, nullable=False, default="")
    is_stale: Mapped[bool] = mapped_column(Integer, nullable=False, default=0)
    dossier_json: Mapped[dict[str, Any]] = mapped_column(JsonType, nullable=False, default=dict)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now_utc, onupdate=now_utc, nullable=False)


class Stage2ContentionSummary(Base):
    __tablename__ = "contention_summaries"
    __table_args__ = (Index("ix_contention_summaries_esr", "esr_id"),)

    contention_id: Mapped[str] = mapped_column(String(128), primary_key=True)
    esr_id: Mapped[str] = mapped_column(String(64), nullable=False)
    source_digest: Mapped[str] = mapped_column(String(128), nullable=False, index=True)
    summary_provider: Mapped[str] = mapped_column(String(80), nullable=False, default="local")
    summary_json: Mapped[dict[str, Any]] = mapped_column(JsonType, nullable=False, default=dict)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now_utc, onupdate=now_utc, nullable=False)


class Stage2TriageJob(Base):
    __tablename__ = "triage_jobs"
    __table_args__ = (Index("ix_triage_jobs_esr_status", "esr_id", "status"),)

    job_id: Mapped[str] = mapped_column(String(64), primary_key=True, default=lambda: str(uuid4()))
    esr_id: Mapped[str] = mapped_column(String(64), nullable=False, index=True)
    evidence_digest: Mapped[str] = mapped_column(String(128), nullable=False, index=True)
    status: Mapped[str] = mapped_column(String(32), nullable=False, default="queued", index=True)
    worker_lease: Mapped[str] = mapped_column(String(80), nullable=False, default="")
    attempts: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    last_error: Mapped[str] = mapped_column(Text, nullable=False, default="")
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now_utc, nullable=False)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now_utc, onupdate=now_utc, nullable=False)


class Stage2EvidenceVersion(Base):
    __tablename__ = "evidence_versions"

    esr_id: Mapped[str] = mapped_column(String(64), primary_key=True)
    evidence_digest: Mapped[str] = mapped_column(String(128), nullable=False, index=True)
    document_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    fact_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now_utc, onupdate=now_utc, nullable=False)


class Stage2ProcessingAudit(Base):
    __tablename__ = "processing_audit"
    __table_args__ = (Index("ix_processing_audit_esr", "esr_id", "created_at"),)

    id: Mapped[str] = mapped_column(String(64), primary_key=True, default=lambda: str(uuid4()))
    esr_id: Mapped[str] = mapped_column(String(64), nullable=False)
    evidence_digest: Mapped[str] = mapped_column(String(128), nullable=False, default="")
    provider: Mapped[str] = mapped_column(String(80), nullable=False, default="local")
    retries: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    failover_triggered: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    fallback_used: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    audit_json: Mapped[dict[str, Any]] = mapped_column(JsonType, nullable=False, default=dict)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now_utc, nullable=False)


class Stage5QAAuditJob(Base):
    __tablename__ = "stage5_qa_audit_jobs"
    __table_args__ = (Index("ix_stage5_qa_jobs_review_status", "review_id", "status"),)

    job_id: Mapped[str] = mapped_column(String(80), primary_key=True, default=lambda: str(uuid4()))
    review_id: Mapped[str] = mapped_column(String(80), nullable=False, index=True)
    dbq_id: Mapped[str] = mapped_column(String(80), nullable=False, index=True)
    version_id: Mapped[str] = mapped_column(String(80), nullable=False)
    evidence_digest: Mapped[str] = mapped_column(String(128), nullable=False, index=True)
    status: Mapped[str] = mapped_column(String(32), nullable=False, default="queued", index=True)
    worker_lease: Mapped[str] = mapped_column(String(80), nullable=False, default="")
    attempts: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    last_error: Mapped[str] = mapped_column(Text, nullable=False, default="")
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now_utc, nullable=False)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now_utc, onupdate=now_utc, nullable=False)


class Stage6DeliveryOperation(Base):
    __tablename__ = "stage6_delivery_operations"
    __table_args__ = (Index("ix_stage6_op_kind_key", "operation_kind", "idempotency_key", unique=True),)

    operation_id: Mapped[str] = mapped_column(String(80), primary_key=True, default=lambda: str(uuid4()))
    esr_id: Mapped[str] = mapped_column(String(80), nullable=False, index=True)
    operation_kind: Mapped[str] = mapped_column(String(64), nullable=False)
    idempotency_key: Mapped[str] = mapped_column(String(160), nullable=False)
    status: Mapped[str] = mapped_column(String(32), nullable=False, default="completed")
    result_summary: Mapped[dict[str, Any]] = mapped_column(JsonType, nullable=False, default=dict)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now_utc, nullable=False)
