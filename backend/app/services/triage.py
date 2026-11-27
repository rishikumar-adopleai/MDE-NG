"""Stage 2 Medical Triage & Examination Planning state & commands."""

from __future__ import annotations

import hashlib
from uuid import uuid4

from fastapi import HTTPException
from pydantic import BaseModel, Field, ValidationError

from app.core.config import settings
from app.services.intake_store import record, refresh_intake_work, timestamp, touch
from app.services.intake_view import TRIAGE_ACTIVE_STATES, has_readable_evidence, linked_documents
from app.services.triage_pipeline import STAGE2_PROMPT_VERSION, run_triage_pipeline_for_state
from app.services.va_dbq_catalog import catalog_references, requested_form_label
from app.services.va_rules import (
    ADDITIONAL_DBQS,
    EXAM_ALIASES,
    EXAM_CATALOG,
    RECORDS_REVIEW_MODALITIES,
    RULES_VERSION,
    STANDARD_MODALITIES,
)

TRIAGE_COMMANDS = frozenset({
    "saveTriageDraft",
    "approveTriagePlan",
    "requestTriageClarification",
    "resumeTriageReview",
    "authorizeTriageAce",
    "escalateDistressAlert",
})


class Plan(BaseModel):
    examCode: str = Field(default="", max_length=32)
    modality: str = Field(default="", max_length=40)
    dbqs: list[str] = Field(default_factory=list, max_length=8)
    diagnostics: list[str] = Field(default_factory=list, max_length=8)
    rationale: str = Field(default="", max_length=3000)
    scopeChangeAuthorized: bool = False
    remandAuthorizationReference: str = Field(default="", max_length=120)
    unverifiedRuleAcknowledged: bool = False


def _allowed_dbqs_for_exam(code: str, primary_dbq: str, item: dict | None = None) -> list[str]:
    extras = [*ADDITIONAL_DBQS]
    if code in {"knee", "back", "neck", "shoulder"}:
        extras.append("Peripheral Nerves DBQ")
    if item:
        for name in item.get("requestedDbqNames", []):
            if (label := requested_form_label(name)) and label not in extras and label != primary_dbq:
                extras.append(label)
    return list(dict.fromkeys([primary_dbq, *extras]))


def exam_choices() -> list[dict]:
    choices = [
        {
            "code": code,
            "title": item[0],
            "specialty": item[1],
            "credentials": item[2],
            "primaryDbq": item[3],
            "dbqs": _allowed_dbqs_for_exam(code, item[3]),
            "modalities": item[4],
            "diagnostics": item[5],
        }
        for code, item in EXAM_CATALOG.items()
    ]
    choices.append({
        "code": "dynamic",
        "title": "Dynamic / Unmapped Examination",
        "specialty": "Unverified — Dynamic Examination Requirement",
        "credentials": ["Authorized clinical verification required (MDEO Handbook)"],
        "primaryDbq": "",
        "dbqs": [*ADDITIONAL_DBQS],
        "modalities": STANDARD_MODALITIES,
        "diagnostics": ["Imaging report review", "Diagnostic study review", "Laboratory panel review"],
    })
    return choices


def permitted_modalities(item: dict, modalities: list[str], dossier_packet: dict | None = None) -> list[str]:
    allowed = list(modalities)
    rules_permitted = ((dossier_packet or {}).get("rulesEval") or {}).get("permittedModalities")
    if isinstance(rules_permitted, list) and rules_permitted:
        filtered = [m for m in allowed if m in rules_permitted]
        if filtered:
            allowed = filtered
    if isinstance(reference := item.get("aceAuthorizationReference"), str) and reference.strip():
        return allowed
    return [modality for modality in allowed if modality != "ace-records-review"]


