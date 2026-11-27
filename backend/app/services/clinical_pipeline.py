"""LangGraph Stage 4 Clinical Evaluation Pipeline: Dual-Mode DBQ Processing, Validation & Draft Medical Review."""

from __future__ import annotations

import logging
import re
from datetime import UTC, datetime
from typing import Any, Literal, TypedDict

from langgraph.graph import END, START, StateGraph
from pydantic import BaseModel, ConfigDict, Field

from app.core.config import settings
from app.services.agent_prompts import (
    STAGE4_EXTRACTION_PROMPT,
    STAGE4_VALIDATION_AND_REVIEW_PROMPT,
)
from app.services.intake_processing import (
    DocumentProcessingError,
    azure_review_completion,
    extract_pages_with_ocr,
    get_last_completion_provider,
)

logger = logging.getLogger(__name__)
STAGE4_PROMPT_VERSION = "stage4-clinical-copilot-v1.0"


class ExtractedDBQFields(BaseModel):
    model_config = ConfigDict(extra="forbid")
    detectedClaimantName: str = Field(default="", max_length=160)
    history: str = Field(default="", max_length=4000)
    findings: str = Field(default="", max_length=6000)
    rationale: str = Field(default="", max_length=4000)
    recordsReviewed: str = Field(default="", max_length=3000)
    diagnosisOutcome: Literal["Established", "Could not be established", ""] = ""
    diagnosisDetails: str = Field(default="", max_length=3000)
    remarks: str = Field(default="", max_length=2000)


class ValidationAlertSchema(BaseModel):
    model_config = ConfigDict(extra="forbid")
    severity: Literal["blocking", "warning"]
    category: Literal["completeness", "identity", "scope-mismatch", "clinical-gap", "diagnostic-flag", "readability"]
    message: str = Field(max_length=400)
    recommendedAction: str = Field(max_length=400)


class ValidationAndReviewSchema(BaseModel):
    model_config = ConfigDict(extra="forbid")
    alerts: list[ValidationAlertSchema] = Field(default_factory=list, max_length=10)
    summary: str = Field(max_length=2500)
    keyFindings: list[str] = Field(default_factory=list, max_length=8)
    diagnosticSummary: str = Field(default="", max_length=1500)
    citations: list[str] = Field(default_factory=list, max_length=10)


class ClinicalGraphState(TypedDict, total=False):
    mode: Literal["digital-form", "uploaded-dbq"]
    esr: dict
    veteran: dict
    examination: dict
    dbq: dict
    contentions: list[dict]
    requirements: list[dict]
    diagnostic_orders: list[dict]
    diagnostic_results: list[dict]
    triage_dossier: dict | None
    submitted_values: dict[str, Any]
    uploaded_dbq_file: dict | None
    uploaded_diagnostic_files: list[dict]
    allow_llm: bool
    # Computed graph outputs
    parsed_dbq_pages: dict[int, str]
    parsed_dbq_text: str
    detected_claimant_name: str
    parsed_diagnostic_docs: list[dict]
    extracted_values: dict[str, Any]
    validation_alerts: list[dict]
    validation_passed: bool
    draft_medical_review: dict | None
    provider_used: str


def _now_iso() -> str:
    return datetime.now(UTC).isoformat()


def _route_input_mode(state: ClinicalGraphState) -> str:
    return "parse_and_extract_upload" if state.get("mode") == "uploaded-dbq" else "normalize_digital_form"


def _parse_diagnostic_uploads(files: list[dict]) -> list[dict]:
    parsed: list[dict] = []
    for item in files or []:
        filename = str(item.get("filename") or "diagnostic-report.pdf")
        content = item.get("content") or b""
        order_id = str(item.get("orderId") or "")
        try:
            pages, ocr_applied, _ = extract_pages_with_ocr(filename, content)
            full_text = "\n\n".join(f"[Page {p}] {txt}" for p, txt in sorted(pages.items()) if txt.strip())
            lower_text = full_text.lower()
            reported_flag = None
            if "critical" in lower_text:
                reported_flag = "critical"
            elif any(tok in lower_text for tok in ("abnormal", "tear", "effusion", "loss", "positive", "elevated")):
                reported_flag = "abnormal"
            summary = full_text[:1200].strip() or f"Uploaded diagnostic report: {filename}"
            parsed.append({
                "orderId": order_id,
                "filename": filename,
                "pageCount": len(pages),
                "ocrApplied": ocr_applied,
                "text": full_text[:8000],
                "summary": summary,
                "reportedFlag": reported_flag,
                "readable": bool(full_text.strip()),
            })
        except DocumentProcessingError as exc:
            parsed.append({
                "orderId": order_id,
                "filename": filename,
                "pageCount": 0,
                "ocrApplied": False,
                "text": "",
                "summary": str(exc),
                "reportedFlag": None,
                "readable": False,
                "error": str(exc),
            })
    return parsed


