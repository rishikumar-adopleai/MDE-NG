import base64
import hashlib
import logging
import time
from datetime import UTC, datetime, timedelta
from uuid import uuid4

from fastapi import HTTPException
from sqlalchemy import and_, or_, select, update
from sqlalchemy.orm import Session

from app.core.config import settings
from app.core.database import SessionLocal
from app.models import OutboxJob
from app.services.intake_examples import example_claimant
from app.services.intake_processing import DocumentProcessingError, process_document
from app.services.intake_view import READABLE_EVIDENCE_BLOCKER, has_intake_evidence, with_intake_views
from app.services.workspace_store import get_state, save_next_state

TASK_NAME = "intake.process_document"
logger = logging.getLogger(__name__)


def timestamp() -> str:
    return datetime.now(UTC).isoformat().replace("+00:00", "Z")


def record(record_id: str, now: str) -> dict:
    return {"id": record_id, "version": 1, "createdAt": now, "updatedAt": now}


def touch(row: dict, now: str, **changes) -> None:
    row.update(changes, version=row["version"] + 1, updatedAt=now)


def activity(state: dict, esr_id: str, object_id: str, action: str, actor: str, now: str) -> None:
    key = f"activity-{uuid4()}"
    state["activitiesById"][key] = {
        "id": key, "esrId": esr_id, "entityId": object_id, "at": now,
        "actorId": actor, "action": action,
        "entityType": "esr" if esr_id == object_id else "evidence-document",
    }


def refresh_intake_work(state: dict, esr_id: str, now: str) -> None:
    esr = state["esrsById"][esr_id]
    previous = state["workItemsById"]
    state["workItemsById"] = {key: item for key, item in previous.items()
                             if item["esrId"] != esr_id or item["stage"] not in {"Intake", "Medical Triage"}}

    def add_work(key: str, entity_id: str, entity_type: str, stage: str, title: str,
                 status: str, blockers: list[str]) -> None:
        row = {**record(key, now), "esrId": esr_id, "entityId": entity_id,
               "entityType": entity_type, "stage": stage, "title": title, "status": status,
               "priority": esr["priority"], "blockingReasons": blockers,
               "assignedTo": esr.get("assignedTeamId")}
        if key in previous:
            row.update(createdAt=previous[key]["createdAt"], version=previous[key]["version"] + 1)
        state["workItemsById"][key] = row

    if esr.get("workflowClosedAt"):
        return
    if esr["intakeStatus"] in {"ready-for-triage", "completed"}:
        for key in esr["contentionIds"]:
            item = state["contentionsById"][key]
            if item.get("simulatedDisposition") == "cancelled":
                continue
            status = {"awaiting-review": "queued", "in-review": "in-progress",
                      "clarification-required": "blocked"}.get(item["status"])
            if status:
                blockers = [item["clarificationReason"]] if item.get("clarificationReason") else []
                add_work(f"work-medical-triage-{key}", key, "contention", "Medical Triage",
                         item["title"], status, blockers)
        return

    blockers = [issue["title"] for issue in state["intakeExceptionsById"].values()
                if issue["esrId"] == esr_id and issue["status"] == "open"]
    if esr.get("receiptStatus") != "received" or not esr.get("aepRecordedAt"):
        blockers.append("Record the ESR receipt and AEP acknowledgement")
    documents = [state["evidenceDocumentsById"][key] for key in esr["evidenceDocumentIds"]
                 if key in state["evidenceDocumentsById"]]
    pending_upload = any(item["classificationStatus"] in {"queued", "processing"} for item in documents)
    if not pending_upload and not has_intake_evidence(state, esr, documents):
        blockers.append(READABLE_EVIDENCE_BLOCKER)
    add_work(f"work-intake-{esr_id}", esr_id, "esr", "Intake",
             "Review intake handoff" if blockers else "Process source documents" if pending_upload else "Validate received ESR",
             "blocked" if blockers else "in-progress" if pending_upload else "queued", blockers)


def save_state(db: Session, state: dict, esr_id: str) -> dict:
    now = timestamp()
    revision = state["revision"]
    refresh_intake_work(state, esr_id, now)
    state.update(revision=revision + 1, updatedAt=now)
    save_next_state(db, revision, state)
    return state