def compute_contention_evidence_digest(state: dict, item: dict) -> str:
    esr = state.get("esrsById", {}).get(item.get("esrId", ""), {})
    docs = [
        doc for doc in linked_documents(state, item)
        if doc.get("classificationStatus") != "excluded" and not doc.get("isExcluded")
    ]
    membership = ",".join(sorted(item.get("evidenceDocumentIds") or []))
    doc_parts = []
    for d in sorted(docs, key=lambda x: x["id"]):
        sec_bounds = ",".join(
            f"{s.get('sectionId', '')}:{s.get('startPage', '')}-{s.get('endPage', '')}:{s.get('status', '')}"
            for s in (d.get("sections") or [])
        )
        page_nums = ",".join(str(p.get("page", "")) for p in ((d.get("processing") or {}).get("allPages") or []))
        doc_parts.append(
            f"{d['id']}:{d.get('sourceHash', '')}:{d.get('version', 1)}:{d.get('classificationStatus', '')}:"
            f"{d.get('classifiedPageCount', 0)}/{d.get('pageCount', 0)}:[{sec_bounds}]:[{page_nums}]"
        )
    raw = "|".join([
        f"esr:{esr.get('id', '')}:v{esr.get('version', 1)}",
        f"rules:{RULES_VERSION}",
        f"membership:{membership}",
        *doc_parts,
    ])
    return hashlib.sha256(raw.encode()).hexdigest()


def invalidate_stale_triage_plans(state: dict, esr_id: str, now: str, reason: str) -> list[str]:
    """Invalidate any approved triage plan whose linked evidence, ESR version, or rule version changed."""
    esr = state.get("esrsById", {}).get(esr_id)
    if not esr:
        return []
    invalidated: list[str] = []
    for cid in esr.get("contentionIds", []):
        item = state.get("contentionsById", {}).get(cid)
        if not item or item.get("simulatedDisposition") == "cancelled":
            continue
        approved_digest = item.get("approvedEvidenceDigest")
        if not approved_digest:
            continue
        current_digest = compute_contention_evidence_digest(state, item)
        if (
            current_digest == approved_digest
            and item.get("approvedEsrVersion", esr.get("version", 1)) == esr.get("version", 1)
            and item.get("approvedRuleVersion", RULES_VERSION) == RULES_VERSION
        ):
            continue
        for req_id in item.get("examinationRequirementIds", []):
            req = state.get("examinationRequirementsById", {}).get(req_id)
            if req and req.get("status") not in {"superseded-by-evidence-revision", "cancelled"}:
                touch(req, now, status="superseded-by-evidence-revision", invalidatedAt=now, invalidationReason=reason)
            sched_key = f"work-scheduling-{req_id}"
            sched_work = state.get("workItemsById", {}).get(sched_key)
            if sched_work and sched_work.get("status") in {"queued", "in-progress", "blocked", "completed"}:
                touch(sched_work, now, status="cancelled", blockingReasons=[reason])
            for appt in state.get("appointmentsById", {}).values():
                if req_id in (appt.get("requirementIds") or []) and appt.get("status") in {"held", "confirmed", "confirmed-locally"}:
                    touch(appt, now, status="cancelled", invalidationReason=reason, cancelledAt=now)
        touch(
            item, now,
            status="awaiting-review",
            triageStaleReason=reason,
            approvedEvidenceDigest=None,
            aceAuthorizationReference=None,
            aceAuthorizedBy=None,
            aceAuthorizedAt=None,
            aceAuthorizationRationale=None,
            triageDraft=None,
        )
        key = f"activity-{uuid4()}"
        state.setdefault("activitiesById", {})[key] = {
            "id": key,
            "esrId": esr_id,
            "entityId": cid,
            "entityType": "contention",
            "at": now,
            "actorId": "system-evidence-guard",
            "action": "triage-plan-invalidated",
            "rationale": reason,
        }
        invalidated.append(cid)
    if invalidated:
        (state.get("triageDossiersByEsrId") or {}).pop(esr_id, None)
        from app.services.qa_rules import invalidate_stale_qa_reviews
        invalidate_stale_qa_reviews(
            state,
            esr_id,
            now,
            reason,
            affected_contention_ids=set(invalidated),
        )
    return invalidated