def _deterministic_extract_dbq_fields(pages: dict[int, str], dbq: dict) -> tuple[dict[str, Any], str]:
    full_text = "\n\n".join(txt for _, txt in sorted(pages.items()) if txt.strip())
    lines = [ln.strip() for ln in full_text.splitlines() if ln.strip()]

    detected_name = ""
    for ln in lines[:20]:
        m = re.search(r"(?:claimant|veteran|patient|name)\s*[:\-]\s*([A-Za-z][A-Za-z\s\.\-']{2,50})", ln, re.I)
        if m:
            detected_name = m.group(1).strip()
            break

    def _section_grab(patterns: tuple[str, ...], fallback: str = "") -> str:
        for idx, ln in enumerate(lines):
            if any(re.search(pat, ln, re.I) for pat in patterns):
                chunk = [ln]
                for nxt in lines[idx + 1: idx + 6]:
                    if any(re.match(r"^(history|findings|physical exam|rationale|medical opinion|diagnosis|records|remarks)\s*[:\-]", nxt, re.I) for _ in [0]):
                        break
                    chunk.append(nxt)
                return " ".join(chunk)[:2500]
        return fallback

    history = _section_grab((r"^history\b", r"medical history", r"subjective"), full_text[:900].strip())
    findings = _section_grab((r"^(exam|examination|physical|objective)\s*findings", r"range of motion", r"\bROM\b", r"audiogram"), full_text[:1400].strip())
    rationale = _section_grab((r"^(clinical\s+)?rationale", r"medical opinion", r"impression", r"assessment"), "Findings documented in uploaded DBQ report.")
    records_reviewed = _section_grab((r"records reviewed", r"evidence reviewed", r"source"), "Reviewed uploaded DBQ and linked Stage 1-2 claim records.")
    diagnosis_details = _section_grab((r"^diagnosis\b", r"diagnostic impression", r"condition"), f"{dbq.get('formType', 'Condition')} evaluated per uploaded DBQ.")
    remarks = _section_grab((r"^remarks\b", r"additional comments"), "")

    lower = full_text.lower()
    outcome = "Could not be established" if "could not be established" in lower or "no diagnosis" in lower else ("Established" if full_text.strip() else "")

    values = {
        "history": history,
        "findings": findings,
        "rationale": rationale,
        "recordsReviewed": records_reviewed,
        "diagnosisOutcome": outcome,
        "diagnosisDetails": diagnosis_details,
        "remarks": remarks,
    }
    return values, detected_name


def _extract_document_data(state: ClinicalGraphState) -> dict[str, Any]:
    """Node 1: Extract Document Data from completed DBQ file or submitted digital form and supporting diagnostics."""
    diag_docs = _parse_diagnostic_uploads(state.get("uploaded_diagnostic_files") or [])
    mode = state.get("mode") or "digital-form"

    if mode == "uploaded-dbq":
        upload = state.get("uploaded_dbq_file") or {}
        filename = str(upload.get("filename") or "uploaded-dbq.pdf")
        content = upload.get("content") or b""
        dbq = state.get("dbq") or {}

        try:
            pages, _, _ = extract_pages_with_ocr(filename, content)
            full_text = "\n\n".join(f"[Page {p}] {txt}" for p, txt in sorted(pages.items()) if txt.strip())
            det_values, detected_name = _deterministic_extract_dbq_fields(pages, dbq)
            return {
                "parsed_dbq_pages": pages,
                "parsed_dbq_text": full_text,
                "detected_claimant_name": detected_name,
                "parsed_diagnostic_docs": diag_docs,
                "extracted_values": det_values,
                "provider_used": "local",
            }
        except DocumentProcessingError as exc:
            return {
                "parsed_dbq_pages": {},
                "parsed_dbq_text": "",
                "detected_claimant_name": "",
                "parsed_diagnostic_docs": diag_docs,
                "extracted_values": dict(dbq.get("values") or {}),
                "validation_alerts": [{
                    "id": "alert-readability",
                    "severity": "blocking",
                    "category": "readability",
                    "message": f"Uploaded DBQ '{filename}' could not be read: {exc}",
                    "recommendedAction": "Upload a readable UTF-8 text or searchable PDF DBQ document.",
                }],
                "provider_used": "local",
            }
    else:
        submitted = dict(state.get("submitted_values") or state.get("dbq", {}).get("values") or {})
        return {
            "parsed_dbq_pages": {},
            "parsed_dbq_text": "",
            "detected_claimant_name": "",
            "parsed_diagnostic_docs": diag_docs,
            "extracted_values": submitted,
            "provider_used": "local",
        }


