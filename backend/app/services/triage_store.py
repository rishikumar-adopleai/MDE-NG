"""Database-backed Stage 2 triage coordination, normalized dossier persistence, and audit storage."""

from __future__ import annotations

import threading
from contextlib import contextmanager
from typing import Any
from uuid import uuid4

from sqlalchemy import delete, select, update
from sqlalchemy.orm import Session

from app.models import (
    Stage2ClinicalFact,
    Stage2ContentionSummary,
    Stage2EvidenceVersion,
    Stage2ProcessingAudit,
    Stage2TriageDossier,
    Stage2TriageJob,
    now_utc,
)
from app.services.triage_pipeline import compute_triage_digest

_ESR_LOCKS_GUARD = threading.Lock()
_ESR_LOCKS: dict[str, threading.Lock] = {}


@contextmanager
def esr_triage_lock(esr_id: str):
    """Serialize in-process Stage 2 preparation per ESR without blocking unrelated ESRs."""
    with _ESR_LOCKS_GUARD:
        lock = _ESR_LOCKS.setdefault(esr_id, threading.Lock())
    lock.acquire()
    try:
        yield
    finally:
        lock.release()


def compute_current_esr_digest(state: dict, esr_id: str) -> str:
    esr = (state.get("esrsById") or {}).get(esr_id)
    if not esr:
        return ""
    docs_map = state.get("evidenceDocumentsById") or {}
    conts_map = state.get("contentionsById") or {}
    documents = [docs_map[did] for did in esr.get("evidenceDocumentIds", []) if did in docs_map]
    contentions = [conts_map[cid] for cid in esr.get("contentionIds", []) if cid in conts_map]
    return compute_triage_digest(esr, documents, contentions)


def register_or_reuse_triage_job(
    db: Session,
    esr_id: str,
    evidence_digest: str,
    worker_lease: str = "",
) -> tuple[Stage2TriageJob, bool]:
    """Ensure at most one active Stage 2 job exists per ESR and evidence version."""
    pending = [
        obj for obj in db.new
        if isinstance(obj, Stage2TriageJob)
        and obj.esr_id == esr_id
        and obj.status in ("queued", "processing")
    ]
    persisted = db.execute(
        select(Stage2TriageJob).where(
            Stage2TriageJob.esr_id == esr_id,
            Stage2TriageJob.status.in_(("queued", "processing")),
        )
    ).scalars().all()
    for row in [*pending, *persisted]:
        if not evidence_digest or row.evidence_digest == evidence_digest:
            return row, False
        row.status = "superseded"
        row.updated_at = now_utc()

    job = Stage2TriageJob(
        job_id=f"triage-job-{uuid4().hex[:16]}",
        esr_id=esr_id,
        evidence_digest=evidence_digest,
        status="processing" if worker_lease else "queued",
        worker_lease=worker_lease,
        attempts=1 if worker_lease else 0,
    )
    db.add(job)
    db.flush()
    return job, True


def finalize_triage_job(
    db: Session,
    job_id: str,
    status: str,
    *,
    last_error: str = "",
) -> None:
    db.execute(
        update(Stage2TriageJob)
        .where(Stage2TriageJob.job_id == job_id)
        .values(status=status, last_error=last_error[:500], updated_at=now_utc())
    )