def initial_plan(item: dict, dossier_packet: dict | None = None) -> Plan:
    if "triageDraft" in item:
        return Plan.model_validate(item["triageDraft"])
    title = item["title"].casefold()
    requests = [label for name in item.get("requestedDbqNames", []) if (label := requested_form_label(name))]
    candidates = [code for code, mapping in EXAM_CATALOG.items() if mapping[3] in requests]
    code = candidates[0] if len(candidates) == 1 else ""
    if not candidates:
        code = next((c for c, terms in EXAM_ALIASES.items() if any(term in title for term in terms)), "")
    if not code and dossier_packet:
        suggested = (dossier_packet.get("examSuggestion") or {}).get("examCode") or ""
        if suggested in EXAM_CATALOG:
            code = suggested
    mapped = EXAM_CATALOG.get(code)
    forms = list(dict.fromkeys([mapped[3], *(label for label in requests if label in ADDITIONAL_DBQS)])) if mapped else []
    va_pathway = ((dossier_packet or {}).get("rulesEval") or {}).get("vaSpecifiedPathway") or ""
    allowed = permitted_modalities(item, mapped[4]) if mapped else []
    selected_modality = va_pathway if va_pathway and va_pathway in allowed else ""
    return Plan(examCode=code, modality=selected_modality, dbqs=forms, diagnostics=[])


def _is_simulated_esr(esr: dict, vet: dict) -> bool:
    is_sim = esr.get("isSimulated")
    if is_sim is not None:
        return bool(is_sim)
    return bool(
        esr.get("id", "").startswith("esr-local")
        or "syn" in esr.get("id", "").lower()
        or "example" in (vet.get("displayName") or "").lower()
        or "demo" in (vet.get("displayName") or "").lower()
    )


def _ensure_dossier(state: dict, esr_id: str) -> dict:
    existing = (state.get("triageDossiersByEsrId") or {}).get(esr_id)
    esr = (state.get("esrsById") or {}).get(esr_id) or {}
    proc_status = (esr.get("triageProcessing") or {}).get("status")
    if existing and existing.get("ruleVersion") == RULES_VERSION and (
        existing.get("promptVersion") == STAGE2_PROMPT_VERSION or proc_status == "completed"
    ):
        return existing
    if proc_status in {"queued", "processing"}:
        return existing or {}
    # Compute lightweight deterministic NLP + VA Rules dossier on-the-fly without mutating snapshot revision
    scratch = {
        "esrsById": state.get("esrsById", {}),
        "veteransById": state.get("veteransById", {}),
        "evidenceDocumentsById": state.get("evidenceDocumentsById", {}),
        "contentionsById": state.get("contentionsById", {}),
        "clinicalFactsById": dict(state.get("clinicalFactsById", {})),
        "triageDossiersByEsrId": dict(state.get("triageDossiersByEsrId", {})),
    }
    return run_triage_pipeline_for_state(scratch, esr_id, allow_llm=False) or {}