def add_case(state: dict, name: str | None, titles: list[str], actor: str, now: str) -> dict:
    esr_id = f"esr-local-{uuid4().hex[:12]}"
    example = name is None
    if name is None:
        name, initials = example_claimant(esr_id)
    else:
        initials = "".join(word[0].upper() for word in name.split()[:2])
    veteran_id = esr_id.replace("esr-", "vet-", 1)
    reference = f"LOCAL-AEP-{esr_id.removeprefix('esr-local-').upper()}"
    facility = next(iter(state["facilitiesById"].values()), {})
    state["veteransById"][veteran_id] = {
        **record(veteran_id, now), "displayName": name,
        "initials": initials,
        "claimIdentifier": f"SYN-{esr_id[-12:].upper()}",
        "serviceGroup": "Army", "homeFacilityId": facility.get("id", ""),
        "city": facility.get("city", "Synthetic"), "stateCode": facility.get("stateCode", ""),
        "accessibilityNeeds": [],
    }
    contention_ids = []
    for position, title in enumerate(titles, 1):
        item_id = f"{esr_id}-contention-{position}"
        contention_ids.append(item_id)
        state["contentionsById"][item_id] = {
            **record(item_id, now), "esrId": esr_id, "title": title,
            "status": "awaiting-review", "evidenceDocumentIds": [],
            "examinationRequirementIds": [], "simulatedDisposition": "pending",
        }
    esr = {
        **record(esr_id, now), "veteranId": veteran_id, "receivedAt": now,
        "dueAt": (datetime.now(UTC) + timedelta(days=21)).isoformat(),
        "contentionIds": contention_ids, "evidenceDocumentIds": [], "priority": "routine",
        "sourceReference": esr_id.upper(), "externalState": "not-enabled",
        "receiptStatus": "received", "receiptReference": reference, "aepRecordedAt": now,
        "intakeStatus": "processing",
        "documentManifest": {
            "manifestId": f"MNF-{esr_id[-8:].upper()}",
            "source": "Prime EMS / VBMS eFolder Manifest",
            "receivedAt": now,
            "expectedDocuments": [
                {
                    "id": "exp-doc-1",
                    "title": "Service Treatment Records (Active Duty)",
                    "expectedCategory": "service-treatment",
                    "expectedPages": 14,
                    "required": True,
                },
                {
                    "id": "exp-doc-2",
                    "title": "VA Medical Center Treatment Notes",
                    "expectedCategory": "va-medical-records",
                    "expectedPages": 8,
                    "required": True,
                },
            ],
        },
    }
    if example:
        esr["intakeOrigin"] = "example"
    state["esrsById"][esr_id] = esr
    activity(state, esr_id, esr_id, "esr-received", actor, now)
    activity(state, esr_id, esr_id, "intake-aep-recorded", actor, now)
    return esr


def queue_document(db: Session, state: dict, esr: dict, filename: str, content: bytes, now: str) -> str:
    digest = hashlib.sha256(content).hexdigest()
    documents = state["evidenceDocumentsById"]
    if any(documents.get(item, {}).get("sourceHash") == digest for item in esr["evidenceDocumentIds"]):
        raise HTTPException(409, "This file is already linked to this ESR.")

    doc_id = f"doc-{uuid4()}"
    documents[doc_id] = {
        **record(doc_id, now), "esrId": esr["id"], "title": filename, "category": "other",
        "sourceReference": filename, "sourceHash": digest, "pageCount": 0,
        "classifiedPageCount": 0, "unreadablePageCount": 0, "classificationStatus": "queued",
        "contentionIds": list(esr["contentionIds"]),
        "processing": {"provider": settings.intake_provider, "status": "queued"},
    }
    touch(esr, now, evidenceDocumentIds=[*esr["evidenceDocumentIds"], doc_id], intakeStatus="processing")
    for contention_id in esr["contentionIds"]:
        contention = state["contentionsById"][contention_id]
        touch(contention, now, evidenceDocumentIds=[*contention["evidenceDocumentIds"], doc_id])
    veteran = state["veteransById"].get(esr.get("veteranId"), {})
    claimant_name = veteran.get("displayName")
    db.add(OutboxJob(id=doc_id.removeprefix("doc-"), task_name=TASK_NAME, status="queued", payload={
        "documentId": doc_id, "filename": filename, "content": base64.b64encode(content).decode("ascii"),
        "claimantName": claimant_name,
    }))
    return doc_id


