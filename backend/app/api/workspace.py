import hashlib
import json
from typing import Annotated, Literal
from uuid import UUID

from fastapi import APIRouter, Depends, File, Header, HTTPException, UploadFile
from pydantic import BaseModel, Field, JsonValue
from sqlalchemy.orm import Session

from app.core.config import settings
from app.core.database import get_db
from app.core.security import CurrentUser, get_current_user
from app.models import IdempotencyRecord, OutboxJob
from app.services.clinical_pipeline import run_clinical_pipeline, with_clinical_view
from app.services.intake_store import INTAKE_COMMANDS, TRIAGE_TASK_NAME, apply_intake_command, enqueue_triage_job, timestamp
from app.services.intake_view import with_intake_views
from app.services.scheduling import (
    SCHEDULING_COMMANDS,
    apply_scheduling_command,
    find_coordinated_scheduling_matches,
    find_scheduling_matches,
    with_scheduling_view,
)
from app.services.stage_ai import STAGE_TASKS, generate_stage_review
from app.services.triage import TRIAGE_COMMANDS, apply_triage_command, with_triage_view
from app.services.triage_pipeline import run_triage_pipeline_for_state
from app.services.va_dbq_catalog import reference_forms
from app.services.workspace_store import get_state, run_engine, save_next_state

router = APIRouter(tags=["Workspace"])

COMMAND_ROLES = {
    **dict.fromkeys(("reviewIntakeException", "recordIntakeReceipt",
                     "completeDocumentProcessing", "validateIntake", "updateDocumentCategory"), "intake"),
    **dict.fromkeys(TRIAGE_COMMANDS, "case-builder"),
    **dict.fromkeys(("recordOutreach", "holdAppointment", "holdCoordinatedAppointments", "confirmAppointment",
                     "setSchedulingDistanceLimit", "recordAppointmentLogistics",
                     "verifyVeteranLocation", "rescheduleAppointment", "cancelAppointment"), "scheduler"),
    **dict.fromkeys(("saveDBQDraft", "validateDBQ", "reviewDiagnostic", "recordDiagnosticResult",
                     "completeClinicalReview", "recordExamEncounter", "recordDiagnosticException", "resubmitCorrection",
                     "returnClinicalToScheduling"), "clinician"),
    **dict.fromkeys(("requestCorrection", "addQAFinding", "resolveQAFinding",
                     "approveQAReview", "runQAChecklist"), "qa"),
    **dict.fromkeys(("ensureResultsPackage", "validateResultsPackage", "prepareResultsPackage",
                     "createReconciliationTask", "completeReconciliationTask", "recordDeliveryReview", "simulatePackageTransmission",
                     "recordSimulatedPackageResponse", "recordSimulatedContentionCancellation",
                     "uploadContentionDocuments", "sendContentionRap", "createLinkedReworkESR",
                     "assessReworkESR", "updateReworkRootCause",
                     "closeESR"), "delivery"),
    "decideInsight": "admin",
}


class Command(BaseModel):
    action: str = Field(max_length=55)
    args: list[JsonValue] = Field(max_length=8)
    revision: int = Field(ge=0)


class AIReviewRequest(BaseModel):
    stage: str = Field(max_length=40)
    entityId: str = Field(min_length=1, max_length=200)


class SchedulingMatchRequest(BaseModel):
    requirementIds: list[str] = Field(min_length=1, max_length=12)
    facilityId: str | None = Field(default=None, max_length=64)
    examinerId: str | None = Field(default=None, max_length=64)


class CoordinatedMatchRequest(BaseModel):
    esrId: str = Field(min_length=1, max_length=64)
    requirementIds: list[str] | None = None


def workspace_reader(user: CurrentUser = Depends(get_current_user)) -> CurrentUser:
    if settings.app_env == "prod" and user.role != "admin":
        raise HTTPException(403, "The aggregate workspace requires an administrator.")
    return user


def _with_all_views(state: dict) -> dict:
    return with_clinical_view(with_scheduling_view(with_triage_view(with_intake_views(state))))