def persist_stage2_normalized(
    db: Session,
    esr_id: str,
    dossier: dict[str, Any],
    facts: list[dict[str, Any]],
    *,
    document_count: int = 0,
    is_stale: bool = False,
) -> None:
    """Persist Stage 2 facts, dossier, contention summaries, evidence version, and audit rows."""
    digest = str(dossier.get("sourceDigest") or "")
    now = now_utc()

    db.execute(delete(Stage2ClinicalFact).where(Stage2ClinicalFact.esr_id == esr_id))
    for fact in facts:
        fid = str(fact.get("id") or "")
        if not fid:
            continue
        db.add(Stage2ClinicalFact(
            fact_id=fid,
            esr_id=esr_id,
            document_id=str(fact.get("documentId") or ""),
            source_hash=str(fact.get("sourceHash") or ""),
            fact_json=fact,
            updated_at=now,
        ))

    existing_dossier = db.get(Stage2TriageDossier, esr_id)
    if existing_dossier:
        existing_dossier.source_digest = digest
        existing_dossier.ai_provider = str(dossier.get("aiProvider") or "local")
        existing_dossier.case_overview = str(dossier.get("caseOverview") or "")
        existing_dossier.is_stale = 1 if is_stale else 0
        existing_dossier.dossier_json = dossier
        existing_dossier.updated_at = now
    else:
        db.add(Stage2TriageDossier(
            esr_id=esr_id,
            source_digest=digest,
            ai_provider=str(dossier.get("aiProvider") or "local"),
            case_overview=str(dossier.get("caseOverview") or ""),
            is_stale=1 if is_stale else 0,
            dossier_json=dossier,
            updated_at=now,
        ))

    db.execute(delete(Stage2ContentionSummary).where(Stage2ContentionSummary.esr_id == esr_id))
    for cid, packet in (dossier.get("contentionsById") or {}).items():
        db.add(Stage2ContentionSummary(
            contention_id=cid,
            esr_id=esr_id,
            source_digest=digest,
            summary_provider=str(packet.get("summaryProvider") or "local"),
            summary_json=packet,
            updated_at=now,
        ))

    existing_ver = db.get(Stage2EvidenceVersion, esr_id)
    if existing_ver:
        existing_ver.evidence_digest = digest
        existing_ver.document_count = document_count
        existing_ver.fact_count = len(facts)
        existing_ver.updated_at = now
    else:
        db.add(Stage2EvidenceVersion(
            esr_id=esr_id,
            evidence_digest=digest,
            document_count=document_count,
            fact_count=len(facts),
            updated_at=now,
        ))

    audit = dossier.get("processingAudit") or {}
    router_audit = audit.get("router") or {}
    db.add(Stage2ProcessingAudit(
        id=f"audit-{uuid4().hex[:16]}",
        esr_id=esr_id,
        evidence_digest=digest,
        provider=str(dossier.get("aiProvider") or "local"),
        retries=int(router_audit.get("retries") or 0),
        failover_triggered=1 if router_audit.get("failoverTriggered") else 0,
        fallback_used=1 if dossier.get("aiUnavailable") else 0,
        audit_json=audit,
        created_at=now,
    ))


def merge_stage2_result_into_state(
    state: dict,
    esr_id: str,
    computed_state: dict,
    dossier: dict[str, Any] | None,
    now: str,
    *,
    mark_stale: bool = False,
) -> None:
    """Apply ONLY one ESR's computed clinical facts & dossier onto the latest snapshot state."""
    from app.services.intake_store import touch

    esr = (state.get("esrsById") or {}).get(esr_id)
    if esr is None:
        return
    if dossier:
        facts_store = state.setdefault("clinicalFactsById", {})
        for k in [key for key, row in facts_store.items() if row.get("esrId") == esr_id]:
            del facts_store[k]
        for k, row in (computed_state.get("clinicalFactsById") or {}).items():
            if row.get("esrId") == esr_id:
                facts_store[k] = row
        state.setdefault("triageDossiersByEsrId", {})[esr_id] = dossier
        touch(
            esr,
            now,
            triageProcessing={
                "status": "stale" if mark_stale else "completed",
                "completedAt": now,
                "sourceDigest": dossier.get("sourceDigest", ""),
                "factCount": dossier.get("factCount", 0),
                "aiProvider": dossier.get("aiProvider", "local"),
                "ruleVersion": dossier.get("ruleVersion", ""),
                "isStale": mark_stale,
            },
        )
    else:
        touch(esr, now, triageProcessing={"status": "completed", "completedAt": now})