def with_triage_view(state: dict) -> dict:
    rows = []
    active_esr_ids: dict[str, str] = {}

    for work in state.get("workItemsById", {}).values():
        if work.get("stage") != "Medical Triage" or work.get("status") in {"completed", "cancelled"}:
            continue
        item = state.get("contentionsById", {}).get(work["entityId"])
        esr = state.get("esrsById", {}).get(item["esrId"]) if item else None
        if not esr or esr.get("workflowClosedAt") or esr.get("intakeStatus") not in {"ready-for-triage", "completed"}:
            continue
        if item.get("status") not in TRIAGE_ACTIVE_STATES or item.get("simulatedDisposition") == "cancelled":
            continue
        active_esr_ids.setdefault(esr["id"], work.get("priority", esr.get("priority", "routine")))

        dossier = _ensure_dossier(state, esr["id"])
        packet = (dossier.get("contentionsById") or {}).get(item["id"], {})
        documents = [
            doc for doc in linked_documents(state, item)
            if doc.get("classificationStatus") != "excluded" and not doc.get("isExcluded")
        ]
        awaiting_response = item["status"] == "clarification-required"
        vet = state.get("veteransById", {}).get(esr.get("veteranId"), {})
        is_sim = _is_simulated_esr(esr, vet)
        plan_obj = initial_plan(item, packet)

        rows.append({
            "contentionId": item["id"],
            "esrId": item["esrId"],
            "priority": work["priority"],
            "documentIds": [doc["id"] for doc in documents],
            "readableEvidence": has_readable_evidence(documents),
            "awaitingResponse": awaiting_response,
            "requestedDbqs": item.get("requestedDbqNames", []),
            "aceAuthorized": bool(item.get("aceAuthorizationReference")),
            "aceAuthorizationReference": item.get("aceAuthorizationReference") or "",
            "triageStaleReason": item.get("triageStaleReason") or "",
            "currentEvidenceDigest": compute_contention_evidence_digest(state, item),
            "pathwaysByExam": {
                **{
                    code: permitted_modalities(item, exam[4], packet if code == (packet.get("examSuggestion") or {}).get("examCode") else None)
                    for code, exam in EXAM_CATALOG.items()
                },
                "dynamic": permitted_modalities(item, STANDARD_MODALITIES, packet),
            },
            "initialPlan": plan_obj.model_dump(),
            "specialInstructions": esr.get("specialInstructions") or "",
            "serviceBranch": vet.get("serviceGroup") or "",
            "isSimulated": is_sim,
            "dossier": packet,
        })

    # Build case-grouped Stage 2 view (including approved contentions on active triage cases)
    rows_by_contention = {r["contentionId"]: r for r in rows}
    cases_by_esr: dict[str, dict] = {}
    queue_order = list(active_esr_ids.keys())

    for esr_id in queue_order:
        esr = state["esrsById"][esr_id]
        vet = state.get("veteransById", {}).get(esr.get("veteranId"), {})
        dossier = _ensure_dossier(state, esr_id)
        triage_proc = esr.get("triageProcessing") or {
            "status": "completed",
            "factCount": dossier.get("factCount", 0),
            "ruleVersion": RULES_VERSION,
        }
        claimant_name = vet.get("displayName") or esr.get("claimantName") or esr_id
        initials = vet.get("initials") or "".join(w[0].upper() for w in claimant_name.split()[:2]) or "VA"

        case_contentions = []
        approved_count = 0
        pending_count = 0
        clarification_count = 0
        for index, cid in enumerate(esr.get("contentionIds", []), 1):
            c_item = state.get("contentionsById", {}).get(cid)
            if not c_item or c_item.get("simulatedDisposition") == "cancelled":
                continue
            packet = (dossier.get("contentionsById") or {}).get(cid, {})
            req_ids = c_item.get("examinationRequirementIds") or []
            latest_req = state.get("examinationRequirementsById", {}).get(req_ids[-1]) if req_ids else None
            c_status = c_item.get("status", "awaiting-review")
            if c_status == "approved-for-exam":
                approved_count += 1
            else:
                pending_count += 1
                if c_status == "clarification-required":
                    clarification_count += 1
            case_contentions.append({
                "index": index,
                "contentionId": cid,
                "title": c_item.get("title", ""),
                "status": c_status,
                "requestedDbqs": c_item.get("requestedDbqNames", []),
                "aceAuthorized": bool(c_item.get("aceAuthorizationReference")),
                "aceAuthorizationReference": c_item.get("aceAuthorizationReference") or "",
                "approvedRequirement": latest_req,
                "row": rows_by_contention.get(cid),
                "dossier": packet,
            })

        cases_by_esr[esr_id] = {
            "esrId": esr_id,
            "claimantName": claimant_name,
            "initials": initials,
            "claimIdentifier": vet.get("claimIdentifier") or esr.get("sourceReference") or esr_id,
            "serviceBranch": vet.get("serviceGroup") or "",
            "benefitProgram": (esr.get("examManagement") or {}).get("program") or "Compensation & Pension",
            "priority": esr.get("priority", "routine"),
            "specialInstructions": esr.get("specialInstructions") or "",
            "isSimulated": _is_simulated_esr(esr, vet),
            "processing": triage_proc,
            "caseOverview": dossier.get("caseOverview", ""),
            "caseCitations": dossier.get("caseCitations", []),
            "aiProvider": dossier.get("aiProvider", "local"),
            "distressAlerts": dossier.get("distressAlerts", []),
            "programFlags": dossier.get("programFlags") or {},
            "requestedCount": len(case_contentions),
            "approvedCount": approved_count,
            "pendingCount": pending_count,
            "clarificationCount": clarification_count,
            "contentions": case_contentions,
        }

    return {
        **state,
        "triage": {
            "rows": rows,
            "queueIds": queue_order,
            "casesByEsrId": cases_by_esr,
            "examChoices": exam_choices(),
            "ruleVersion": RULES_VERSION,
        },
    }