@router.get("/runtime")
def runtime() -> dict:
    return {"mode": settings.app_env, "authRequired": settings.auth_required,
            "simulationEnabled": settings.app_env == "dev",
            "aiEnabled": settings.app_env == "dev" and settings.intake_provider == "azure-openai"}


@router.get("/workspace")
def get_workspace(db: Session = Depends(get_db),
                  _user: CurrentUser = Depends(workspace_reader)) -> dict:
    state = get_state(db, backfill_requests=False, load_practice=False)
    return _with_all_views(state) if settings.app_env == "dev" else state


@router.get("/workspace/work-items/{work_id}")
def get_work_item(work_id: str, db: Session = Depends(get_db),
                  _user: CurrentUser = Depends(workspace_reader)) -> dict:
    if settings.app_env != "dev":
        raise HTTPException(503, "Stage workbenches require configured production services.")
    state = get_state(db)
    outcome = run_engine({"action": "$stage-view", "args": [work_id], "state": state})
    if "error" in outcome:
        raise HTTPException(404, outcome["error"])
    detail = outcome.get("detail") if isinstance(outcome, dict) else None
    if isinstance(detail, dict) and "dbq" in detail and "examId" in detail:
        dbq = detail["dbq"]
        esr_id = dbq.get("esrId")
        exam = (state.get("examinationsById") or {}).get(detail["examId"]) or {}
        dossier = (state.get("triageDossiersByEsrId") or {}).get(esr_id) or {}
        packets = dossier.get("packetsByContentionId") or {}
        stage2_gaps = [
            gap
            for cid in dbq.get("contentionIds", [])
            for gap in ((packets.get(cid) or {}).get("rulesEval") or {}).get("evidenceGaps") or []
        ]
        appt = next(
            (
                (state.get("appointmentsById") or {}).get(aid)
                for aid in exam.get("appointmentIds", [])
                if ((state.get("appointmentsById") or {}).get(aid) or {}).get("status") in {"confirmed-locally", "completed"}
            ),
            None,
        )
        fac = (state.get("facilitiesById") or {}).get(appt.get("facilityId")) if appt else None
        detail["stage2Summary"] = dossier.get("overallMedicalSummary", "")
        detail["stage2Gaps"] = stage2_gaps
        detail["appointment"] = (
            {
                "id": appt["id"],
                "startsAt": appt.get("startsAt"),
                "endsAt": appt.get("endsAt"),
                "modality": appt.get("modality"),
                "status": appt.get("status"),
                "facilityName": (fac or {}).get("name") or appt.get("facilityName") or "",
                "examinerName": detail.get("examiner") or appt.get("examinerName") or "",
            }
            if appt
            else None
        )
        detail["uploadedDbqDocument"] = dbq.get("uploadedDbqDocument")
        detail["validationAlerts"] = dbq.get("validationAlerts", [])
        detail["draftMedicalReview"] = dbq.get("draftMedicalReview")
    return outcome


@router.get("/workspace/source-records")
def get_source_records(
    stage: Literal["Medical Triage", "Scheduling", "Clinical Evaluation", "Internal QA", "VA Delivery & Review"],
    _user: CurrentUser = Depends(workspace_reader),
) -> dict:
    if settings.app_env != "dev":
        raise HTTPException(404, "Reference records are unavailable in this workspace.")
    return {"events": [],
            "forms": reference_forms() if stage in {"Clinical Evaluation", "Internal QA"} else []}


@router.post("/workspace/ai-review")
def ai_review(body: AIReviewRequest, db: Session = Depends(get_db),
              _user: CurrentUser = Depends(workspace_reader)) -> dict:
    if settings.app_env != "dev":
        raise HTTPException(404, "AI review is unavailable in this workspace.")
    if body.stage not in STAGE_TASKS:
        raise HTTPException(422, "Choose a workflow stage.")
    if settings.intake_provider != "azure-openai":
        raise HTTPException(503, "AI review is not enabled for this workspace.")
    state = get_state(db)
    db.close()
    return generate_stage_review(state, body.stage, body.entityId)


