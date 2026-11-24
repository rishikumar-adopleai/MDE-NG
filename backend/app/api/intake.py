from pathlib import PurePath
from typing import Annotated, Literal
from uuid import uuid4

from fastapi import APIRouter, Depends, File, Form, HTTPException, UploadFile
from pydantic import BaseModel
from sqlalchemy.orm import Session

from app.core.config import settings
from app.core.database import get_db
from app.core.security import CurrentUser, get_current_user
from app.models import OutboxJob
from app.services.intake_processing import MAX_FILE_BYTES
from app.services.intake_view import with_intake_views
from app.services.intake_store import add_case, queue_document, record, save_state, timestamp, touch
from app.services.stage_ai import generate_stage_review, select_work, stage_sources
from app.services.triage import with_triage_view
from app.services.va_samples import MAX_XML_BYTES, import_exam_request, parse_exam_request, read_bundled_request, scenario_files
from app.services.workspace_store import get_state, save_next_state

router = APIRouter(prefix="/intake", tags=["Intake"])
IntakeFile = Annotated[UploadFile, File()]


class DocumentReviewDecision(BaseModel):
    reviewId: str
    decision: Literal["accepted-for-human-review", "rejected"]


def intake_actor(user: CurrentUser = Depends(get_current_user)) -> CurrentUser:
    if settings.app_env != "dev":
        raise HTTPException(404, "Intake actions are unavailable in this workspace.")
    if user.role not in {"intake", "admin"}:
        raise HTTPException(403, "This role cannot manage Intake records.")
    return user


def _save_intake_snapshot(db: Session, state: dict, now: str) -> None:
    revision = state["revision"]
    state.update(revision=revision + 1, updatedAt=now)
    save_next_state(db, revision, state)
    db.commit()


@router.post("/esrs/{esr_id}/document-review")
def review_documents(esr_id: str, _user: CurrentUser = Depends(intake_actor),
                     db: Session = Depends(get_db)) -> dict:
    if settings.intake_provider != "azure-openai":
        raise HTTPException(503, "AI review is not enabled for this workspace.")
    state = get_state(db)
    sources = stage_sources(state, select_work(state, "Intake", esr_id))
    db.close()
    review = generate_stage_review(state, "Intake", esr_id, document_checklist=True)

    state = get_state(db)
    if sources != stage_sources(state, select_work(state, "Intake", esr_id)):
        raise HTTPException(409, "The Intake records changed during this review. Run the review again.")
    now = timestamp()
    key = f"document-review-{esr_id}"
    item = {**record(key, now), "esrId": esr_id, "entityId": esr_id, "stage": "Intake",
            "title": "Document review assistant", "explanation": review["overview"]["text"],
            "rules": [], "evidenceDocumentIds": [], "provenance": "ai-review",
            "decision": "pending", "documentReview": {**review, "reviewId": str(uuid4()), "format": "checklist"}}
    state["aiInsightsById"][key] = item
    _save_intake_snapshot(db, state, now)
    return {"state": with_triage_view(with_intake_views(state))}


@router.put("/esrs/{esr_id}/document-review/decision")
def decide_document_review(esr_id: str, body: DocumentReviewDecision,
                           user: CurrentUser = Depends(intake_actor), db: Session = Depends(get_db)) -> dict:
    state = get_state(db)
    select_work(state, "Intake", esr_id)
    item = state["aiInsightsById"].get(f"document-review-{esr_id}")
    if not item or item.get("documentReview", {}).get("reviewId") != body.reviewId:
        raise HTTPException(409, "This review has been replaced. Open the current review to record a decision.")
    if item["decision"] != body.decision:
        now = timestamp()
        touch(item, now, decision=body.decision, decidedBy=user.user_id, decidedAt=now)
        key = f"activity-{uuid4()}"
        summary = "AI document review accepted for staff review." if body.decision == "accepted-for-human-review" else "AI document review rejected."
        state["activitiesById"][key] = {
            "id": key, "esrId": esr_id, "entityId": esr_id, "at": now, "actorId": user.user_id,
            "action": "assistance-disposition-recorded", "entityType": "esr", "rationale": summary,
        }
        _save_intake_snapshot(db, state, now)
    return {"state": with_triage_view(with_intake_views(state))}


def uploaded_document(file: UploadFile, synthetic: bool) -> tuple[str, bytes]:
    if not synthetic:
        raise HTTPException(422, "Confirm that the document contains no real claimant records.")
    filename = PurePath((file.filename or "").replace("\\", "/")).name
    valid_exts = (".pdf", ".txt", ".xml", ".png", ".jpg", ".jpeg", ".tiff", ".bmp", ".tif")
    if len(filename) > 150 or not filename.lower().endswith(valid_exts):
        raise HTTPException(422, "Choose a supported document format (PDF, TXT, XML, or Image) under 150 characters.")
    content = file.file.read(MAX_FILE_BYTES + 1)
    if not content or len(content) > MAX_FILE_BYTES:
        raise HTTPException(413, "The source file must be nonempty and at most 5 MB.")
    if filename.lower().endswith(".pdf") and not content.startswith(b"%PDF-"):
        raise HTTPException(422, "The selected file does not have a valid PDF header.")
    return filename, content


def open_case(state: dict, esr_id: str) -> dict:
    esr = state["esrsById"].get(esr_id)
    if not esr:
        raise HTTPException(404, "ESR not found.")
    if esr.get("workflowClosedAt") or esr["intakeStatus"] in {"ready-for-triage", "completed"}:
        raise HTTPException(409, "This ESR has already left Intake.")
    if not esr.get("aepRecordedAt"):
        raise HTTPException(409, "Record the acknowledgement before uploading files.")
    return esr