def claim_job() -> tuple[str, str, dict] | None:
    now = int(time.time())
    with SessionLocal() as db:
        candidate = db.execute(
            select(OutboxJob.id, OutboxJob.status).where(
                OutboxJob.task_name == TASK_NAME,
                or_(OutboxJob.status == "queued",
                    and_(OutboxJob.status.like("run:%"), OutboxJob.status <= f"run:{now:010d}")),
            ).order_by(OutboxJob.created_at).limit(1)
        ).first()
        if not candidate:
            return None
        job_id, status = candidate
        lease = f"run:{now + 180:010d}"
        result = db.execute(update(OutboxJob).where(OutboxJob.id == job_id, OutboxJob.status == status)
                            .values(status=lease))
        if result.rowcount != 1:
            return None
        payload = db.get(OutboxJob, job_id).payload
        db.commit()
        return job_id, lease, payload


def finish_job(job_id: str, lease: str, document_id: str, result: dict) -> None:
    for _ in range(4):
        with SessionLocal() as db:
            job = db.get(OutboxJob, job_id)
            if not job or job.status != lease:
                return
            state = get_state(db)
            doc = state["evidenceDocumentsById"].get(document_id)
            if doc is None:
                job.status = "completed"
                db.commit()
                return
            esr = state["esrsById"][doc["esrId"]]
            now = timestamp()
            mismatch = result.get("quality", {}).get("claimantMatch") == "mismatch"
            needs_review = bool(result.get("error") or result.get("unreadablePages") or mismatch)
            facts = {
                "provider": result.get("provider", settings.intake_provider), "status": "needs-review" if needs_review else "indexed",
                "finishedAt": now,
            }
            for key in ("summary", "error", "retryable", "aiUnavailable"):
                if key in result:
                    facts[key] = result[key]
            pages = result.get("excerpts", [])
            facts["sources"] = pages
            excerpts = [
                {"id": f"{document_id}-{key}-{page['page']}", "pageNumber": page["page"],
                 "contentionId": key, "text": page["text"]}
                for page in pages for key in doc["contentionIds"]
            ]
            touch(doc, now, processing=facts, category=result.get("category", "other"),
                  pageCount=result.get("pageCount", 0), classifiedPageCount=result.get("readablePages", 0),
                  unreadablePageCount=result.get("unreadablePages", 0), syntheticExcerpts=excerpts,
                  sections=result.get("sections", []), quality=result.get("quality", {}),
                  sourceHash=result.get("sourceHash", doc.get("sourceHash", "")),
                  classificationStatus="needs-human-review" if needs_review else "indexed")
            issue_id = f"issue-{document_id}-processing"
            issues = state["intakeExceptionsById"]
            if needs_review:
                issues[issue_id] = {
                    **record(issue_id, now), "esrId": esr["id"], "documentId": document_id,
                    "title": "Document requires staff review", "status": "open", "sourceReference": doc["sourceReference"],
                    "kind": "metadata-incomplete" if mismatch else "unreadable-pages" if doc["unreadablePageCount"] else "metadata-incomplete",
                    "detail": "Claimant identity mismatch flagged in document text." if mismatch else (result.get("error") or f"{doc['unreadablePageCount']} page(s) have no readable text layer."),
                }
            elif issue_id in issues:
                touch(issues[issue_id], now, status="resolved", resolution="Document successfully reprocessed.")
            pending = any(state["evidenceDocumentsById"][item]["classificationStatus"] in {"queued", "processing"}
                          for item in esr["evidenceDocumentIds"])
            touch(esr, now, intakeStatus="processing" if pending else "requires-attention")
            activity(state, esr["id"], document_id, "intake-document-reviewed", "intake-worker", now)
            try:
                save_state(db, state, esr["id"])
            except HTTPException as exc:
                if exc.status_code != 409:
                    raise
                continue
            changed = db.execute(update(OutboxJob).where(OutboxJob.id == job_id, OutboxJob.status == lease)
                                 .values(status="failed" if result.get("retryable") else "completed"))
            if changed.rowcount == 1:
                db.commit()
            return
    logger.warning("Intake processing result will retry after the job lease expires.")


def process_next_job() -> bool:
    claimed = claim_job()
    if not claimed:
        return False
    job_id, lease, payload = claimed
    try:
        result = process_document(
            payload["filename"],
            base64.b64decode(payload["content"]),
            expected_claimant=payload.get("claimantName"),
        )
    except DocumentProcessingError as exc:
        result = {"error": str(exc)}
    finish_job(job_id, lease, payload["documentId"], result)
    return True