def _identify_dbq_type_and_version(state: ClinicalGraphState) -> dict[str, Any]:
    """Node 2: Identify DBQ Type and Version, plus claimant identity verification."""
    alerts: list[dict] = list(state.get("validation_alerts") or [])
    dbq = state.get("dbq") or {}
    veteran = state.get("veteran") or {}
    detected_name = str(state.get("detected_claimant_name") or "").strip()
    vet_name = str(veteran.get("displayName") or veteran.get("fullName") or "").strip()

    if detected_name and vet_name:
        vet_tokens = {t.lower() for t in re.findall(r"[A-Za-z]{2,}", vet_name)}
        det_tokens = {t.lower() for t in re.findall(r"[A-Za-z]{2,}", detected_name)}
        if vet_tokens and det_tokens and not (vet_tokens & det_tokens):
            alerts.append({
                "id": "alert-identity-mismatch",
                "severity": "blocking",
                "category": "identity",
                "message": f"Uploaded DBQ claimant '{detected_name}' does not match Veteran '{vet_name}'.",
                "recommendedAction": "Verify the uploaded file belongs to this Veteran and re-upload the correct DBQ.",
            })

    return {
        "validation_alerts": alerts,
        "dbq_form_type": str(dbq.get("formType") or "Standard VA DBQ"),
        "dbq_form_version": str(dbq.get("formVersion") or "synthetic-v2"),
    }


def _normalize_clinical_fields(state: ClinicalGraphState) -> dict[str, Any]:
    """Node 3: Normalize Clinical Fields into canonical DBQ schema."""
    extracted = dict(state.get("extracted_values") or {})
    full_text = state.get("parsed_dbq_text") or ""
    dbq = state.get("dbq") or {}
    provider_used = state.get("provider_used") or "local"

    if state.get("mode") == "uploaded-dbq" and state.get("allow_llm") and full_text.strip():
        upload = state.get("uploaded_dbq_file") or {}
        filename = str(upload.get("filename") or "uploaded-dbq.pdf")
        sources = [{
            "id": "DBQ_UPLOAD",
            "label": f"{filename} ({dbq.get('formType', 'DBQ')})",
            "excerpt": full_text[:12000],
        }]
        try:
            raw = azure_review_completion(
                "clinical_dbq_extract",
                ExtractedDBQFields.model_json_schema(),
                STAGE4_EXTRACTION_PROMPT,
                sources,
                max_tokens=1400,
            )
            parsed = ExtractedDBQFields.model_validate_json(raw)
            provider_used = get_last_completion_provider()
            extracted = {
                "history": parsed.history or extracted.get("history", ""),
                "findings": parsed.findings or extracted.get("findings", ""),
                "rationale": parsed.rationale or extracted.get("rationale", ""),
                "recordsReviewed": parsed.recordsReviewed or extracted.get("recordsReviewed", ""),
                "diagnosisOutcome": parsed.diagnosisOutcome or extracted.get("diagnosisOutcome", ""),
                "diagnosisDetails": parsed.diagnosisDetails or extracted.get("diagnosisDetails", ""),
                "remarks": parsed.remarks or extracted.get("remarks", ""),
            }
        except Exception as exc:
            logger.warning("Stage 4 LLM extraction fallback to deterministic parser: %s", exc)

    return {
        "extracted_values": extracted,
        "provider_used": provider_used,
    }