@router.post("/esrs", status_code=202)
def create_esr(
    file: IntakeFile,
    name: Annotated[str, Form(min_length=1, max_length=100)],
    contentions: Annotated[str, Form(min_length=1, max_length=600)],
    synthetic: Annotated[bool, Form()],
    user: CurrentUser = Depends(intake_actor),
    db: Session = Depends(get_db),
) -> dict:
    filename, content = uploaded_document(file, synthetic)
    titles = list(dict.fromkeys(line.strip() for line in contentions.splitlines() if line.strip()))
    if not name.strip() or not 1 <= len(titles) <= 10 or any(len(title) > 120 for title in titles):
        raise HTTPException(422, "Enter a claimant name and one to ten contention titles.")
    return receive_case(db, user.user_id, name.strip(), titles, filename, content)


def receive_case(db: Session, actor: str, name: str | None, titles: list[str], filename: str, content: bytes) -> dict:
    state = get_state(db)
    now = timestamp()
    esr = add_case(state, name, titles, actor, now)
    doc_id = queue_document(db, state, esr, filename, content, now)
    next_state = save_state(db, state, esr["id"])
    db.commit()
    return {"esrId": esr["id"], "documentId": doc_id, "state": with_triage_view(with_intake_views(next_state))}


@router.get("/exam-management/examples")
def exam_management_examples(_user: CurrentUser = Depends(intake_actor)) -> dict:
    return {"items": [{"id": key, "title": title} for key, (title, _) in scenario_files().items()]}


def receive_exam_management(db: Session, user: CurrentUser, filename: str, content: bytes, *, bundled: bool = False) -> dict:
    parsed = parse_exam_request(content)
    state = get_state(db)
    existing = next((esr for esr in state["esrsById"].values()
                     if esr.get("examManagement", {}).get("esrId") == parsed.reference), None) if bundled else None
    esr, created = (existing, False) if existing else import_exam_request(db, state, parsed, content, filename, user.user_id)
    if created:
        _save_intake_snapshot(db, state, timestamp())
    return {"esrId": esr["id"], "created": created, "state": with_triage_view(with_intake_views(state))}


@router.post("/exam-management/examples/{scenario_id}", status_code=202)
def receive_exam_management_example(scenario_id: str, user: CurrentUser = Depends(intake_actor),
                                    db: Session = Depends(get_db)) -> dict:
    scenario = scenario_files().get(scenario_id)
    if not scenario:
        raise HTTPException(404, "This example request is unavailable.")
    _, path = scenario
    loaded = read_bundled_request(path)
    if not loaded:
        raise HTTPException(503, "This example request could not be read.")
    return receive_exam_management(db, user, path.name, loaded[1], bundled=True)


@router.post("/exam-management/esrs", status_code=202)
def receive_exam_management_upload(file: IntakeFile, synthetic: Annotated[bool, Form()],
                                   user: CurrentUser = Depends(intake_actor),
                                   db: Session = Depends(get_db)) -> dict:
    if not synthetic:
        raise HTTPException(422, "Confirm that the document contains no real claimant records.")
    filename = PurePath((file.filename or "").replace("\\", "/")).name
    if len(filename) > 150 or not filename.lower().endswith(".xml"):
        raise HTTPException(422, "Choose an Exam Management XML file.")
    content = file.file.read(MAX_XML_BYTES + 1)
    if len(content) > MAX_XML_BYTES:
        raise HTTPException(413, "The XML file may be up to 2 MB.")
    return receive_exam_management(db, user, filename, content)


@router.post("/esrs/{esr_id}/documents", status_code=202)
def attach_document(esr_id: str,
                    file: UploadFile = File(None),
                    files: list[UploadFile] = File(None),
                    synthetic: Annotated[bool, Form()] = True,
                    _user: CurrentUser = Depends(intake_actor), db: Session = Depends(get_db)) -> dict:
    all_files = [f for f in (files or []) if f and f.filename]
    if file and file.filename and file not in all_files:
        all_files.append(file)
    if not all_files:
        raise HTTPException(422, "Select at least one source file.")

    state = get_state(db)
    esr = open_case(state, esr_id)
    now = timestamp()
    doc_ids = []
    for f in all_files:
        filename, content = uploaded_document(f, synthetic)
        doc_id = queue_document(db, state, esr, filename, content, now)
        doc_ids.append(doc_id)
    next_state = save_state(db, state, esr["id"])
    db.commit()
    return {"esrId": esr_id, "documentId": doc_ids[0] if doc_ids else None, "documentIds": doc_ids, "state": with_triage_view(with_intake_views(next_state))}


@router.post("/esrs/{esr_id}/documents/{document_id}/retry", status_code=202)
def retry_document(esr_id: str, document_id: str, _user: CurrentUser = Depends(intake_actor),
                   db: Session = Depends(get_db)) -> dict:
    state = get_state(db)
    esr = open_case(state, esr_id)
    doc = state["evidenceDocumentsById"].get(document_id)
    if not doc or doc["esrId"] != esr_id:
        raise HTTPException(404, "Document not found.")
    job = db.get(OutboxJob, document_id.removeprefix("doc-"))
    if not job or job.status != "failed":
        raise HTTPException(409, "Only a document with a retryable processing error can be retried.")
    now = timestamp()
    touch(doc, now, classificationStatus="queued", processing={"provider": settings.intake_provider, "status": "queued"})
    touch(esr, now, intakeStatus="processing")
    job.status = "queued"
    next_state = save_state(db, state, esr["id"])
    db.commit()
    return {"esrId": esr_id, "documentId": document_id, "state": with_triage_view(with_intake_views(next_state))}