@router.post("/workspace/commands")
def apply_command(
    body: Command,
    idempotency_key: Annotated[UUID, Header()],
    db: Session = Depends(get_db),
    user: CurrentUser = Depends(workspace_reader),
) -> dict:
    if settings.app_env != "dev":
        raise HTTPException(503, "This workflow action is unavailable.")
    role = COMMAND_ROLES.get(body.action)
    if role is None:
        raise HTTPException(422, "Unknown workspace action.")
    if user.role not in (role, "admin"):
        raise HTTPException(403, "This role cannot perform that action.")

    payload = json.dumps([user.user_id, body.action, body.args], sort_keys=True, separators=(",", ":"))
    fingerprint = hashlib.sha256(payload.encode()).hexdigest()
    state = get_state(db)
    previous = db.get(IdempotencyRecord, str(idempotency_key))
    if previous:
        if previous.response_json["fingerprint"] != fingerprint:
            raise HTTPException(409, "The command key was already used for a different operation.")
        return {"state": with_scheduling_view(with_triage_view(with_intake_views(state))), "value": previous.response_json["value"]}
    if body.revision != state["revision"]:
        raise HTTPException(409, "The workspace changed. Review the latest records and retry.")

    if body.action in {"requestCorrection", "resolveQAFinding", "approveQAReview", "runQAChecklist"} and body.args and isinstance(body.args[0], str):
        from app.services.qa_rules import check_independent_qa_reviewer, enforce_backend_qa_approval
        target_id = body.args[0]
        if body.action in {"requestCorrection", "resolveQAFinding"}:
            finding_obj = (state.get("qaFindingsById") or {}).get(target_id) or {}
            review_obj = (state.get("qaReviewsById") or {}).get(finding_obj.get("qaReviewId") or "")
        else:
            review_obj = (state.get("qaReviewsById") or {}).get(target_id)
        if review_obj:
            check_independent_qa_reviewer(state, review_obj, user.user_id, user.role)
        if body.action == "resolveQAFinding" and finding_obj.get("origin") == "deterministic":
            ck = finding_obj.get("checkKey")
            chk = next((c for c in (review_obj or {}).get("checks") or [] if c.get("key") == ck), None)
            if chk and chk.get("status") == "failed":
                raise HTTPException(
                    422,
                    f"Cannot manually resolve failed deterministic rule '{chk.get('ruleId') or ck}'. Return DBQ to Stage 4 or fix the underlying deficiency first.",
                )
        if body.action == "approveQAReview":
            note_arg = str(body.args[1]) if len(body.args) > 1 and isinstance(body.args[1], str) else ""
            override_arg = body.args[2] if len(body.args) > 2 else False
            approval_meta = enforce_backend_qa_approval(
                db, state, target_id, user.user_id, user.role, note_arg, override_arg
            )
        else:
            approval_meta = None
    else:
        approval_meta = None

    STAGE6_GUARDED_ACTIONS = {
        "validateResultsPackage", "prepareResultsPackage", "simulatePackageTransmission",
        "uploadContentionDocuments", "sendContentionRap", "createLinkedReworkESR",
        "assessReworkESR", "updateReworkRootCause", "closeESR",
    }
    if body.action in STAGE6_GUARDED_ACTIONS:
        from app.services.qa_rules import enforce_backend_stage6_delivery
        stage6_meta = enforce_backend_stage6_delivery(db, state, body.action, body.args, user.user_id, user.role)
    else:
        stage6_meta = {}

    if body.action in INTAKE_COMMANDS:
        outcome = apply_intake_command(state, body.action, body.args, user.user_id)
    elif body.action in TRIAGE_COMMANDS:
        outcome = apply_triage_command(state, body.action, body.args, user.user_id)
        # Release any Stage 3 DB slots belonging to appointments cancelled by Stage 2 evidence invalidation
        for appt in (outcome["state"].get("appointmentsById") or {}).values():
            if appt.get("status") == "cancelled" and appt.get("invalidationReason") and appt.get("slotId"):
                from sqlalchemy import update as sql_update
                from app.models import Stage3TimeSlot
                db.execute(
                    sql_update(Stage3TimeSlot)
                    .where(Stage3TimeSlot.slot_id == appt["slotId"])
                    .values(slot_status="available")
                )
    elif body.action in SCHEDULING_COMMANDS:
        outcome = apply_scheduling_command(db, state, body.action, body.args, user.user_id)
    else:
        outcome = run_engine({"action": body.action, "args": body.args, "state": state,
                              "actorId": user.user_id})
    if "error" in outcome:
        raise HTTPException(422, outcome["error"])
    next_state = outcome["state"]
    if body.action == "validateIntake" and body.args and isinstance(body.args[0], str):
        from app.services.triage_store import compute_current_esr_digest
        esr_id = body.args[0]
        enqueue_triage_job(
            db,
            esr_id,
            force=True,
            evidence_digest=compute_current_esr_digest(next_state, esr_id),
        )
    elif body.action == "completeClinicalReview" and body.args and isinstance(body.args[0], str):
        from app.services.qa_pipeline import enqueue_qa_audit_job
        dbq_id = body.args[0]
        qa_review = next(
            (r for r in (next_state.get("qaReviewsById") or {}).values() if r.get("dbqId") == dbq_id),
            None,
        )
        if qa_review:
            qa_review["qaProcessing"] = {"status": "queued", "queuedAt": timestamp(), "versionId": qa_review.get("versionId")}
            enqueue_qa_audit_job(db, qa_review["id"], force=True)
    elif body.action == "recordSimulatedPackageResponse" and len(body.args) >= 4 and body.args[1] == "report-fix" and isinstance(body.args[3], str):
        from app.services.qa_pipeline import enqueue_qa_audit_job
        dbq_id = body.args[3]
        qa_review = next(
            (r for r in (next_state.get("qaReviewsById") or {}).values() if r.get("dbqId") == dbq_id),
            None,
        )
        if qa_review:
            qa_review["qaProcessing"] = {"status": "queued", "queuedAt": timestamp(), "versionId": qa_review.get("versionId")}
            enqueue_qa_audit_job(db, qa_review["id"], force=True)
    elif body.action == "runQAChecklist" and body.args and isinstance(body.args[0], str):
        from app.services.qa_pipeline import record_qa_audit_event
        from app.services.qa_rules import run_deterministic_qa_rules
        review_id = body.args[0]
        if review_id in (next_state.get("qaReviewsById") or {}):
            res = run_deterministic_qa_rules(db, next_state, review_id)
            record_qa_audit_event(db, user.user_id, "qa-checklist-run", review_id, {
                "reviewId": review_id, "failedCount": res["failedCount"], "ruleVersion": res["ruleVersion"],
            })
    elif body.action == "approveQAReview" and body.args and isinstance(body.args[0], str):
        from app.services.qa_pipeline import record_qa_audit_event
        from app.services.qa_rules import compute_qa_evidence_digest
        review_id = body.args[0]
        approved_rev = (next_state.get("qaReviewsById") or {}).get(review_id)
        final_digest = compute_qa_evidence_digest(next_state, review_id) or (approval_meta or {}).get("evidenceDigest")
        if approved_rev and approval_meta:
            approved_rev["evidenceDigest"] = final_digest
            approved_rev["approvedEvidenceDigest"] = final_digest
            approved_rev["ruleVersion"] = approval_meta["ruleVersion"]
            approved_rev["isStale"] = False
            if isinstance(review_obj, dict) and review_obj.get("manualAiOverride"):
                approved_rev["manualAiOverride"] = review_obj["manualAiOverride"]
        record_qa_audit_event(db, user.user_id, "qa-review-approved", review_id, {
            "reviewId": review_id,
            "dbqId": (approved_rev or {}).get("dbqId"),
            "versionId": (approved_rev or {}).get("versionId"),
            "evidenceDigest": final_digest,
            "ruleVersion": (approval_meta or {}).get("ruleVersion"),
            "manualAiOverride": bool((approval_meta or {}).get("manualAiOverride")),
        })
    elif body.action in {"requestCorrection", "addQAFinding", "resolveQAFinding"} and body.args and isinstance(body.args[0], str):
        from app.services.qa_pipeline import record_qa_audit_event
        record_qa_audit_event(db, user.user_id, f"qa-{body.action}", body.args[0], {"targetId": body.args[0]})
    elif body.action in {"recordDiagnosticResult", "recordDiagnosticException"} and body.args and isinstance(body.args[0], str):
        from app.services.qa_rules import invalidate_stale_qa_reviews
        order = (next_state.get("diagnosticOrdersById") or {}).get(body.args[0]) or {}
        exam = (next_state.get("examinationsById") or {}).get(order.get("examinationId") or "") or {}
        if order.get("esrId") and exam.get("requiredDbqIds"):
            invalidate_stale_qa_reviews(
                next_state,
                order["esrId"],
                timestamp(),
                f"Diagnostic order '{order.get('testName', order.get('id'))}' updated.",
                affected_dbq_ids=set(exam.get("requiredDbqIds") or []),
            )
    save_next_state(db, body.revision, next_state)
    if body.action in {
        "ensureResultsPackage", "validateResultsPackage", "prepareResultsPackage",
        "uploadContentionDocuments", "sendContentionRap", "createLinkedReworkESR",
        "assessReworkESR", "updateReworkRootCause", "closeESR",
    }:
        from app.models import Stage6DeliveryOperation
        from app.services.qa_pipeline import record_qa_audit_event
        ref = str(body.args[0]) if body.args else "stage6"
        if stage6_meta.get("dbOpKind") and stage6_meta.get("dbOpKey"):
            db.add(Stage6DeliveryOperation(
                esr_id=str(stage6_meta.get("esrId") or ref)[:80],
                operation_kind=str(stage6_meta["dbOpKind"])[:64],
                idempotency_key=str(stage6_meta["dbOpKey"])[:160],
                status="completed",
                result_summary={"action": body.action, "actorId": user.user_id},
            ))
        record_qa_audit_event(
            db,
            user.user_id,
            f"stage6-{body.action}",
            ref[:64],
            {"action": body.action, "targetRef": ref[:64], "esrId": stage6_meta.get("esrId") or (outcome.get("value") or {}).get("reworkEsrId")},
        )
    db.add(IdempotencyRecord(key=str(idempotency_key), response_json={
        "fingerprint": fingerprint, "value": outcome["value"],
    }))
    db.commit()
    return {**outcome, "state": _with_all_views(next_state)}


