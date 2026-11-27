"""Stage 5 Internal QA LangGraph + AgenticModelRouter Clinical Audit Pipeline."""

from __future__ import annotations

import hashlib
import json
import logging
import re
from datetime import UTC, datetime
from typing import Any, Literal, TypedDict
from uuid import uuid4

from fastapi import HTTPException
from langgraph.graph import END, START, StateGraph
from pydantic import BaseModel, ConfigDict, Field
from sqlalchemy import func, select, update
from sqlalchemy.orm import Session

from app.core.config import settings
from app.core.database import SessionLocal
from app.models import AuditEvent, OutboxJob, Stage5QAAuditJob
from app.services.agent_prompts import (
    STAGE5_QA_AUDIT_PROMPT,
    STAGE5_QA_AUDIT_REPAIR_PROMPT,
)
from app.services.intake_processing import (
    azure_review_completion,
    get_last_completion_audit,
    get_last_completion_provider,
)
from app.services.qa_rules import (
    QA_RULES_VERSION,
    compute_qa_evidence_digest,
    run_deterministic_qa_rules,
)

logger = logging.getLogger(__name__)

QA_TASK_NAME = "qa.audit_report"
MAX_DBQ_FIELD_CHARS = 8000
MAX_SOURCE_SLICE_CHARS = 10000
MAX_QA_BATCH_CHARS = 56000
MAX_SOURCES_PER_BATCH = 48

_TOKEN_RE = re.compile(r"[a-z0-9]{3,}", re.IGNORECASE)
_STOPWORDS = frozenset({
    "the", "and", "for", "with", "that", "this", "from", "was", "were", "are",
    "has", "have", "had", "not", "but", "into", "onto", "than", "then", "also",
    "such", "been", "when", "where", "which", "while", "during", "after",
    "before", "about", "between", "under", "over", "claimant", "veteran",
    "report", "exam", "examination", "dbq", "record", "records", "evidence",
    "clinical", "medical", "findings", "history", "remarks", "stage", "review",
})


def _now_iso() -> str:
    return datetime.now(UTC).isoformat().replace("+00:00", "Z")


def record_qa_audit_event(
    db: Session | None,
    actor_id: str,
    action: str,
    object_ref: str,
    detail: dict[str, Any],
) -> None:
    """Persist strictly non-PHI Stage 5 audit telemetry into AuditEvent."""
    if db is None:
        return
    try:
        sanitized = {
            k: v
            for k, v in (detail or {}).items()
            if k not in {"text", "excerpt", "values", "clinicalSummary", "veteranName", "patientName", "rawContent", "prompt"}
        }
        next_seq = int(db.scalar(select(func.coalesce(func.max(AuditEvent.sequence), 0))) or 0) + 1
        db.add(AuditEvent(
            sequence=next_seq,
            actor_id=str(actor_id or "system-qa")[:64],
            action=str(action)[:64],
            object_ref=str(object_ref)[:64],
            detail=json.dumps(sanitized, sort_keys=True)[:3900],
        ))
    except Exception:
        logger.warning("Stage 5 failed to record AuditEvent action=%s ref=%s", action, object_ref)


class QAAuditWarningModel(BaseModel):
    model_config = ConfigDict(extra="forbid")

    title: str
    sectionReference: str
    issueCategory: Literal[
        "missing-evidence",
        "clinical-contradiction",
        "form-version",
        "evidence-consistency",
        "missing-signature",
        "forbidden-content",
        "diagnostic-gap",
        "safety-24h",
        "ace-remarks",
        "credentials",
        "advisory",
    ]
    severity: Literal["review", "advisory"]
    rationale: str
    sourceIds: list[str] = Field(default_factory=list)


class QAAuditLLMResult(BaseModel):
    model_config = ConfigDict(extra="forbid")

    summary: str
    warnings: list[QAAuditWarningModel] = Field(default_factory=list)


class QAAuditGraphState(TypedDict, total=False):
    context: dict[str, Any]
    valid_source_ids: list[str]
    source_lookup: dict[str, dict[str, Any]]
    attempt: int
    max_attempts: int
    raw_output: str
    parsed: dict[str, Any] | None
    errors: list[str]
    provider: str | None
    audit_meta: dict[str, Any]
    status: Literal["completed", "failed"]


def _qa_audit_schema() -> dict[str, Any]:
    return {
        "type": "object",
        "additionalProperties": False,
        "properties": {
            "summary": {"type": "string"},
            "warnings": {
                "type": "array",
                "items": {
                    "type": "object",
                    "additionalProperties": False,
                    "properties": {
                        "title": {"type": "string"},
                        "sectionReference": {"type": "string"},
                        "issueCategory": {
                            "type": "string",
                            "enum": [
                                "missing-evidence",
                                "clinical-contradiction",
                                "form-version",
                                "evidence-consistency",
                                "missing-signature",
                                "forbidden-content",
                                "diagnostic-gap",
                                "safety-24h",
                                "ace-remarks",
                                "credentials",
                                "advisory",
                            ],
                        },
                        "severity": {"type": "string", "enum": ["review", "advisory"]},
                        "rationale": {"type": "string"},
                        "sourceIds": {"type": "array", "items": {"type": "string"}},
                    },
                    "required": ["title", "sectionReference", "issueCategory", "severity", "rationale", "sourceIds"],
                },
            },
        },
        "required": ["summary", "warnings"],
    }


def _semantic_tokens(text: str) -> set[str]:
    return {t.lower() for t in _TOKEN_RE.findall(str(text or "")) if t.lower() not in _STOPWORDS}