def _serialize_plan(plan: Plan) -> dict:
    data = {
        "examCode": plan.examCode,
        "modality": plan.modality,
        "dbqs": plan.dbqs,
        "diagnostics": plan.diagnostics,
        "rationale": plan.rationale,
    }
    if plan.scopeChangeAuthorized:
        data["scopeChangeAuthorized"] = True
    if plan.remandAuthorizationReference:
        data["remandAuthorizationReference"] = plan.remandAuthorizationReference
    if plan.unverifiedRuleAcknowledged:
        data["unverifiedRuleAcknowledged"] = True
    return data


def _dynamic_mapping_for_item(item: dict, dossier_packet: dict | None) -> tuple[str, str, list[str], str, list[str], list[str]]:
    suggestion = (dossier_packet or {}).get("examSuggestion") or {}
    req_dbqs = item.get("requestedDbqNames") or suggestion.get("suggestedDbqs") or [f"{item.get('title', 'Clinical')} DBQ"]
    primary_dbq = req_dbqs[0]
    return (
        item.get("title") or "Dynamic Examination",
        suggestion.get("specialty") or "Unverified — Dynamic Examination Requirement",
        suggestion.get("credentials") or ["Authorized clinical verification required (MDEO Handbook)"],
        primary_dbq,
        STANDARD_MODALITIES,
        ["Imaging report review", "Diagnostic study review", "Laboratory panel review"],
    )


def read_plan(payload: object, item: dict, dossier_packet: dict | None = None) -> Plan:
    try:
        plan = Plan.model_validate(payload)
    except ValidationError as exc:
        raise HTTPException(422, "Enter a valid examination plan.") from exc
    is_dynamic = plan.examCode == "dynamic"
    mapping = _dynamic_mapping_for_item(item, dossier_packet) if is_dynamic else EXAM_CATALOG.get(plan.examCode)
    if (plan.examCode and not mapping) or (not mapping and (plan.modality or plan.dbqs or plan.diagnostics)):
        raise HTTPException(422, "Select an examination type from the catalog.")
    allowed_modalities = permitted_modalities(
        item, mapping[4],
        dossier_packet if mapping and (is_dynamic or plan.examCode == (dossier_packet or {}).get("examSuggestion", {}).get("examCode")) else None,
    ) if mapping else []
    allowed_dbqs = _allowed_dbqs_for_exam(plan.examCode, mapping[3], item) if mapping else []
    if is_dynamic:
        allowed_dbqs = list(dict.fromkeys([*allowed_dbqs, *(item.get("requestedDbqNames") or []), *plan.dbqs[:2]]))
    if mapping and (
        (plan.modality and plan.modality not in allowed_modalities)
        or any(dbq not in allowed_dbqs for dbq in plan.dbqs)
        or any(diagnostic not in mapping[5] for diagnostic in plan.diagnostics)
    ):
        raise HTTPException(422, "Use only DBQs, pathways, and diagnostics offered for this examination.")
    plan.dbqs = list(dict.fromkeys(plan.dbqs))
    plan.diagnostics = list(dict.fromkeys(plan.diagnostics))
    plan.rationale = plan.rationale.strip()
    return plan