@router.post("/scheduling/match")
def match_scheduling_options(
    body: SchedulingMatchRequest,
    db: Session = Depends(get_db),
    user: CurrentUser = Depends(workspace_reader),
) -> dict:
    if settings.app_env != "dev":
        raise HTTPException(503, "This workflow action is unavailable.")
    if user.role not in {"scheduler", "admin", "case-builder"}:
        raise HTTPException(403, "This role cannot search scheduling matches.")
    state = get_state(db, backfill_requests=False, load_practice=False)
    return find_scheduling_matches(
        db, state, body.requirementIds, facility_id=body.facilityId, examiner_id=body.examinerId
    )


@router.post("/scheduling/match-coordinated")
def match_coordinated_scheduling(
    body: CoordinatedMatchRequest,
    db: Session = Depends(get_db),
    user: CurrentUser = Depends(workspace_reader),
) -> dict:
    if settings.app_env != "dev":
        raise HTTPException(503, "This workflow action is unavailable.")
    if user.role not in {"scheduler", "admin", "case-builder"}:
        raise HTTPException(403, "This role cannot search scheduling matches.")
    state = get_state(db, backfill_requests=False, load_practice=False)
    return find_coordinated_scheduling_matches(
        db, state, body.esrId, requirement_ids=body.requirementIds
    )


