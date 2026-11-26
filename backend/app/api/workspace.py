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
        esr_id = body.args[0]
        enqueue_triage_job(db, esr_id, force=True)
    save_next_state(db, body.revision, next_state)
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
    from app.services.intake_store import timestamp, touch

    for _ in range(3):
        state = get_state(db)
        esr = state.get("esrsById", {}).get(esr_id)
        if not esr or esr.get("intakeStatus") not in {"ready-for-triage", "completed"}:
            raise HTTPException(404, "Validated ESR not found in Medical Triage.")
        revision = state["revision"]
        run_triage_pipeline_for_state(state, esr_id, allow_llm=(settings.intake_provider == "azure-openai"), force=force)
        dossier = (state.get("triageDossiersByEsrId") or {}).get(esr_id) or {}
        now = timestamp()
        touch(esr, now, triageProcessing={
            "status": "completed",
            "completedAt": now,
            "sourceDigest": dossier.get("sourceDigest", ""),
            "factCount": dossier.get("factCount", 0),
            "aiProvider": dossier.get("aiProvider", "local"),
            "ruleVersion": dossier.get("ruleVersion", ""),
        })
        try:
            save_next_state(db, revision, state)
            db.commit()
            return {"ok": True, "esrId": esr_id, "state": _with_all_views(state)}
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