def _basic_completeness_validation(state: ClinicalGraphState) -> dict[str, Any]:
    """Node 4: Basic Completeness Validation against required fields, diagnostics, and Stage 2 gaps."""
    alerts: list[dict] = list(state.get("validation_alerts") or [])
    dbq = state.get("dbq") or {}
    values = state.get("extracted_values") or {}
    veteran = state.get("veteran") or {}
    contentions = state.get("contentions") or []
    diag_orders = state.get("diagnostic_orders") or []
    diag_results = state.get("diagnostic_results") or []
    dossier = state.get("triage_dossier") or {}

    is_opinion = "opinion" in str(dbq.get("formType") or "").lower()
    required_keys = (
        [("opinionQuestion", "Medical opinion question"), ("rationale", "Clinical rationale")]
        if is_opinion
        else [("history", "Medical history"), ("findings", "Examination findings"), ("rationale", "Clinical rationale")]
    )
    if dbq.get("formVersion") == "synthetic-v2":
        required_keys.append(("recordsReviewed", "Records reviewed and source references"))
        if not is_opinion:
            required_keys.extend([("diagnosisOutcome", "Diagnosis outcome"), ("diagnosisDetails", "Diagnosis details")])

    for key, label in required_keys:
        if not str(values.get(key) or "").strip():
            alerts.append({
                "id": f"alert-missing-{key}",
                "severity": "blocking",
                "category": "completeness",
                "message": f"{label} is missing.",
                "recommendedAction": f"Complete the '{label}' section or upload a complete DBQ before sign-off.",
            })

    # Patient identity check on uploaded DBQ
    detected_name = str(state.get("detected_claimant_name") or "").strip()
    vet_name = str(veteran.get("displayName") or veteran.get("fullName") or "").strip()
    if detected_name and vet_name:
        vet_tokens = {t.lower() for t in re.findall(r"[A-Za-z]{2,}", vet_name)}
        det_tokens = {t.lower() for t in re.findall(r"[A-Za-z]{2,}", detected_name)}
        if vet_tokens and det_tokens and not (vet_tokens & det_tokens):
            alerts.append({
                "id": "alert-identity-mismatch",
                "severity": "blocking",
                "category": "identity",
                "message": f"Uploaded DBQ claimant '{detected_name}' does not match Veteran '{vet_name}'.",
                "recommendedAction": "Verify the uploaded file belongs to this Veteran and re-upload the correct DBQ.",
            })

    # Scope / Condition alignment check
    combined_text = " ".join(str(v or "") for v in values.values()).lower()
    form_type = str(dbq.get("formType") or "").lower()
    if combined_text.strip():
        if "knee" in form_type and not any(w in combined_text for w in ("knee", "flexion", "extension", "rom", "joint", "menisc", "patella", "lower leg")):
            alerts.append({
                "id": "alert-scope-knee",
                "severity": "warning",
                "category": "scope-mismatch",
                "message": "Findings do not clearly reference knee anatomy or Range of Motion (ROM) measurements.",
                "recommendedAction": "Confirm right/left knee examination findings and ROM measurements are documented.",
            })
        elif "hearing" in form_type and not any(w in combined_text for w in ("hearing", "tinnitus", "audiogram", "auditory", "ear", "hz", "db", "speech")):
            alerts.append({
                "id": "alert-scope-hearing",
                "severity": "warning",
                "category": "scope-mismatch",
                "message": "Findings do not reference hearing thresholds, audiogram results, or tinnitus.",
                "recommendedAction": "Verify audiometric findings and tinnitus assessment are included.",
            })
        elif "skin" in form_type and not any(w in combined_text for w in ("skin", "eczema", "dermat", "lesion", "rash", "scar", "bsa", "topical")):
            alerts.append({
                "id": "alert-scope-skin",
                "severity": "warning",
                "category": "scope-mismatch",
                "message": "Findings do not reference skin condition, affected body surface area, or systemic/topical therapy.",
                "recommendedAction": "Confirm dermatological findings and treatment history are documented.",
            })

    # Stage 2 "Collect During Exam" gap check
    packets = (dossier.get("packetsByContentionId") or {})
    for cont in contentions:
        pkt = packets.get(cont.get("id")) or {}
        gaps = (pkt.get("rulesEval") or {}).get("evidenceGaps") or []
        for gap in gaps:
            if gap.get("gapGroup") == "collect-during-exam":
                msg = str(gap.get("message") or "")
                if msg and ("rom" in msg.lower() and "rom" not in combined_text and "flexion" not in combined_text and "°" not in combined_text):
                    alerts.append({
                        "id": f"alert-gap-{gap.get('id', 'rom')}",
                        "severity": "warning",
                        "category": "clinical-gap",
                        "message": f"Stage 2 gap may be unaddressed: {msg}",
                        "recommendedAction": str(gap.get("recommendedAction") or "Confirm this measurement during the exam."),
                    })

    # Diagnostic completeness & abnormal/critical flags
    for order in diag_orders:
        has_result = bool(order.get("resultIds"))
        has_exception = bool(order.get("exception"))
        if not has_result and not has_exception:
            alerts.append({
                "id": f"alert-diag-pending-{order.get('id')}",
                "severity": "blocking",
                "category": "diagnostic-flag",
                "message": f"Diagnostic order '{order.get('orderType', 'Diagnostic')}' has no uploaded report or exception.",
                "recommendedAction": "Upload/record the diagnostic result or document a clinical exception.",
            })

    for res in diag_results:
        if res.get("reportedFlag") in {"abnormal", "critical"} and not res.get("reviewedAt"):
            alerts.append({
                "id": f"alert-diag-flag-{res.get('id')}",
                "severity": "blocking",
                "category": "diagnostic-flag",
                "message": f"Diagnostic report '{res.get('sourceReference')}' is marked {res.get('reportedFlag')} and requires clinician review.",
                "recommendedAction": "Review the diagnostic result and click 'Record result review'.",
            })

    blocking_count = sum(1 for a in alerts if a.get("severity") == "blocking")
    return {
        "validation_alerts": alerts,
        "validation_passed": blocking_count == 0,
    }