@router.post("/triage/esrs/{esr_id}/prepare")
def prepare_triage_dossier(
    esr_id: str,
    force: bool = False,
    db: Session = Depends(get_db),
    user: CurrentUser = Depends(workspace_reader),
) -> dict:
    if settings.app_env != "dev":
        raise HTTPException(503, "This workflow action is unavailable.")
    if user.role not in {"case-builder", "intake", "clinician", "admin"}:
        raise HTTPException(403, "This role cannot prepare triage dossiers.")
    from copy import deepcopy
    from app.services.intake_store import timestamp
    from app.services.triage_store import (
        compute_current_esr_digest,
        esr_triage_lock,
        finalize_triage_job,
        merge_stage2_result_into_state,
        persist_stage2_normalized,
        register_or_reuse_triage_job,
    )

    with esr_triage_lock(esr_id):
        initial_state = get_state(db)
        esr = (initial_state.get("esrsById") or {}).get(esr_id)
        if not esr or esr.get("intakeStatus") not in {"ready-for-triage", "completed"}:
            raise HTTPException(404, "Validated ESR not found in Medical Triage.")

        expected_digest = compute_current_esr_digest(initial_state, esr_id)
        existing_proc = esr.get("triageProcessing") or {}
        existing_dossier = (initial_state.get("triageDossiersByEsrId") or {}).get(esr_id)
        if (
            not force
            and existing_proc.get("status") == "completed"
            and existing_dossier
            and existing_dossier.get("sourceDigest") == expected_digest
            and (settings.intake_provider != "azure-openai" or existing_dossier.get("aiAttempted"))
        ):
            return {"ok": True, "esrId": esr_id, "state": _with_all_views(initial_state)}

        triage_job, _ = register_or_reuse_triage_job(
            db, esr_id, expected_digest, worker_lease=f"http:{user.user_id}"
        )
        triage_job_id = triage_job.job_id
        db.commit()

        # Run slow NLP + LLM pipeline ONCE outside snapshot commit retry loop
        computed_state = deepcopy(initial_state)
        try:
            dossier = run_triage_pipeline_for_state(
                computed_state,
                esr_id,
                allow_llm=(settings.intake_provider == "azure-openai"),
                force=force,
            )
        except Exception as exc:
            finalize_triage_job(db, triage_job_id, "failed", last_error=str(exc))
            db.commit()
            raise

        # Atomic merge onto latest snapshot state (checks evidence version; retries only the save, never the LLM)
        for _ in range(5):
            db.expire_all()
            latest_state = get_state(db)
            latest_esr = (latest_state.get("esrsById") or {}).get(esr_id)
            if not latest_esr:
                finalize_triage_job(db, triage_job_id, "completed")
                db.commit()
                raise HTTPException(404, "Validated ESR not found in Medical Triage.")

            now = timestamp()
            current_digest = compute_current_esr_digest(latest_state, esr_id)
            evidence_changed = bool(expected_digest and current_digest and current_digest != expected_digest)
            revision = latest_state["revision"]
            merge_stage2_result_into_state(
                latest_state,
                esr_id,
                computed_state,
                dossier,
                now,
                mark_stale=evidence_changed,
            )
            if dossier:
                esr_facts = [
                    row for row in (computed_state.get("clinicalFactsById") or {}).values()
                    if row.get("esrId") == esr_id
                ]
                persist_stage2_normalized(
                    db,
                    esr_id,
                    dossier,
                    esr_facts,
                    document_count=len(latest_esr.get("evidenceDocumentIds") or []),
                    is_stale=evidence_changed,
                )
            finalize_triage_job(db, triage_job_id, "stale" if evidence_changed else "completed")
            if evidence_changed:
                enqueue_triage_job(db, esr_id, force=True, evidence_digest=current_digest)
            try:
                save_next_state(db, revision, latest_state)
                db.commit()
                return {"ok": True, "esrId": esr_id, "state": _with_all_views(latest_state)}
            except HTTPException as exc:
                if exc.status_code != 409:
                    raise
                db.rollback()
    raise HTTPException(409, "The workspace changed while preparing triage. Please retry.")