def _collect_qa_evidence_inventory(
    state: dict,
    review: dict,
    dbq: dict,
    version: dict,
) -> tuple[dict[str, Any], list[dict[str, Any]], dict[str, Any]]:
    """Reuse Stage 1-4 structured records to build complete, non-truncated QA context & coverage."""
    esr_id = str(review.get("esrId") or dbq.get("esrId") or "")
    esr = (state.get("esrsById") or {}).get(esr_id) or {}
    veteran = (state.get("veteransById") or {}).get(esr.get("veteranId") or "") or {}
    exam = (state.get("examinationsById") or {}).get(dbq.get("examinationId") or "") or {}
    req_id = (exam.get("requirementIds") or [None])[-1]
    req = (state.get("examinationRequirementsById") or {}).get(req_id or "") or {}
    dossier = (state.get("triageDossiersByEsrId") or {}).get(esr_id) or {}
    contentions_by_id = state.get("contentionsById") or {}
    docs_by_id = state.get("evidenceDocumentsById") or {}
    facts_by_id = state.get("clinicalFactsById") or {}
    orders_by_id = state.get("diagnosticOrdersById") or {}
    results_by_id = state.get("diagnosticResultsById") or {}
    appts_by_id = state.get("appointmentsById") or {}

    appt = next(
        (
            appts_by_id.get(aid)
            for aid in (exam.get("appointmentIds") or [])
            if (appts_by_id.get(aid) or {}).get("status") in {"completed", "confirmed-locally", "confirmed"}
        ),
        None,
    )

    contention_ids = [str(cid) for cid in (dbq.get("contentionIds") or []) if cid in contentions_by_id]
    contention_set = set(contention_ids)

    # Bound huge per-field strings generously (8k vs prior arbitrary small caps)
    locked_values = {
        k: (str(v)[:MAX_DBQ_FIELD_CHARS] if isinstance(v, str) else v)
        for k, v in (version.get("values") or {}).items()
    }

    relevant_contentions = [
        {
            "contentionId": cid,
            "condition": contentions_by_id[cid].get("condition"),
            "laterality": contentions_by_id[cid].get("laterality"),
            "requestedDbqNames": contentions_by_id[cid].get("requestedDbqNames") or [],
            "specialInstructions": contentions_by_id[cid].get("specialInstructions") or [],
            "collectDuringExam": contentions_by_id[cid].get("collectDuringExam") or [],
            "triageDraft": contentions_by_id[cid].get("triageDraft") or {},
        }
        for cid in contention_ids
    ]

    stage4_draft_review = (exam.get("stage4") or {}).get("draftMedicalReview") or None

    core_context: dict[str, Any] = {
        "esrSummary": {
            "esrId": esr_id,
            "veteranId": veteran.get("id") or esr.get("veteranId"),
            "veteranName": veteran.get("displayName") or veteran.get("fullName"),
            "programFlags": dossier.get("programFlags") or {},
            "examPathway": (req.get("selectedModality") or (appt or {}).get("modality")),
            "scheduledStart": (appt or {}).get("startsAt"),
            "assignedExaminerId": (appt or {}).get("examinerId") or exam.get("examinerId"),
            "signingClinicianId": (dbq.get("localAttestation") or {}).get("staffActorId"),
        },
        "dbqMeta": {
            "dbqId": dbq.get("id"),
            "formType": dbq.get("formType"),
            "formVersion": dbq.get("formVersion"),
            "versionId": version.get("id"),
            "contentionIds": contention_ids,
        },
        "contentions": relevant_contentions,
        "lockedValues": locked_values,
        "stage4MedicalReview": stage4_draft_review,
        "deterministicScorecard": [
            {
                "ruleId": c.get("ruleId"),
                "key": c.get("key"),
                "status": c.get("status"),
                "severity": c.get("severity"),
                "detail": c.get("detail"),
            }
            for c in (review.get("checks") or [])
        ],
    }

    sources: list[dict[str, Any]] = []
    seen_fingerprints: set[str] = set()
    available_count = 0
    processed_count = 0
    relevant_count = 0
    human_inspect_count = 0
    unavailable_count = 0

    def _add_source(
        source_id: str,
        kind: str,
        title: str,
        text: str,
        *,
        document_id: str | None = None,
        page_range: str | None = None,
        contention_id: str | None = None,
        metadata: dict[str, Any] | None = None,
        is_relevant: bool = True,
    ) -> None:
        nonlocal processed_count, relevant_count
        cleaned = str(text or "").strip()
        if not cleaned:
            return
        norm_text = re.sub(r"\s+", " ", cleaned.lower())
        fp_key = f"{kind}|{document_id or ''}|{page_range or ''}|{contention_id or ''}|{norm_text[:300]}"
        fp = hashlib.sha1(fp_key.encode("utf-8", errors="replace")).hexdigest()
        if fp in seen_fingerprints:
            return
        seen_fingerprints.add(fp)
        processed_count += 1
        if is_relevant:
            relevant_count += 1
        entry: dict[str, Any] = {
            "sourceId": source_id,
            "kind": kind,
            "title": title,
            "text": cleaned,
        }
        if document_id:
            entry["documentId"] = document_id
        if page_range:
            entry["pageRange"] = page_range
        if contention_id:
            entry["contentionId"] = contention_id
        if metadata:
            entry["metadata"] = metadata
        sources.append(entry)

    # 1. Stage 2 Clinical Facts (relevant to contention first, then all ESR case history/contradictions/chronology)
    fact_rows = [
        f for f in facts_by_id.values()
        if str(f.get("esrId") or "") == esr_id
    ]
    fact_rows.sort(
        key=lambda f: (
            0 if str(f.get("contentionId") or "") in contention_set else 1,
            str(f.get("date") or f.get("createdAt") or ""),
            str(f.get("id") or ""),
        )
    )
    for idx, f in enumerate(fact_rows, 1):
        available_count += 1
        is_rel = (str(f.get("contentionId") or "") in contention_set) or not f.get("contentionId")
        fact_payload = {
            "factType": f.get("factType") or f.get("category"),
            "label": f.get("label") or f.get("statement") or f.get("finding"),
            "laterality": f.get("laterality"),
            "date": f.get("date"),
            "measurement": f.get("measurement") or f.get("value"),
            "unit": f.get("unit"),
            "negation": f.get("negated") or f.get("negation"),
            "uncertainty": f.get("uncertain") or f.get("uncertainty"),
            "contradictory": f.get("contradictory"),
            "clinicalContext": f.get("context") or f.get("clinicalContext"),
            "sourceDocumentId": f.get("documentId") or f.get("sourceDocumentId"),
            "page": f.get("page") or f.get("pageNumber"),
        }
        _add_source(
            f"FACT-{f.get('id') or idx}",
            "stage2-clinical-fact",
            f"Stage 2 Fact: {fact_payload['label'] or f.get('id')}",
            json.dumps(fact_payload, default=str),
            document_id=str(f.get("documentId") or f.get("sourceDocumentId") or "") or None,
            page_range=str(f.get("page") or "") or None,
            contention_id=str(f.get("contentionId") or "") or None,
            is_relevant=is_rel,
        )

    # 2. Stage 2 Unresolved Clinical Gaps & Distress Alerts
    for idx, gap in enumerate(dossier.get("evidenceGaps") or [], 1):
        available_count += 1
        g_cid = str(gap.get("contentionId") or "")
        is_rel = (not g_cid) or (g_cid in contention_set)
        _add_source(
            f"GAP-{gap.get('id') or idx}",
            "stage2-evidence-gap",
            f"Stage 2 Gap: {gap.get('title') or gap.get('kind') or idx}",
            json.dumps(gap, default=str),
            contention_id=g_cid or None,
            is_relevant=is_rel,
        )

    for idx, alert in enumerate(dossier.get("distressAlerts") or [], 1):
        available_count += 1
        _add_source(
            f"SAFETY-{alert.get('id') or idx}",
            "stage2-safety-alert",
            f"Stage 2 Safety Alert ({alert.get('severity') or 'info'})",
            json.dumps(alert, default=str),
            is_relevant=True,
        )

    # 3. Stage 4 Diagnostic Orders & Results (excluding unverified/uncertain/raw psych)
    for oid in exam.get("diagnosticOrderIds") or []:
        order = orders_by_id.get(oid)
        if not order:
            continue
        available_count += 1
        is_rel = True
        if order.get("exceptionReason"):
            _add_source(
                f"DIAG-ORD-{oid}",
                "diagnostic-exception",
                f"Diagnostic Exception ({order.get('testName') or oid})",
                json.dumps({
                    "orderId": oid,
                    "testName": order.get("testName"),
                    "status": order.get("status"),
                    "exceptionReason": order.get("exceptionReason"),
                    "exceptionNote": order.get("exceptionNote"),
                }),
                is_relevant=True,
            )
            continue
        result = results_by_id.get(order.get("resultId") or "")
        if not result:
            unavailable_count += 1
            human_inspect_count += 1
            continue
        if result.get("containsRestrictedRawMaterial") or result.get("classificationUncertain"):
            human_inspect_count += 1
            continue
        if not result.get("reviewedAt"):
            human_inspect_count += 1
        _add_source(
            f"DIAG-{result.get('id') or oid}",
            "diagnostic-result",
            f"Diagnostic: {order.get('testName') or result.get('sourceReference') or oid}",
            json.dumps({
                "orderId": oid,
                "resultId": result.get("id"),
                "testName": order.get("testName"),
                "reportedFlag": result.get("reportedFlag") or "normal",
                "summary": result.get("summary") or "",
                "reviewedAt": result.get("reviewedAt"),
                "reviewedBy": result.get("reviewedBy"),
                "identifiedDuringExam": result.get("identifiedDuringExam"),
                "patientNotifiedAt": result.get("patientNotifiedAt"),
                "incidentReportLogged": result.get("incidentReportLogged"),
                "sourceReference": result.get("sourceReference"),
            }),
            document_id=str(result.get("uploadedDocumentId") or "") or None,
            is_relevant=is_rel,
        )

    # 4. ALL Linked Stage 1 / Stage 2 Medical Evidence Documents (no 14-doc or 1,800-char limit!)
    linked_doc_ids = list(dict.fromkeys([
        *[
            did
            for cid in contention_ids
            for did in ((contentions_by_id.get(cid) or {}).get("evidenceDocumentIds") or [])
        ],
        *[
            str(f.get("documentId") or f.get("sourceDocumentId"))
            for f in fact_rows
            if f.get("documentId") or f.get("sourceDocumentId")
        ],
        *[str(did) for did in (esr.get("evidenceDocumentIds") or [])],
    ]))

    doc_counter = 0
    allowed_doc_esr_ids = {esr_id, str(esr.get("linkedOriginalEsrId") or "")} - {""}
    for did in linked_doc_ids:
        doc = docs_by_id.get(did)
        if not doc or doc.get("esrId") not in allowed_doc_esr_ids or doc.get("classificationStatus") == "excluded":
            continue
        available_count += 1
        doc_cids = set(str(x) for x in (doc.get("contentionIds") or []))
        is_rel = bool(doc_cids & contention_set) or (not doc_cids)

        if doc.get("containsRestrictedRawMaterial") or doc.get("classificationUncertain"):
            human_inspect_count += 1
            continue
        if doc.get("classificationStatus") in {"queued", "processing"}:
            unavailable_count += 1
            human_inspect_count += 1
            continue
        if int(doc.get("unreadablePageCount") or 0) > 0:
            unavailable_count += 1
            human_inspect_count += 1

        pages = doc.get("extractedPages") or []
        if pages:
            doc_counter += 1
            # Pack sequential pages into non-truncated slices up to MAX_SOURCE_SLICE_CHARS
            buf: list[str] = []
            buf_chars = 0
            start_page = 1
            last_page = 1
            slice_idx = 0
            for p_idx, page in enumerate(pages, 1):
                if isinstance(page, dict):
                    p_num = page.get("pageNumber") or p_idx
                    p_text = str(page.get("text") or page.get("content") or "").strip()
                    p_unreadable = bool(page.get("unreadable"))
                else:
                    p_num = p_idx
                    p_text = str(page or "").strip()
                    p_unreadable = False
                if p_unreadable or not p_text:
                    continue
                formatted = f"[Page {p_num}] {p_text}"
                if buf and (buf_chars + len(formatted) > MAX_SOURCE_SLICE_CHARS):
                    slice_idx += 1
                    sid = f"S{doc_counter}" if slice_idx == 1 else f"S{doc_counter}-p{start_page}"
                    _add_source(
                        sid,
                        f"efolder-{doc.get('category') or 'document'}",
                        f"{doc.get('title') or did} (pages {start_page}-{last_page})",
                        "\n".join(buf),
                        document_id=did,
                        page_range=f"{start_page}-{last_page}",
                        metadata={"category": doc.get("category"), "classificationStatus": doc.get("classificationStatus")},
                        is_relevant=is_rel,
                    )
                    buf = []
                    buf_chars = 0
                    start_page = p_num
                if not buf:
                    start_page = p_num
                buf.append(formatted)
                buf_chars += len(formatted)
                last_page = p_num
            if buf:
                slice_idx += 1
                sid = f"S{doc_counter}" if slice_idx == 1 else f"S{doc_counter}-p{start_page}"
                _add_source(
                    sid,
                    f"efolder-{doc.get('category') or 'document'}",
                    f"{doc.get('title') or did} (pages {start_page}-{last_page})",
                    "\n".join(buf),
                    document_id=did,
                    page_range=f"{start_page}-{last_page}",
                    metadata={"category": doc.get("category"), "classificationStatus": doc.get("classificationStatus")},
                    is_relevant=is_rel,
                )
            elif int(doc.get("classifiedPageCount") or 0) <= 0:
                unavailable_count += 1
                human_inspect_count += 1
        else:
            raw_text = str(doc.get("extractedText") or doc.get("summary") or doc.get("sourceReference") or "").strip()
            if not raw_text:
                unavailable_count += 1
                human_inspect_count += 1
                continue
            doc_counter += 1
            if len(raw_text) <= MAX_SOURCE_SLICE_CHARS:
                _add_source(
                    f"S{doc_counter}",
                    f"efolder-{doc.get('category') or 'document'}",
                    str(doc.get("title") or did),
                    raw_text,
                    document_id=did,
                    metadata={"category": doc.get("category")},
                    is_relevant=is_rel,
                )
            else:
                for offset in range(0, len(raw_text), MAX_SOURCE_SLICE_CHARS):
                    part = (offset // MAX_SOURCE_SLICE_CHARS) + 1
                    sid = f"S{doc_counter}" if part == 1 else f"S{doc_counter}-{part}"
                    _add_source(
                        sid,
                        f"efolder-{doc.get('category') or 'document'}",
                        f"{doc.get('title') or did} (part {part})",
                        raw_text[offset : offset + MAX_SOURCE_SLICE_CHARS],
                        document_id=did,
                        metadata={"category": doc.get("category")},
                        is_relevant=is_rel,
                    )

    coverage = {
        "availableCount": available_count,
        "processedCount": processed_count,
        "relevantToExamCount": relevant_count,
        "deterministicReviewedCount": processed_count,
        "aiReviewedCount": 0,
        "humanInspectionRequiredCount": human_inspect_count,
        "unavailableOrUnreadableCount": unavailable_count,
        "batchesPlanned": 0,
        "batchesSucceeded": 0,
        "completeCoverage": False,
        "computedAt": _now_iso(),
    }
    return core_context, sources, coverage


def _build_qa_evidence_batches(
    core_context: dict[str, Any],
    sources: list[dict[str, Any]],
) -> list[list[dict[str, Any]]]:
    """Partition evidence sources into bounded multi-batch packets without silently dropping any item."""
    if not sources:
        return [[]]

    header_size = len(json.dumps(core_context, default=str))
    budget = max(12000, MAX_QA_BATCH_CHARS - min(header_size, MAX_QA_BATCH_CHARS // 2))
    batches: list[list[dict[str, Any]]] = []
    cur: list[dict[str, Any]] = []
    cur_chars = 0
    for src in sources:
        src_size = len(json.dumps(src, default=str))
        if cur and (cur_chars + src_size > budget or len(cur) >= MAX_SOURCES_PER_BATCH):
            batches.append(cur)
            cur = []
            cur_chars = 0
        cur.append(src)
        cur_chars += src_size
    if cur:
        batches.append(cur)
    return batches


def _validate_qa_audit_payload(
    raw_text: str,
    valid_source_ids: set[str],
    source_lookup: dict[str, dict[str, Any]] | None = None,
) -> tuple[dict[str, Any] | None, list[str]]:
    errors: list[str] = []
    cleaned = str(raw_text or "").strip()
    if cleaned.startswith("```"):
        cleaned = re.sub(r"^```(?:json)?\s*|\s*```$", "", cleaned, flags=re.IGNORECASE | re.DOTALL).strip()
    try:
        data = json.loads(cleaned)
    except Exception as exc:
        return None, [f"Invalid JSON: {exc}"]

    try:
        validated = QAAuditLLMResult.model_validate(data).model_dump()
    except Exception as exc:
        return None, [f"Schema error: {exc}"]

    for idx, w in enumerate(validated.get("warnings") or []):
        if w.get("severity") not in {"review", "advisory"}:
            errors.append(f"warnings[{idx}].severity must be 'review' or 'advisory'")
        cited = list(w.get("sourceIds") or [])
        bad_ids = [sid for sid in cited if sid not in valid_source_ids]
        if bad_ids:
            errors.append(f"warnings[{idx}].sourceIds contains invalid IDs: {bad_ids}")
            continue
        if source_lookup and cited:
            empty_refs = [sid for sid in cited if not str((source_lookup.get(sid) or {}).get("text") or "").strip()]
            if empty_refs:
                errors.append(f"warnings[{idx}].sourceIds reference empty records: {empty_refs}")

    if errors:
        return None, errors
    return validated, []


def _execute_guarded_audit_batch(state: QAAuditGraphState) -> QAAuditGraphState:
    attempt = int(state.get("attempt") or 0) + 1
    ctx = state["context"]
    errors = list(state.get("errors") or [])
    base_prompt = STAGE5_QA_AUDIT_PROMPT if attempt == 1 else f"{STAGE5_QA_AUDIT_REPAIR_PROMPT}\n\nErrors to fix:\n" + "\n".join(errors)
    payload_input = {
        "batchIndex": ctx.get("batchIndex", 1),
        "batchTotal": ctx.get("batchTotal", 1),
        "validSourceIds": state.get("valid_source_ids") or [],
        "esrSummary": ctx.get("esrSummary") or {},
        "dbqMeta": ctx.get("dbqMeta") or {},
        "contentions": ctx.get("contentions") or [],
        "lockedValues": ctx.get("lockedValues") or {},
        "stage4MedicalReview": ctx.get("stage4MedicalReview"),
        "deterministicScorecard": ctx.get("deterministicScorecard") or [],
        "eFolderExcerpts": ctx.get("eFolderExcerpts") or [],
        "stage2Facts": ctx.get("stage2Facts") or [],
        "stage2EvidenceGaps": ctx.get("stage2EvidenceGaps") or [],
    }
    try:
        raw = azure_review_completion(
            "stage5_qa_clinical_audit",
            _qa_audit_schema(),
            base_prompt,
            [{"sourceId": "QA-CONTEXT", "text": json.dumps(payload_input, default=str)}],
            max_tokens=1000,
        )
        return {
            "attempt": attempt,
            "raw_output": raw,
            "provider": get_last_completion_provider(),
            "audit_meta": get_last_completion_audit(),
        }
    except Exception as exc:
        logger.warning("Stage 5 QA LLM execution failed on attempt=%s", attempt)
        return {
            "attempt": attempt,
            "raw_output": "",
            "errors": [type(exc).__name__],
            "provider": None,
            "audit_meta": get_last_completion_audit(),
            "status": "failed",
        }


def _validate_guarded_audit_node(state: QAAuditGraphState) -> QAAuditGraphState:
    if state.get("status") == "failed" and not state.get("raw_output"):
        return {"parsed": None, "status": "failed"}
    valid_ids = set(state.get("valid_source_ids") or [])
    parsed, errs = _validate_qa_audit_payload(
        state.get("raw_output") or "",
        valid_ids,
        state.get("source_lookup"),
    )
    if parsed is not None:
        return {"parsed": parsed, "errors": [], "status": "completed"}
    return {"parsed": None, "errors": errs}


def _route_guarded_audit(state: QAAuditGraphState) -> str:
    if state.get("parsed") is not None:
        return "done"
    if int(state.get("attempt") or 0) >= int(state.get("max_attempts") or 2) + 1:
        return "done"
    if state.get("status") == "failed" and not state.get("raw_output"):
        return "done"
    return "repair"


_qa_graph_builder = StateGraph(QAAuditGraphState)
_qa_graph_builder.add_node("generate", _execute_guarded_audit_batch)
_qa_graph_builder.add_node("validate", _validate_guarded_audit_node)
_qa_graph_builder.add_edge(START, "generate")
_qa_graph_builder.add_edge("generate", "validate")
_qa_graph_builder.add_conditional_edges(
    "validate",
    _route_guarded_audit,
    {"repair": "generate", "done": END},
)
_QA_AUDIT_GRAPH = _qa_graph_builder.compile()


def run_guarded_qa_audit_llm(context: dict, max_repair_attempts: int = 2) -> dict[str, Any]:
    """Run one LangGraph-validated + bounded-repair Stage 5 clinical audit pass."""
    sources: list[dict[str, Any]] = []
    if context.get("eFolderExcerpts"):
        for ex in context["eFolderExcerpts"]:
            sources.append(ex)
    if context.get("stage2Facts"):
        for f in context["stage2Facts"]:
            if isinstance(f, dict) and f.get("sourceId"):
                sources.append(f)
    if context.get("stage2EvidenceGaps"):
        for g in context["stage2EvidenceGaps"]:
            if isinstance(g, dict) and g.get("sourceId"):
                sources.append(g)

    valid_ids: list[str] = list(dict.fromkeys(
        str(s["sourceId"]) for s in sources if isinstance(s, dict) and s.get("sourceId")
    ))
    source_lookup = {
        str(s["sourceId"]): s for s in sources if isinstance(s, dict) and s.get("sourceId")
    }
    if not valid_ids and context.get("validSourceIds"):
        valid_ids = [str(x) for x in context["validSourceIds"]]

    out = _QA_AUDIT_GRAPH.invoke({
        "context": context,
        "valid_source_ids": valid_ids,
        "source_lookup": source_lookup,
        "attempt": 0,
        "max_attempts": max_repair_attempts,
        "errors": [],
    })
    parsed = out.get("parsed")
    if parsed:
        return {
            "ok": True,
            "result": parsed,
            "provider": out.get("provider"),
            "auditMeta": out.get("audit_meta") or {},
            "attempts": out.get("attempt") or 1,
        }
    return {
        "ok": False,
        "result": None,
        "errors": out.get("errors") or ["LLM audit failed validation"],
        "provider": out.get("provider"),
        "auditMeta": out.get("audit_meta") or {},
        "attempts": out.get("attempt") or 1,
    }


def _reconcile_batch_results(batch_outputs: list[dict[str, Any]], batches_planned: int) -> dict[str, Any]:
    """Merge multi-batch QA LLM warnings with semantic deduplication & explicit partial/failure tracking."""
    merged_warnings: dict[tuple[str, str, str], dict[str, Any]] = {}
    summaries: list[str] = []
    providers: list[str] = []
    fallback_used = False
    batches_succeeded = 0
    errors: list[str] = []
    covered_source_ids: set[str] = set()

    for b in batch_outputs:
        if b.get("ok") and isinstance(b.get("result"), dict):
            batches_succeeded += 1
            covered_source_ids.update(b.get("batchSourceIds") or [])
            if b.get("provider"):
                providers.append(str(b["provider"]))
            if (b.get("auditMeta") or {}).get("fallbackUsed"):
                fallback_used = True
            s_text = str(b["result"].get("summary") or "").strip()
            if s_text and s_text not in summaries:
                summaries.append(s_text)
            for w in b["result"].get("warnings") or []:
                sec = str(w.get("sectionReference") or "").strip().lower()
                cat = str(w.get("issueCategory") or "advisory").strip().lower()
                title_sig = " ".join(sorted(_semantic_tokens(w.get("title") or ""))[:6])
                dedup_key = (sec, cat, title_sig or str(w.get("title") or "").strip().lower())
                if dedup_key not in merged_warnings:
                    merged_warnings[dedup_key] = dict(w)
                else:
                    existing = merged_warnings[dedup_key]
                    existing["sourceIds"] = list(dict.fromkeys([
                        *(existing.get("sourceIds") or []),
                        *(w.get("sourceIds") or []),
                    ]))
                    if w.get("severity") == "review":
                        existing["severity"] = "review"
                    if len(str(w.get("rationale") or "")) > len(str(existing.get("rationale") or "")):
                        existing["rationale"] = w["rationale"]
        else:
            errors.extend(b.get("errors") or ["Batch execution failed"])

    if batches_succeeded == 0:
        status = "failed"
    elif batches_succeeded < batches_planned:
        status = "partial"
    else:
        status = "completed"

    return {
        "status": status,
        "summary": " ".join(summaries)[:2500] if summaries else "",
        "warnings": list(merged_warnings.values()),
        "providers": list(dict.fromkeys(providers)),
        "fallbackUsed": fallback_used,
        "batchesPlanned": batches_planned,
        "batchesSucceeded": batches_succeeded,
        "errors": errors[:10],
        "coveredSourceIds": covered_source_ids,
    }


def _sync_ai_warnings_into_findings(
    state: dict,
    review: dict,
    dbq: dict,
    version: dict,
    warnings: list[dict[str, Any]],
    source_lookup: dict[str, dict[str, Any]],
    now: str,
) -> list[str]:
    qa_findings = state.setdefault("qaFindingsById", {})
    finding_ids = list(review.get("findingIds") or [])

    # Close stale open ai-audit findings for this review before inserting current verified run warnings
    for fid in finding_ids:
        f = qa_findings.get(fid)
        if not f or f.get("origin") != "ai-audit":
            continue
        if f.get("status") in {"open", "correction-requested"}:
            f["status"] = "resolved"
            f["comments"] = [*f.get("comments", []), "Superseded by refreshed Stage 5 AI clinical audit."]
            f["updatedAt"] = now
            f["version"] = f.get("version", 1) + 1

    def _cited_source_objs(sids: list[str]) -> list[dict[str, Any]]:
        out: list[dict[str, Any]] = []
        seen_cid: set[str] = set()
        for sid in sids:
            if sid in seen_cid:
                continue
            seen_cid.add(sid)
            src = source_lookup.get(sid) or {}
            doc_id = str(src.get("documentId") or "")
            if not doc_id and not src:
                continue
            page_raw = str(src.get("pageRange") or "1").split("-")[0]
            try:
                page_num = int(page_raw)
            except ValueError:
                page_num = 1
            title = str(src.get("title") or doc_id or sid)
            out.append({
                "sourceId": sid,
                "documentId": doc_id,
                "documentTitle": title,
                "pageNumber": page_num,
                "citationLabel": f"{title} · p. {page_num}",
                "excerpt": str(src.get("text") or "")[:360],
            })
        return out

    created_ids: list[str] = []
    for idx, w in enumerate(warnings, 1):
        sev = "review" if w.get("severity") == "review" else "advisory"
        evidence_doc_ids = list(dict.fromkeys(
            str((source_lookup.get(sid) or {}).get("documentId"))
            for sid in (w.get("sourceIds") or [])
            if (source_lookup.get(sid) or {}).get("documentId")
        ))
        cited_objs = _cited_source_objs(list(w.get("sourceIds") or []))
        fid = f"finding-ai-{review['id']}-{idx}-{int(datetime.now(UTC).timestamp() * 1000)}"
        qa_findings[fid] = {
            "id": fid,
            "version": 1,
            "createdAt": now,
            "updatedAt": now,
            "esrId": review["esrId"],
            "qaReviewId": review["id"],
            "dbqId": dbq["id"],
            "sectionReference": w["sectionReference"],
            "issueCategory": w["issueCategory"],
            "severity": sev,
            "title": w["title"],
            "supportingEvidenceIds": evidence_doc_ids,
            "sourceIds": list(w.get("sourceIds") or []),
            "citedSources": cited_objs,
            "reviewerId": "ai-qa-assistant",
            "status": "open",
            "comments": [f"AI Clinical Audit ({sev}): {w['rationale']} [Sources: {', '.join(w.get('sourceIds') or [])}]"],
            "resolutionHistory": [],
            "origin": "ai-audit",
            "versionId": version["id"],
        }
        finding_ids.append(fid)
        created_ids.append(fid)

    review["findingIds"] = finding_ids
    return created_ids


def run_qa_audit_for_state(
    db: Session | None,
    state: dict,
    review_id: str,
    *,
    allow_llm: bool = True,
    force: bool = False,
) -> dict[str, Any]:
    """Run Stage 5 deterministic rules + complete Stage 2-reused multi-batch AI audit."""
    rules_out = run_deterministic_qa_rules(db, state, review_id)
    review = state["qaReviewsById"][review_id]
    dbq = state["dbqsById"][review["dbqId"]]
    version = state["dbqVersionsById"][review["versionId"]]
    now = _now_iso()

    core_context, sources, coverage = _collect_qa_evidence_inventory(state, review, dbq, version)
    source_lookup = {str(s["sourceId"]): s for s in sources}
    batches = _build_qa_evidence_batches(core_context, sources)
    coverage["batchesPlanned"] = len(batches)

    current_digest = rules_out["evidenceDigest"]
    prior_ai = review.get("aiAudit") or {}
    already_fresh = (
        not force
        and prior_ai.get("versionId") == version["id"]
        and prior_ai.get("evidenceDigest") == current_digest
        and prior_ai.get("status") in {"completed", "partial"}
    )
    if already_fresh:
        coverage["batchesSucceeded"] = prior_ai.get("batchesSucceeded", coverage["batchesPlanned"])
        coverage["aiReviewedCount"] = prior_ai.get("aiReviewedCount", len(sources))
        coverage["completeCoverage"] = (
            coverage["unavailableOrUnreadableCount"] == 0
            and prior_ai.get("status") == "completed"
        )
        review["evidenceCoverage"] = coverage
        return {
            **rules_out,
            "aiAudit": prior_ai,
            "evidenceCoverage": coverage,
            "cached": True,
        }

    provider_mode = str(settings.intake_provider or "none")
    if not allow_llm:
        ai_audit = {
            "status": "pending",
            "mode": "deterministic-only",
            "provider": None,
            "fallbackUsed": False,
            "summary": "Deterministic VA contract rules evaluated; AI clinical consistency review not executed yet.",
            "warningCount": 0,
            "batchesPlanned": len(batches),
            "batchesSucceeded": 0,
            "manualReviewRequired": False,
            "versionId": version["id"],
            "evidenceDigest": current_digest,
            "evaluatedAt": now,
        }
        review["aiAudit"] = ai_audit
        review["evidenceCoverage"] = coverage
        return {**rules_out, "aiAudit": ai_audit, "evidenceCoverage": coverage}

    if provider_mode == "none":
        ai_audit = {
            "status": "failed",
            "mode": "unavailable",
            "provider": None,
            "fallbackUsed": False,
            "summary": "AI clinical audit skipped because no AI provider is configured. Human QA reviewer must inspect evidence manually and record confirmation before approval.",
            "warningCount": 0,
            "batchesPlanned": len(batches),
            "batchesSucceeded": 0,
            "manualReviewRequired": True,
            "errorSummary": "No AI model provider enabled.",
            "versionId": version["id"],
            "evidenceDigest": current_digest,
            "evaluatedAt": now,
        }
        review["aiAudit"] = ai_audit
        review["evidenceCoverage"] = coverage
        return {**rules_out, "aiAudit": ai_audit, "evidenceCoverage": coverage}

    batch_outputs: list[dict[str, Any]] = []
    for b_idx, batch_sources in enumerate(batches, 1):
        efolder_excerpts = [s for s in batch_sources if s.get("kind") not in {"stage2-clinical-fact", "stage2-evidence-gap"}]
        stage2_facts = [s for s in batch_sources if s.get("kind") == "stage2-clinical-fact"]
        stage2_gaps = [s for s in batch_sources if s.get("kind") == "stage2-evidence-gap"]
        batch_ctx = {
            **core_context,
            "batchIndex": b_idx,
            "batchTotal": len(batches),
            "eFolderExcerpts": efolder_excerpts,
            "stage2Facts": stage2_facts,
            "stage2EvidenceGaps": stage2_gaps,
        }
        b_res = run_guarded_qa_audit_llm(batch_ctx, max_repair_attempts=2)
        b_res["batchSourceIds"] = [str(s["sourceId"]) for s in batch_sources]
        batch_outputs.append(b_res)

    merged = _reconcile_batch_results(batch_outputs, len(batches))
    coverage["batchesSucceeded"] = merged["batchesSucceeded"]
    coverage["aiReviewedCount"] = len(merged["coveredSourceIds"])
    coverage["completeCoverage"] = (
        coverage["unavailableOrUnreadableCount"] == 0
        and merged["status"] == "completed"
    )

    if merged["status"] == "failed":
        ai_audit = {
            "status": "failed",
            "mode": "unavailable",
            "provider": merged["providers"][0] if merged["providers"] else None,
            "fallbackUsed": merged["fallbackUsed"],
            "summary": "AI clinical audit failed across configured providers; deterministic VA contract rules remain enforced and manual human QA confirmation is required.",
            "warningCount": 0,
            "batchesPlanned": len(batches),
            "batchesSucceeded": 0,
            "manualReviewRequired": True,
            "errorSummary": "; ".join(merged["errors"])[:300],
            "versionId": version["id"],
            "evidenceDigest": current_digest,
            "evaluatedAt": now,
        }
    else:
        created = _sync_ai_warnings_into_findings(
            state, review, dbq, version, merged["warnings"], source_lookup, now
        )
        summary_txt = merged["summary"]
        if merged["status"] == "partial":
            summary_txt = (
                f"Partial AI audit ({merged['batchesSucceeded']}/{len(batches)} batches succeeded). "
                "Human QA reviewer must manually verify un-audited evidence batches before approval. "
                + summary_txt
            )
        all_cited_sids = list(dict.fromkeys(
            sid
            for w in merged["warnings"]
            for sid in (w.get("sourceIds") or [])
        ))
        cited_summary = [
            obj
            for fid in created
            for obj in ((state.get("qaFindingsById") or {}).get(fid, {}).get("citedSources") or [])
        ]
        ai_audit = {
            "status": merged["status"],
            "mode": "llm-assisted",
            "provider": ", ".join(merged["providers"]) if merged["providers"] else settings.intake_provider,
            "fallbackUsed": merged["fallbackUsed"],
            "summary": summary_txt.strip() or "AI clinical audit reviewed supplied Stage 2 facts and evidence batches.",
            "warningCount": len(merged["warnings"]),
            "createdFindingIds": created,
            "citedSources": cited_summary,
            "batchesPlanned": len(batches),
            "batchesSucceeded": merged["batchesSucceeded"],
            "manualReviewRequired": merged["status"] == "partial",
            "versionId": version["id"],
            "evidenceDigest": current_digest,
            "evaluatedAt": now,
        }

    review["aiAudit"] = ai_audit
    review["evidenceCoverage"] = coverage
    review["updatedAt"] = now
    review["version"] = review.get("version", 1) + 1
    logger.info(
        "Stage 5 audit completed review=%s version=%s aiStatus=%s batches=%s/%s findings=%s",
        review_id,
        version["id"],
        ai_audit["status"],
        ai_audit["batchesSucceeded"],
        ai_audit["batchesPlanned"],
        ai_audit.get("warningCount", 0),
    )
    record_qa_audit_event(
        db,
        "system-qa",
        f"qa-audit-{ai_audit['status']}",
        review_id,
        {
            "reviewId": review_id,
            "dbqId": dbq["id"],
            "versionId": version["id"],
            "evidenceDigest": current_digest,
            "ruleVersion": QA_RULES_VERSION,
            "failedRules": rules_out["failedCount"],
            "reviewNeededRules": rules_out["reviewNeededCount"],
            "aiStatus": ai_audit["status"],
            "batchesPlanned": ai_audit["batchesPlanned"],
            "batchesSucceeded": ai_audit["batchesSucceeded"],
            "warningCount": ai_audit.get("warningCount", 0),
            "coverage": {
                "availableCount": coverage["availableCount"],
                "processedCount": coverage["processedCount"],
                "aiReviewedCount": coverage["aiReviewedCount"],
                "unavailableOrUnreadableCount": coverage["unavailableOrUnreadableCount"],
            },
        },
    )
    return {
        **rules_out,
        "aiAudit": ai_audit,
        "evidenceCoverage": coverage,
    }


def enqueue_qa_audit_job(db: Session, review_id: str, *, force: bool = False) -> str:
    """Create or reuse a DB-backed Stage 5 QA audit job + OutboxJob with digest deduplication."""
    from app.services.workspace_store import get_state

    state = get_state(db)
    review = (state.get("qaReviewsById") or {}).get(review_id) or {}
    dbq_id = str(review.get("dbqId") or "")
    version_id = str(review.get("versionId") or "")
    digest = compute_qa_evidence_digest(state, review_id) if review else ""

    existing_job = db.scalar(
        select(Stage5QAAuditJob).where(
            Stage5QAAuditJob.review_id == review_id,
            Stage5QAAuditJob.version_id == version_id,
            Stage5QAAuditJob.evidence_digest == digest,
            Stage5QAAuditJob.status.in_(("queued", "running")),
        )
    )
    if existing_job and not force:
        queued_outboxes = db.scalars(
            select(OutboxJob).where(
                OutboxJob.task_name == QA_TASK_NAME,
                OutboxJob.status == "queued",
            )
        ).all()
        existing_outbox = next(
            (row for row in queued_outboxes if (row.payload or {}).get("reviewId") == review_id),
            None,
        )
        if existing_outbox:
            return existing_outbox.id

    # Mark superseded queued jobs for older versions/digests on this review
    now_dt = datetime.now(UTC)
    db.execute(
        update(Stage5QAAuditJob)
        .where(
            Stage5QAAuditJob.review_id == review_id,
            Stage5QAAuditJob.status == "queued",
        )
        .values(status="superseded", updated_at=now_dt)
    )

    qa_job_id = f"qajob-{uuid4()}"
    outbox_id = str(uuid4())
    db.add(Stage5QAAuditJob(
        job_id=qa_job_id,
        review_id=review_id,
        dbq_id=dbq_id,
        version_id=version_id,
        evidence_digest=digest,
        status="queued",
        attempts=0,
        last_error="",
        created_at=now_dt,
        updated_at=now_dt,
    ))
    db.add(OutboxJob(
        id=outbox_id,
        task_name=QA_TASK_NAME,
        status="queued",
        payload={
            "qaJobId": qa_job_id,
            "reviewId": review_id,
            "dbqId": dbq_id,
            "versionId": version_id,
            "evidenceDigest": digest,
            "force": bool(force),
        },
    ))
    record_qa_audit_event(
        db,
        "system-qa",
        "qa-audit-queued",
        review_id,
        {"qaJobId": qa_job_id, "reviewId": review_id, "versionId": version_id, "evidenceDigest": digest, "force": force},
    )
    return outbox_id


def finish_qa_audit_job(job_id: str, lease: str, review_id: str, *, force: bool = False) -> None:
    """Worker entrypoint executed by run_intake_worker when claiming QA_TASK_NAME."""
    from app.services.intake_store import save_state
    from app.services.workspace_store import get_state

    qa_job_id = ""
    expected_version_id = ""
    expected_digest = ""

    # Phase 1: Claim Stage5QAAuditJob metadata & compute AI/rules without holding DB write lock across LLM calls
    with SessionLocal() as db:
        outbox = db.scalar(select(OutboxJob).where(OutboxJob.id == job_id))
        if outbox and isinstance(outbox.payload, dict):
            p = outbox.payload
            qa_job_id = str(p.get("qaJobId") or "")
            expected_version_id = str(p.get("versionId") or "")
            expected_digest = str(p.get("evidenceDigest") or "")
        if qa_job_id:
            db.execute(
                update(Stage5QAAuditJob)
                .where(Stage5QAAuditJob.job_id == qa_job_id)
                .values(status="running", worker_lease=lease, attempts=Stage5QAAuditJob.attempts + 1, updated_at=datetime.now(UTC))
            )
            db.commit()

        state = get_state(db)
        review = (state.get("qaReviewsById") or {}).get(review_id)
        if not review:
            if qa_job_id:
                db.execute(
                    update(Stage5QAAuditJob)
                    .where(Stage5QAAuditJob.job_id == qa_job_id)
                    .values(status="superseded", last_error="QA review missing", updated_at=datetime.now(UTC))
                )
            db.execute(update(OutboxJob).where(OutboxJob.id == job_id, OutboxJob.status == lease).values(status="completed"))
            db.commit()
            return
        if not expected_version_id:
            expected_version_id = str(review.get("versionId") or "")
        if not expected_digest:
            expected_digest = compute_qa_evidence_digest(state, review_id)

        audit_out = run_qa_audit_for_state(None, state, review_id, allow_llm=True, force=force)
        computed_ai = dict(audit_out.get("aiAudit") or {})
        computed_coverage = dict(audit_out.get("evidenceCoverage") or {})
        computed_ai_findings = {
            fid: dict((state.get("qaFindingsById") or {})[fid])
            for fid in (computed_ai.get("createdFindingIds") or [])
            if fid in (state.get("qaFindingsById") or {})
        }
        esr_id = str(review["esrId"])

    # Phase 2: Atomic commit with stale/version guard and 409 retry (never re-running LLM on version conflict)
    for _attempt in range(3):
        with SessionLocal() as db:
            latest_state = get_state(db)
            latest_review = (latest_state.get("qaReviewsById") or {}).get(review_id)
            if not latest_review:
                if qa_job_id:
                    db.execute(
                        update(Stage5QAAuditJob)
                        .where(Stage5QAAuditJob.job_id == qa_job_id)
                        .values(status="superseded", updated_at=datetime.now(UTC))
                    )
                db.execute(update(OutboxJob).where(OutboxJob.id == job_id, OutboxJob.status == lease).values(status="completed"))
                db.commit()
                return

            latest_digest = compute_qa_evidence_digest(latest_state, review_id)
            if (
                latest_review.get("versionId") != expected_version_id
                or (expected_digest and latest_digest != expected_digest)
            ):
                if qa_job_id:
                    db.execute(
                        update(Stage5QAAuditJob)
                        .where(Stage5QAAuditJob.job_id == qa_job_id)
                        .values(status="superseded", last_error="Revision or evidence changed during QA job", updated_at=datetime.now(UTC))
                    )
                db.execute(update(OutboxJob).where(OutboxJob.id == job_id, OutboxJob.status == lease).values(status="completed"))
                record_qa_audit_event(
                    db,
                    "qa-worker",
                    "qa-audit-superseded",
                    review_id,
                    {"qaJobId": qa_job_id, "expectedVersionId": expected_version_id, "latestVersionId": latest_review.get("versionId")},
                )
                db.commit()
                return

            # Apply fresh deterministic rules on latest_state and attach already-computed AI audit + findings
            run_deterministic_qa_rules(db, latest_state, review_id)
            if computed_ai_findings:
                findings_map = latest_state.setdefault("qaFindingsById", {})
                fids = list(latest_review.get("findingIds") or [])
                for fid, fobj in computed_ai_findings.items():
                    findings_map[fid] = fobj
                    if fid not in fids:
                        fids.append(fid)
                latest_review["findingIds"] = fids
            latest_review["aiAudit"] = computed_ai
            latest_review["evidenceCoverage"] = computed_coverage

            try:
                save_state(db, latest_state, esr_id)
            except HTTPException as exc:
                if exc.status_code != 409:
                    raise
                db.rollback()
                continue

            if qa_job_id:
                final_status = "failed" if computed_ai.get("status") == "failed" else "completed"
                db.execute(
                    update(Stage5QAAuditJob)
                    .where(Stage5QAAuditJob.job_id == qa_job_id)
                    .values(
                        status=final_status,
                        last_error=str(computed_ai.get("errorSummary") or ""),
                        updated_at=datetime.now(UTC),
                    )
                )
            changed = db.execute(
                update(OutboxJob)
                .where(OutboxJob.id == job_id, OutboxJob.status == lease)
                .values(status="completed")
            )
            if changed.rowcount == 1:
                db.commit()
            else:
                db.commit()
            return