def _ready_for_draft_router(state: ClinicalGraphState) -> str:
    """Decision router: Ready for Draft?
    Evaluates whether minimal clinical data is available to generate a review.
    If not, routes to Display Missing Information; otherwise proceeds to Generate AI Medical Review.
    """
    values = state.get("extracted_values") or {}
    has_content = any(str(v or "").strip() for v in values.values())
    if not has_content:
        return "display_missing_information"
    return "generate_ai_medical_review"


def _display_missing_information(state: ClinicalGraphState) -> dict[str, Any]:
    """Node 5a: Display Missing Information when clinical data is incomplete."""
    alerts = list(state.get("validation_alerts") or [])
    if not any(a.get("category") == "completeness" for a in alerts):
        alerts.append({
            "id": "alert-missing-all",
            "severity": "blocking",
            "category": "completeness",
            "message": "No clinical data has been entered or extracted yet.",
            "recommendedAction": "Complete the digital form or upload a filled DBQ document.",
        })
    return {
        "validation_alerts": alerts,
        "validation_passed": False,
        "draft_medical_review": None,
    }


def _generate_ai_medical_review(state: ClinicalGraphState) -> dict[str, Any]:
    """Node 5b: Generate AI Medical Review."""
    dbq = state.get("dbq") or {}
    veteran = state.get("veteran") or {}
    contentions = state.get("contentions") or []
    values = state.get("extracted_values") or {}
    diag_results = state.get("diagnostic_results") or []
    alerts = list(state.get("validation_alerts") or [])
    vet_name = str(veteran.get("displayName") or veteran.get("fullName") or "Veteran")
    form_type = str(dbq.get("formType") or "DBQ")
    cont_titles = ", ".join(c.get("title", "") for c in contentions if c.get("title")) or form_type

    provider_used = state.get("provider_used") or "local"

    if state.get("allow_llm") and any(str(v or "").strip() for v in values.values()):
        sources = [
            {
                "id": "DBQ_VALUES",
                "label": f"{form_type} · Submitted Findings",
                "excerpt": (
                    f"Veteran: {vet_name}\nClaimed Conditions: {cont_titles}\n"
                    f"History: {values.get('history', '')}\n"
                    f"Findings: {values.get('findings', '')}\n"
                    f"Diagnosis: {values.get('diagnosisOutcome', '')} — {values.get('diagnosisDetails', '')}\n"
                    f"Rationale: {values.get('rationale', '')}\n"
                    f"Records Reviewed: {values.get('recordsReviewed', '')}"
                )[:5000],
            }
        ]
        for idx, res in enumerate(diag_results[:4], 1):
            sources.append({
                "id": f"DIAG_{idx}",
                "label": f"Diagnostic: {res.get('sourceReference', 'Report')}",
                "excerpt": f"Flag: {res.get('reportedFlag', 'none')} · {res.get('summary', '')}"[:1200],
            })
        try:
            raw = azure_review_completion(
                "clinical_medical_review",
                ValidationAndReviewSchema.model_json_schema(),
                STAGE4_VALIDATION_AND_REVIEW_PROMPT,
                sources,
                max_tokens=1400,
            )
            parsed = ValidationAndReviewSchema.model_validate_json(raw)
            provider_used = get_last_completion_provider()
            for idx, extra_alert in enumerate(parsed.alerts):
                if not any(a["message"] == extra_alert.message for a in alerts):
                    alerts.append({
                        "id": f"alert-ai-{idx}",
                        "severity": extra_alert.severity,
                        "category": extra_alert.category,
                        "message": extra_alert.message,
                        "recommendedAction": extra_alert.recommendedAction,
                    })
            blocking_count = sum(1 for a in alerts if a.get("severity") == "blocking")
            return {
                "validation_alerts": alerts,
                "validation_passed": blocking_count == 0,
                "provider_used": provider_used,
                "draft_medical_review": {
                    "summary": parsed.summary,
                    "keyFindings": parsed.keyFindings,
                    "diagnosticSummary": parsed.diagnosticSummary,
                    "citations": parsed.citations or [s["label"] for s in sources],
                },
            }
        except Exception as exc:
            logger.warning("Stage 4 LLM medical review fallback: %s", exc)

    # Deterministic template fallback
    findings_preview = str(values.get("findings") or "Examination findings recorded.").strip()
    diag_outcome = str(values.get("diagnosisOutcome") or "").strip()
    diag_details = str(values.get("diagnosisDetails") or "").strip()
    diag_text = "; ".join(f"{r.get('sourceReference')}: {r.get('summary', '')[:180]}" for r in diag_results) or "No separate diagnostic orders recorded."
    key_findings = [
        f"Condition / Scope: {cont_titles} ({form_type})",
        f"Clinical Findings: {findings_preview[:240]}",
    ]
    if diag_outcome or diag_details:
        key_findings.append(f"Diagnosis: {diag_outcome} — {diag_details[:200]}".strip(" —"))
    summary = (
        f"Draft Medical Review for {vet_name} ({form_type}). "
        f"Completed examination findings and supporting records ({values.get('recordsReviewed') or 'claim file'}) "
        f"have been compiled for clinician verification and electronic sign-off."
    )
    return {
        "provider_used": provider_used,
        "draft_medical_review": {
            "summary": summary,
            "keyFindings": key_findings,
            "diagnosticSummary": diag_text,
            "citations": [str(values.get("recordsReviewed") or f"{form_type} Submission")],
        },
    }