class ClinicalValidateRequest(BaseModel):
    values: dict[str, JsonValue] | None = None


@router.post("/clinical/{dbq_id}/upload-dbq")
async def upload_clinical_dbq(
    dbq_id: str,
    file: UploadFile = File(...),
    db: Session = Depends(get_db),
    user: CurrentUser = Depends(workspace_reader),
) -> dict:
    if settings.app_env != "dev":
        raise HTTPException(503, "This workflow action is unavailable.")
    if user.role not in {"clinician", "admin"}:
        raise HTTPException(403, "Only clinicians can upload completed DBQ reports.")
    content = await file.read()
    if not content:
        raise HTTPException(422, "Upload a non-empty DBQ PDF or UTF-8 text file.")

    for _ in range(3):
        state = get_state(db)
        dbq = (state.get("dbqsById") or {}).get(dbq_id)
        if not dbq:
            raise HTTPException(404, "DBQ record not found.")
        revision = state["revision"]
        graph_out = run_clinical_pipeline(
            state,
            dbq_id,
            mode="uploaded-dbq",
            uploaded_dbq_file={"filename": file.filename or "uploaded-dbq.pdf", "content": content},
        )
        extracted = graph_out.get("extracted_values") or {}
        if any(str(v or "").strip() for v in extracted.values()):
            outcome = run_engine({"action": "saveDBQDraft", "args": [dbq_id, extracted], "state": state, "actorId": user.user_id})
            if "error" in outcome:
                raise HTTPException(422, outcome["error"])
            state = outcome["state"]
        dbq_next = state["dbqsById"][dbq_id]
        dbq_next["uploadedDbqDocument"] = {
            "filename": file.filename or "uploaded-dbq.pdf",
            "uploadedAt": timestamp(),
            "uploadedBy": user.user_id,
            "pageCount": len(graph_out.get("parsed_dbq_pages") or {}),
            "detectedClaimantName": graph_out.get("detected_claimant_name", ""),
        }
        dbq_next["validationAlerts"] = graph_out.get("validation_alerts") or []
        dbq_next["draftMedicalReview"] = graph_out.get("draft_medical_review")
        try:
            save_next_state(db, revision, state)
            db.commit()
            return {
                "ok": True,
                "dbqId": dbq_id,
                "extractedValues": extracted,
                "validationAlerts": dbq_next["validationAlerts"],
                "draftMedicalReview": dbq_next["draftMedicalReview"],
                "state": _with_all_views(state),
            }
        except HTTPException as exc:
            if exc.status_code != 409:
                raise
            db.rollback()
    raise HTTPException(409, "The workspace changed while uploading the DBQ. Please retry.")