INTAKE_COMMANDS = frozenset({
    "recordIntakeReceipt", "reviewIntakeException", "completeDocumentProcessing", "validateIntake", "updateDocumentCategory",
})


def apply_intake_command(state: dict, action: str, args: list, actor: str) -> dict:
    if not args or not isinstance(args[0], str):
        raise HTTPException(422, "Select an Intake record.")
    now = timestamp()
    object_id = args[0]
    issue = state["intakeExceptionsById"].get(object_id) if action == "reviewIntakeException" else None
    esr_id = issue["esrId"] if issue else object_id
    esr = state["esrsById"].get(esr_id)
    view = with_intake_views(state)["intake"]["viewsByEsrId"].get(esr_id)
    if not esr or not view:
        raise HTTPException(422, "This record is no longer active in Intake.")

    if action == "recordIntakeReceipt":
        if view["receiptReady"]:
            raise HTTPException(422, "The receipt and acknowledgement are already recorded.")
        if len(args) > 2 or (len(args) == 2 and args[1] is not None and not isinstance(args[1], str)):
            raise HTTPException(422, "Enter a valid receipt reference.")
        reference = (args[1] or "").strip() if len(args) > 1 else ""
        touch(esr, now, intakeStatus="processing", aepRecordedAt=now, receiptStatus="received",
              receiptReference=reference[:150] or f"LOCAL-ESR-{esr_id}")
        event = "intake-aep-recorded"
    elif action == "reviewIntakeException":
        if not issue or issue["status"] not in {"open", "escalated"}:
            raise HTTPException(422, "This exception has already been dispositioned.")
        if len(args) != 3 or args[1] not in ("resolved", "escalated") or not isinstance(args[2], str) or not args[2].strip():
            raise HTTPException(422, "Choose a disposition and enter a review note.")
        touch(issue, now, status=args[1], resolution=args[2].strip()[:2000])
        object_id = issue.get("documentId") or esr_id
        event = f"intake-exception-{args[1]}"
    elif action == "completeDocumentProcessing":
        if not view["canProcessLegacy"]:
            raise HTTPException(422, "Uploaded documents are processed by the Intake worker.")
        for key in view["documentIds"]:
            doc = state["evidenceDocumentsById"][key]
            if not doc.get("processing") and doc["classificationStatus"] in {"queued", "processing"}:
                touch(doc, now, classificationStatus="indexed",
                      classifiedPageCount=doc["pageCount"] - doc["unreadablePageCount"])
        if not any(state["evidenceDocumentsById"][key]["classificationStatus"] in {"queued", "processing"}
                   for key in view["documentIds"]):
            touch(esr, now, intakeStatus="requires-attention")
        event = "intake-document-reviewed"
    elif action == "updateDocumentCategory":
        doc_id = args[0]
        doc = state["evidenceDocumentsById"].get(doc_id)
        if not doc:
            raise HTTPException(404, "Document not found.")
        if len(args) < 2 or not isinstance(args[1], str) or not args[1].strip():
            raise HTTPException(422, "Select a valid document category.")
        new_category = args[1].strip()
        valid_cats = {
            "service-treatment", "va-medical-records", "private-medical-records",
            "hospital", "laboratory", "imaging", "prior-examination", "other",
        }
        if new_category not in valid_cats:
            raise HTTPException(422, f"Invalid category {new_category}.")
        touch(doc, now, category=new_category, userReclassified=True, classificationStatus="indexed")
        issue_id = f"issue-{doc_id}-processing"
        if issue_id in state["intakeExceptionsById"]:
            touch(state["intakeExceptionsById"][issue_id], now, status="resolved", resolution=f"Category manually confirmed as {new_category} by staff.")
        object_id = doc_id
        event = "intake-document-reviewed"
    else:
        if not view["canValidate"]:
            raise HTTPException(422, view["blocker"])
        touch(esr, now, intakeStatus="ready-for-triage")
        event = "intake-validated"

    activity(state, esr_id, object_id, event, actor, now)
    refresh_intake_work(state, esr_id, now)
    state.update(revision=state["revision"] + 1, updatedAt=now)
    return {"state": state, "value": {}}