def _attach_source_references(state: ClinicalGraphState) -> dict[str, Any]:
    """Node 6: Attach Source References."""
    review = dict(state.get("draft_medical_review") or {})
    values = state.get("extracted_values") or {}
    diag_results = state.get("diagnostic_results") or []
    dbq = state.get("dbq") or {}
    form_type = str(dbq.get("formType") or "DBQ")

    citations = list(review.get("citations") or [])
    rec_reviewed = str(values.get("recordsReviewed") or "").strip()
    if rec_reviewed and rec_reviewed not in citations:
        citations.append(rec_reviewed)
    for res in diag_results:
        ref = str(res.get("sourceReference") or "").strip()
        if ref and ref not in citations:
            citations.append(ref)
    if not citations:
        citations.append(f"{form_type} Examination Findings")

    review["citations"] = citations
    return {"draft_medical_review": review}


def _human_review_and_approval(state: ClinicalGraphState) -> dict[str, Any]:
    """Node 7: Human Review and Approval wrapper."""
    review = dict(state.get("draft_medical_review") or {})
    review["status"] = "pending-approval"
    review["generatedAt"] = _now_iso()
    review["modelUsed"] = state.get("provider_used") or "local"
    review["promptVersion"] = STAGE4_PROMPT_VERSION
    return {"draft_medical_review": review}


def _build_clinical_graph():
    """Corrected 8-node LangGraph Clinical Evaluation Pipeline matching user architecture."""
    graph = StateGraph(ClinicalGraphState)
    graph.add_node("extract_document_data", _extract_document_data)
    graph.add_node("identify_dbq_type_and_version", _identify_dbq_type_and_version)
    graph.add_node("normalize_clinical_fields", _normalize_clinical_fields)
    graph.add_node("basic_completeness_validation", _basic_completeness_validation)
    graph.add_node("display_missing_information", _display_missing_information)
    graph.add_node("generate_ai_medical_review", _generate_ai_medical_review)
    graph.add_node("attach_source_references", _attach_source_references)
    graph.add_node("human_review_and_approval", _human_review_and_approval)

    # Linear extraction, classification, normalization, and validation flow
    graph.add_edge(START, "extract_document_data")
    graph.add_edge("extract_document_data", "identify_dbq_type_and_version")
    graph.add_edge("identify_dbq_type_and_version", "normalize_clinical_fields")
    graph.add_edge("normalize_clinical_fields", "basic_completeness_validation")

    # Conditional Branch: Ready for Draft?
    graph.add_conditional_edges(
        "basic_completeness_validation",
        _ready_for_draft_router,
        {
            "display_missing_information": "display_missing_information",
            "generate_ai_medical_review": "generate_ai_medical_review",
        },
    )

    graph.add_edge("display_missing_information", END)
    graph.add_edge("generate_ai_medical_review", "attach_source_references")
    graph.add_edge("attach_source_references", "human_review_and_approval")
    graph.add_edge("human_review_and_approval", END)
    return graph.compile()