@router.post("/clinical/{order_id}/upload-diagnostic")
async def upload_clinical_diagnostic(
    order_id: str,
    file: UploadFile = File(...),
    db: Session = Depends(get_db),
    user: CurrentUser = Depends(workspace_reader),
) -> dict:
    if settings.app_env != "dev":
        raise HTTPException(503, "This workflow action is unavailable.")
    if user.role not in {"clinician", "admin"}:
        raise HTTPException(403, "Only clinicians can upload diagnostic reports.")
    content = await file.read()
    if not content:
        raise HTTPException(422, "Upload a non-empty diagnostic report.")

    from app.services.clinical_pipeline import _parse_diagnostic_uploads
    parsed_list = _parse_diagnostic_uploads([{"orderId": order_id, "filename": file.filename or "diagnostic.pdf", "content": content}])
    parsed = parsed_list[0]
    if not parsed.get("readable"):
        raise HTTPException(422, parsed.get("error") or "The uploaded diagnostic file could not be read.")

    for _ in range(3):
        state = get_state(db)
        order = (state.get("diagnosticOrdersById") or {}).get(order_id)
        if not order:
            raise HTTPException(404, "Diagnostic order not found.")
        revision = state["revision"]
        args = [order_id, parsed["summary"], f"{parsed['filename']} ({parsed['pageCount']}p)"]
        if parsed.get("reportedFlag"):
            args.append(parsed["reportedFlag"])
        outcome = run_engine({"action": "recordDiagnosticResult", "args": args, "state": state, "actorId": user.user_id})
        if "error" in outcome:
            raise HTTPException(422, outcome["error"])
        state = outcome["state"]
        exam = (state.get("examinationsById") or {}).get(order["examinationId"]) or {}
        for linked_dbq_id in exam.get("requiredDbqIds", []):
            graph_out = run_clinical_pipeline(state, linked_dbq_id, mode="digital-form")
            if linked_dbq_id in state.get("dbqsById", {}):
                state["dbqsById"][linked_dbq_id]["validationAlerts"] = graph_out.get("validation_alerts") or []
                state["dbqsById"][linked_dbq_id]["draftMedicalReview"] = graph_out.get("draft_medical_review")
        if parsed.get("classificationUncertain") and order.get("resultId") in (state.get("diagnosticResultsById") or {}):
            state["diagnosticResultsById"][order["resultId"]]["classificationUncertain"] = True
        if exam.get("requiredDbqIds"):
            from app.services.qa_rules import invalidate_stale_qa_reviews
            invalidate_stale_qa_reviews(
                state,
                order["esrId"],
                timestamp(),
                f"Diagnostic report uploaded for order '{order.get('testName', order_id)}'.",
                affected_dbq_ids=set(exam.get("requiredDbqIds") or []),
            )
        try:
            save_next_state(db, revision, state)
            db.commit()
            return {"ok": True, "orderId": order_id, "parsed": parsed, "state": _with_all_views(state)}
        except HTTPException as exc:
            if exc.status_code != 409:
                raise
            db.rollback()
    raise HTTPException(409, "The workspace changed while uploading the diagnostic report. Please retry.")


