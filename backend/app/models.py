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