_CLINICAL_GRAPH = _build_clinical_graph()


def run_clinical_pipeline(
    state: dict,
    dbq_id: str,
    *,
    mode: Literal["digital-form", "uploaded-dbq"],
    submitted_values: dict[str, Any] | None = None,
    uploaded_dbq_file: dict | None = None,
    uploaded_diagnostic_files: list[dict] | None = None,
    allow_llm: bool | None = None,
) -> dict[str, Any]:
    dbq = state.get("dbqsById", {}).get(dbq_id)
    if not dbq:
        raise ValueError(f"DBQ '{dbq_id}' not found.")
    exam = state.get("examinationsById", {}).get(dbq["examinationId"]) or {}
    esr = state.get("esrsById", {}).get(dbq["esrId"]) or {}
    veteran = state.get("veteransById", {}).get(esr.get("veteranId")) or {}
    contentions = [state.get("contentionsById", {}).get(cid) for cid in dbq.get("contentionIds", []) if cid in state.get("contentionsById", {})]
    requirements = [state.get("examinationRequirementsById", {}).get(rid) for rid in exam.get("requirementIds", []) if rid in state.get("examinationRequirementsById", {})]
    diag_orders = [state.get("diagnosticOrdersById", {}).get(oid) for oid in exam.get("diagnosticOrderIds", []) if oid in state.get("diagnosticOrdersById", {})]
    diag_results = [
        state.get("diagnosticResultsById", {}).get(rid)
        for o in diag_orders
        for rid in o.get("resultIds", [])
        if rid in state.get("diagnosticResultsById", {})
    ]
    dossier = (state.get("triageDossiersByEsrId") or {}).get(dbq["esrId"])

    if allow_llm is None:
        allow_llm = settings.intake_provider == "azure-openai"

    initial: ClinicalGraphState = {
        "mode": mode,
        "esr": esr,
        "veteran": veteran,
        "examination": exam,
        "dbq": dbq,
        "contentions": contentions,
        "requirements": requirements,
        "diagnostic_orders": diag_orders,
        "diagnostic_results": diag_results,
        "triage_dossier": dossier,
        "submitted_values": submitted_values or dict(dbq.get("values") or {}),
        "uploaded_dbq_file": uploaded_dbq_file,
        "uploaded_diagnostic_files": uploaded_diagnostic_files or [],
        "allow_llm": allow_llm,
    }
    return _CLINICAL_GRAPH.invoke(initial)