@router.post("/clinical/{dbq_id}/validate-and-draft")
def validate_and_draft_clinical(
    dbq_id: str,
    body: ClinicalValidateRequest,
    db: Session = Depends(get_db),
    user: CurrentUser = Depends(workspace_reader),
) -> dict:
    if settings.app_env != "dev":
        raise HTTPException(503, "This workflow action is unavailable.")
    if user.role not in {"clinician", "admin"}:
        raise HTTPException(403, "Only clinicians can validate and draft medical reviews.")

    for _ in range(3):
        state = get_state(db)
        dbq = (state.get("dbqsById") or {}).get(dbq_id)
        if not dbq:
            raise HTTPException(404, "DBQ record not found.")
        revision = state["revision"]
        if body.values is not None:
            outcome = run_engine({"action": "saveDBQDraft", "args": [dbq_id, body.values], "state": state, "actorId": user.user_id})
            if "error" in outcome:
                raise HTTPException(422, outcome["error"])
            state = outcome["state"]
        graph_out = run_clinical_pipeline(state, dbq_id, mode="digital-form", submitted_values=body.values)
        state["dbqsById"][dbq_id]["validationAlerts"] = graph_out.get("validation_alerts") or []
        state["dbqsById"][dbq_id]["draftMedicalReview"] = graph_out.get("draft_medical_review")
        try:
            save_next_state(db, revision, state)
            db.commit()
            return {
                "ok": True,
                "dbqId": dbq_id,
                "validationAlerts": state["dbqsById"][dbq_id]["validationAlerts"],
                "draftMedicalReview": state["dbqsById"][dbq_id]["draftMedicalReview"],
                "state": _with_all_views(state),
            }
        except HTTPException as exc:
            if exc.status_code != 409:
                raise
            db.rollback()
    raise HTTPException(409, "The workspace changed while validating. Please retry.")


@router.post("/qa/reviews/{review_id}/audit")
def run_stage5_qa_audit(
    review_id: str,
    force: bool = False,
    db: Session = Depends(get_db),
    user: CurrentUser = Depends(workspace_reader),
) -> dict:
    if settings.app_env != "dev":
        raise HTTPException(503, "This workflow action is unavailable.")
    if user.role not in {"qa", "admin"}:
        raise HTTPException(403, "Only QA reviewers can trigger Stage 5 audits.")
    from app.services.qa_pipeline import run_qa_audit_for_state
    from app.services.qa_rules import check_independent_qa_reviewer

    for _ in range(3):
        state = get_state(db)
        review = (state.get("qaReviewsById") or {}).get(review_id)
        if not review:
            raise HTTPException(404, "QA review record not found.")
        check_independent_qa_reviewer(state, review, user.user_id, user.role)
        revision = state["revision"]
        audit_out = run_qa_audit_for_state(
            db,
            state,
            review_id,
            allow_llm=True,
            force=force,
        )
        try:
            save_next_state(db, revision, state)
            db.commit()
            return {
                "ok": True,
                "reviewId": review_id,
                **audit_out,
                "state": _with_all_views(state),
            }
        except HTTPException as exc:
            if exc.status_code != 409:
                raise
            db.rollback()
    raise HTTPException(409, "The workspace changed during QA audit. Please retry.")