def apply_triage_command(state: dict, action: str, args: list, actor: str) -> dict:
    if len(args) != 2 or not isinstance(args[0], str):
        raise HTTPException(422, "Select a contention and provide review details.")
    item = state["contentionsById"].get(args[0])
    esr = state["esrsById"].get(item["esrId"]) if item else None
    if not esr or esr.get("workflowClosedAt") or esr["intakeStatus"] not in {"ready-for-triage", "completed"}:
        raise HTTPException(422, "Intake must be validated before a contention can enter Triage.")
    if item["status"] not in TRIAGE_ACTIVE_STATES or item.get("simulatedDisposition") == "cancelled":
        raise HTTPException(409, "This contention has already left Triage.")
    if item["status"] == "clarification-required" and action != "resumeTriageReview":
        raise HTTPException(422, "Record the clarification response before continuing review.")

    now = timestamp()
    value: dict = {}
    if action in {"requestTriageClarification", "resumeTriageReview"}:
        note = args[1].strip() if isinstance(args[1], str) else ""
        if not note or len(note) > 3000:
            raise HTTPException(422, "Enter a clarification note of up to 3,000 characters.")
        if action == "resumeTriageReview":
            if item["status"] != "clarification-required":
                raise HTTPException(422, "No clarification is pending for this contention.")
            touch(item, now, status="in-review", clarificationReason=None, clarificationResponse=note)
            event = "triage-clarification-answered"
        else:
            touch(item, now, status="clarification-required", clarificationReason=note, clarificationResponse=None)
            event = "triage-contention-clarified"
    elif action == "escalateDistressAlert":
        note = args[1].strip() if isinstance(args[1], str) else ""
        if not note or len(note) > 3000:
            raise HTTPException(422, "Record escalation details for the safety alert.")
        dossier = state.get("triageDossiersByEsrId", {}).get(esr["id"])
        if dossier:
            for alert in dossier.get("distressAlerts", []):
                alert["escalationStatus"] = "escalated"
                alert["escalatedBy"] = actor
                alert["escalatedAt"] = now
                alert["escalationNote"] = note
        touch(item, now, distressEscalatedAt=now, distressEscalatedBy=actor, distressEscalationNote=note)
        event = "triage-safety-alert-escalated"
    elif action == "authorizeTriageAce":
        note = args[1].strip() if isinstance(args[1], str) else ""
        if not note or len(note) > 3000:
            raise HTTPException(422, "Enter a clinical justification before authorizing ACE eligibility.")
        dossier = _ensure_dossier(state, esr["id"])
        packet = (dossier.get("contentionsById") or {}).get(item["id"], {})
        rules_eval = packet.get("rulesEval") or {}
        if rules_eval and rules_eval.get("aceEligible") is False:
            raise HTTPException(
                422,
                rules_eval.get("aceEligibilityReason") or "ACE records review is not eligible for this contention.",
            )
        auth_ref = f"CLIN-ACE-{uuid4().hex[:8].upper()}"
        touch(item, now, aceAuthorizationReference=auth_ref, aceAuthorizedBy=actor, aceAuthorizedAt=now,
              aceAuthorizationRationale=note, status="in-review")
        event = "triage-contention-reviewed"
        value["aceAuthorizationReference"] = auth_ref
    else:
        dossier = _ensure_dossier(state, esr["id"])
        packet = (dossier.get("contentionsById") or {}).get(item["id"], {})
        rules_eval = packet.get("rulesEval") or {}
        plan = read_plan(args[1], item, packet)
        note = plan.rationale
        if action == "saveTriageDraft":
            if plan.examCode and plan.examCode != "dynamic" and EXAM_CATALOG[plan.examCode][3] not in plan.dbqs:
                plan.dbqs.insert(0, EXAM_CATALOG[plan.examCode][3])
            touch(item, now, triageDraft=_serialize_plan(plan), status="in-review")
            event = "triage-plan-draft-saved"
        else:
            docs = linked_documents(state, item)
            if not docs or (plan.modality == "ace-records-review" and not has_readable_evidence(docs)):
                raise HTTPException(422, "Approval requires readable evidence linked to this contention.")
            if not plan.examCode or not plan.modality or not note:
                raise HTTPException(422, "Select an examination, pathway, and at least one DBQ, and record your rationale.")
            mapping = _dynamic_mapping_for_item(item, packet) if plan.examCode == "dynamic" else EXAM_CATALOG[plan.examCode]
            if mapping[3] and mapping[3] not in plan.dbqs and plan.examCode != "dynamic":
                raise HTTPException(422, "Include the primary DBQ required for this examination.")
            if not plan.dbqs:
                raise HTTPException(422, "Include at least one DBQ for this examination.")

            # Enforce authoritative VA ESR pathway instruction when present
            va_pathway = rules_eval.get("vaSpecifiedPathway") or ""
            if va_pathway and plan.modality != va_pathway:
                raise HTTPException(
                    422,
                    f"Authoritative VA ESR instructions require the '{va_pathway}' examination pathway.",
                )

            # Enforce BVA Remand guard (RFP 5.1.1.2): require structured scopeChangeAuthorized + reference (no keyword matching)
            is_bva = (dossier.get("programFlags") or {}).get("isBvaRemand", False)
            req_canon = [label for name in item.get("requestedDbqNames", []) if (label := requested_form_label(name))]
            if is_bva and req_canon and not all(req in plan.dbqs for req in req_canon):
                if not (plan.scopeChangeAuthorized and plan.remandAuthorizationReference.strip()):
                    raise HTTPException(
                        422,
                        "BVA Remand claims require preserving all VA-requested DBQs unless structured scope-change authorization and reference are recorded.",
                    )

            # Enforce verified VA rules or VA clarification uniformly (production-only standard)
            if rules_eval.get("ruleVerificationStatus") == "unverified-requires-review":
                raise HTTPException(
                    422,
                    "Verified applicable VA rules or VA clarification are required before approval.",
                )
            approval_mode = "verified-va-rules"

            evidence_digest = compute_contention_evidence_digest(state, item)
            active_docs_snapshot = [
                {
                    "id": d["id"],
                    "title": d.get("title", ""),
                    "version": d.get("version", 1),
                    "sourceHash": d.get("sourceHash", ""),
                }
                for d in docs
                if d.get("classificationStatus") != "excluded" and not d.get("isExcluded")
            ]

            requirement_id = f"req-{uuid4().hex}"
            requirement = {
                **record(requirement_id, now),
                "esrId": esr["id"],
                "contentionIds": [item["id"]],
                "title": f"{mapping[0]} examination",
                "examinationType": mapping[0],
                "requiredSpecialties": [mapping[1]],
                "requiredCredentials": mapping[2],
                "permittedModalities": permitted_modalities(item, mapping[4], packet),
                "selectedModality": plan.modality,
                "requestedDbqTypes": plan.dbqs,
                "requestedDiagnostics": plan.diagnostics,
                "dbqIds": [],
                "diagnosticOrderIds": [],
                "status": "approved",
                "reviewerRationale": note,
                "approvedBy": actor,
                "approvedAt": now,
                "ruleVersion": RULES_VERSION,
                "approvedRuleVersion": RULES_VERSION,
                "approvedEsrVersion": esr.get("version", 1),
                "ruleVerificationStatus": rules_eval.get("ruleVerificationStatus", "verified"),
                "approvalMode": approval_mode,
                "approvedEvidenceDigest": evidence_digest,
                "approvedEvidenceDocuments": active_docs_snapshot,
                "specialInstructions": esr.get("specialInstructions") or "",
            }
            if item.get("aceAuthorizationReference"):
                requirement["aceAuthorizationReference"] = item["aceAuthorizationReference"]
            references = catalog_references(plan.dbqs) if esr.get("examManagement") or esr.get("practiceScenario") else {}
            if references:
                requirement["dbqCatalogReferences"] = references
            state["examinationRequirementsById"][requirement_id] = requirement
            touch(
                item, now,
                status="approved-for-exam",
                triageDraft=_serialize_plan(plan),
                reviewerRationale=note,
                clarificationReason=None,
                triageStaleReason=None,
                approvedEvidenceDigest=evidence_digest,
                approvedEsrVersion=esr.get("version", 1),
                approvedRuleVersion=RULES_VERSION,
                examinationRequirementIds=[*item["examinationRequirementIds"], requirement_id],
            )
            key = f"work-scheduling-{requirement_id}"
            state["workItemsById"][key] = {
                **record(key, now),
                "esrId": esr["id"],
                "entityId": requirement_id,
                "entityType": "examination-requirement",
                "stage": "Scheduling",
                "title": requirement["title"],
                "priority": esr["priority"],
                "status": "queued",
                "blockingReasons": [],
                "assignedTo": esr.get("assignedTeamId"),
            }
            event = "triage-contention-approved"
            value["entityId"] = requirement_id

    key = f"activity-{uuid4()}"
    state["activitiesById"][key] = {
        "id": key,
        "esrId": esr["id"],
        "entityId": item["id"],
        "entityType": "contention",
        "at": now,
        "actorId": actor,
        "action": event,
        "rationale": note,
    }
    refresh_intake_work(state, esr["id"], now)
    state.update(revision=state["revision"] + 1, updatedAt=now)
    return {"state": state, "value": value}