def ensure_clinical_records_for_exam(state: dict, exam: dict, req: dict, now: str) -> list[str]:
    """Ensure Examination has proper arrays, DBQs, DBQVersions, DiagnosticOrders, and Stage 4 WorkItems."""
    from uuid import uuid4

    esr_id = exam["esrId"]
    esr = state.get("esrsById", {}).get(esr_id) or {}
    req_ids = exam.get("requirementIds") or ([exam["requirementId"]] if exam.get("requirementId") else [])
    if req and req.get("id") and req["id"] not in req_ids:
        req_ids.append(req["id"])
    exam["requirementIds"] = req_ids

    appt_ids = exam.get("appointmentIds") or ([exam["appointmentId"]] if exam.get("appointmentId") else [])
    exam["appointmentIds"] = appt_ids

    contention_ids = exam.get("contentionIds") or list(req.get("contentionIds") or [])
    exam["contentionIds"] = contention_ids

    # Ensure DiagnosticOrders
    diag_order_ids = list(exam.get("diagnosticOrderIds") or [])
    if not diag_order_ids and req.get("requestedDiagnostics"):
        for diag_name in req.get("requestedDiagnostics", []):
            oid = f"diag-order-{uuid4().hex[:12]}"
            state.setdefault("diagnosticOrdersById", {})[oid] = {
                "id": oid,
                "version": 1,
                "createdAt": now,
                "updatedAt": now,
                "esrId": esr_id,
                "examinationId": exam["id"],
                "requirementId": req.get("id", ""),
                "orderType": diag_name,
                "status": "ordered",
                "required": True,
                "resultIds": [],
            }
            diag_order_ids.append(oid)
    exam["diagnosticOrderIds"] = diag_order_ids

    # Ensure DBQs + initial DBQVersion + Stage 4 WorkItems
    dbq_ids = list(exam.get("requiredDbqIds") or [])
    if not dbq_ids:
        dbq_types = list(req.get("requestedDbqTypes") or []) or [req.get("examinationType") or "General Medical DBQ"]
        refs = req.get("dbqCatalogReferences") or {}
        for form_type in dbq_types:
            dbq_id = f"dbq-{uuid4().hex[:12]}"
            ver_id = f"dbq-ver-{uuid4().hex[:12]}"
            state.setdefault("dbqVersionsById", {})[ver_id] = {
                "id": ver_id,
                "version": 1,
                "createdAt": now,
                "updatedAt": now,
                "dbqId": dbq_id,
                "versionNumber": 1,
                "formVersion": "synthetic-v2",
                "values": {},
                "authoredBy": "system",
                "changeSummary": "Initial DBQ draft created from Stage 3 scheduling confirmation.",
                "createdFor": "draft",
            }
            dbq_obj: dict[str, Any] = {
                "id": dbq_id,
                "version": 1,
                "createdAt": now,
                "updatedAt": now,
                "esrId": esr_id,
                "examinationId": exam["id"],
                "contentionIds": contention_ids,
                "formType": form_type,
                "formVersion": "synthetic-v2",
                "values": {},
                "versionIds": [ver_id],
                "currentVersionId": ver_id,
                "status": "draft",
                "validationErrors": [],
                "localReviewState": "not-ready",
            }
            if form_type in refs:
                dbq_obj["catalogReference"] = refs[form_type]
            state.setdefault("dbqsById", {})[dbq_id] = dbq_obj
            dbq_ids.append(dbq_id)

            w_key = f"work-clinical-{dbq_id}"
            state.setdefault("workItemsById", {})[w_key] = {
                "id": w_key,
                "version": 1,
                "createdAt": now,
                "updatedAt": now,
                "esrId": esr_id,
                "entityId": dbq_id,
                "entityType": "dbq",
                "stage": "Clinical Evaluation",
                "title": form_type,
                "priority": esr.get("priority", "routine"),
                "status": "queued",
                "blockingReasons": [],
                "assignedTo": esr.get("assignedTeamId"),
            }
    exam["requiredDbqIds"] = dbq_ids
    req_id = req.get("id")
    if req_id and req_id in (state.get("examinationRequirementsById") or {}):
        req_record = state["examinationRequirementsById"][req_id]
        req_record["dbqIds"] = list(dict.fromkeys([*(req_record.get("dbqIds") or []), *dbq_ids]))
        req_record["diagnosticOrderIds"] = list(dict.fromkeys([*(req_record.get("diagnosticOrderIds") or []), *diag_order_ids]))

    # Cancel legacy exam-level Stage 4 workItem if DBQ-level workItems now exist
    legacy_w_key = f"work-clinical-{exam['id']}"
    if legacy_w_key in (state.get("workItemsById") or {}) and dbq_ids:
        del state["workItemsById"][legacy_w_key]

    return dbq_ids


def with_clinical_view(state: dict) -> dict:
    """Attach case-grouped Stage 4 Clinical Evaluation summary without bloating persisted snapshot."""
    cases_by_esr: dict[str, dict[str, Any]] = {}
    queue_ids: list[str] = []

    for work in (state.get("workItemsById") or {}).values():
        if work.get("stage") != "Clinical Evaluation" or work.get("status") == "cancelled":
            continue
        dbq = (state.get("dbqsById") or {}).get(work.get("entityId"))
        if not dbq:
            continue
        esr_id = dbq.get("esrId")
        esr = (state.get("esrsById") or {}).get(esr_id)
        if not esr:
            continue
        if esr_id not in cases_by_esr:
            vet = (state.get("veteransById") or {}).get(esr.get("veteranId")) or {}
            dossier = (state.get("triageDossiersByEsrId") or {}).get(esr_id) or {}
            cases_by_esr[esr_id] = {
                "esrId": esr_id,
                "claimantName": vet.get("displayName") or esr.get("claimantName") or "Claimant",
                "claimIdentifier": esr.get("sourceReference") or vet.get("claimIdentifier") or esr_id,
                "priority": esr.get("priority", "routine"),
                "overallMedicalSummary": dossier.get("overallMedicalSummary", ""),
                "dbqWorkIds": [],
                "dbqIds": [],
                "signedCount": 0,
                "totalCount": 0,
            }
            queue_ids.append(esr_id)
        case = cases_by_esr[esr_id]
        case["dbqWorkIds"].append(work["id"])
        case["dbqIds"].append(dbq["id"])
        case["totalCount"] += 1
        if dbq.get("signedVersionId") and dbq.get("signedVersionId") == dbq.get("currentVersionId"):
            case["signedCount"] += 1

    return {
        **state,
        "clinical": {
            "queueIds": queue_ids,
            "casesByEsrId": cases_by_esr,
        },
    }
