import hashlib
import io
import re
import zipfile
from pathlib import PurePath
from typing import Annotated, Literal
from uuid import uuid4

import pymupdf
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
from app.services.scheduling import normalize_state_code
from app.services.stage_ai import generate_stage_review, select_work, stage_sources
from app.services.triage import with_triage_view
from app.agents.intake.parser import MAX_XML_BYTES, import_exam_request, parse_exam_request, read_bundled_request, scenario_files
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
    from app.services.workspace_store import get_snapshot
    snapshot = get_snapshot(db)
    revision = snapshot.revision if snapshot else state.get("revision", 0)
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


def unpack_upload_files(file: UploadFile | None, files: list[UploadFile] | None, synthetic: bool = True) -> list[tuple[str, bytes]]:
    seen_names = set()
    raw_uploads: list[UploadFile] = []

    # Include files from 'files' list first, deduplicating by filename
    for f in (files or []):
        if f and f.filename:
            fn = PurePath(f.filename.replace("\\", "/")).name
            if fn not in seen_names:
                seen_names.add(fn)
                raw_uploads.append(f)

    # Include primary 'file' only if not already present in raw_uploads
    if file and file.filename:
        fn = PurePath(file.filename.replace("\\", "/")).name
        if fn not in seen_names:
            seen_names.add(fn)
            raw_uploads.insert(0, file)

    extracted: list[tuple[str, bytes]] = []
    ALLOWED_EXTS = {".pdf", ".xml", ".txt", ".png", ".jpg", ".jpeg", ".tiff", ".bmp", ".tif"}

    for upload in raw_uploads:
        fname = PurePath((upload.filename or "").replace("\\", "/")).name
        content = upload.file.read()
        if not content:
            continue

        # Detect ZIP archives (direct .zip or PK header)
        if fname.lower().endswith(".zip") or (len(content) > 4 and content[:4] == b"PK\x03\x04"):
            try:
                with zipfile.ZipFile(io.BytesIO(content)) as zf:
                    for zip_info in zf.infolist():
                        if zip_info.is_dir():
                            continue
                        sub_name = PurePath(zip_info.filename.replace("\\", "/")).name
                        if sub_name.startswith((".", "_")) or "__MACOSX" in zip_info.filename:
                            continue
                        sub_ext = PurePath(sub_name).suffix.lower()
                        if sub_ext in ALLOWED_EXTS:
                            sub_content = zf.read(zip_info)
                            if sub_content:
                                extracted.append((sub_name, sub_content))
            except Exception as e:
                raise HTTPException(422, f"Could not read ZIP archive '{fname}': {e}")
        else:
            ext = PurePath(fname).suffix.lower()
            if ext in ALLOWED_EXTS:
                extracted.append((fname, content))

    return extracted


def uploaded_document(file: UploadFile, synthetic: bool) -> tuple[str, bytes]:
    if not synthetic:
        raise HTTPException(422, "Confirm that the document contains no real claimant records.")
    filename = PurePath((file.filename or "").replace("\\", "/")).name
    valid_exts = (".pdf", ".txt", ".xml", ".png", ".jpg", ".jpeg", ".tiff", ".bmp", ".tif", ".zip")
    if len(filename) > 150 or not filename.lower().endswith(valid_exts):
        raise HTTPException(422, "Choose a supported document format (PDF, TXT, XML, Image, or ZIP) under 150 characters.")
    content = file.file.read(MAX_FILE_BYTES * 5)
    if not content:
        raise HTTPException(422, "The source file must be nonempty.")
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


def _parse_optional_travel_miles(raw: object) -> int | None:
    if raw is None or isinstance(raw, bool):
        return None
    if isinstance(raw, int):
        return raw if 1 <= raw <= 5000 else None
    text = str(raw).strip()
    if not text:
        return None
    m = re.match(r"^(\d{1,4})(?:\s*(?:mi|miles?))?$", text, re.IGNORECASE)
    if not m:
        return None
    val = int(m.group(1))
    return val if 1 <= val <= 5000 else None


def _extract_location_and_travel_from_text(combined_text: str) -> tuple[str, str, str, int | None]:
    """Extract Veteran city, stateCode, postalCode, and travelLimitMiles when explicitly present; never fabricate defaults."""
    placeholders = {"city", "state", "zip", "zip code", "postal code", "field", "blank", "not", "n/a", "none", "unknown"}
    city = ""
    state_code = ""
    postal_code = ""

    m_full_addr = re.search(
        r"(?:Address|Location)\s*:\s*([A-Za-z][A-Za-z\s.'-]{1,60}),\s*([A-Z]{2})\s+(\d{5}(?:-\d{4})?)\b",
        combined_text,
    )
    if m_full_addr:
        c_val = m_full_addr.group(1).strip()
        if c_val.casefold() not in placeholders:
            city = c_val[:80]
            state_code = m_full_addr.group(2).upper()
            postal_code = m_full_addr.group(3)

    if not city:
        m_city = re.search(r"(?:Claimant[ \t]+|Veteran[ \t]+)?City[ \t]*:[ \t]*([A-Za-z][A-Za-z .'-]{1,60}?)(?:[ \t]*\||[ \t]*,|[ \t]*\r?\n|$)", combined_text, re.IGNORECASE)
        if m_city:
            c_val = m_city.group(1).strip()
            if c_val.casefold() not in placeholders and not any(c_val.lower().startswith(b) for b in ("state", "zip", "not", "blank")):
                city = c_val[:80]

    if not state_code:
        m_state = re.search(r"(?:Claimant[ \t]+|Veteran[ \t]+)?State(?:[ \t]*Code)?[ \t]*:[ \t]*([A-Za-z]{2})\b", combined_text, re.IGNORECASE)
        if m_state:
            s_val = m_state.group(1).strip().upper()
            if s_val.casefold() not in placeholders:
                state_code = s_val

    if not postal_code:
        m_zip = re.search(r"(?:ZIP(?:[ \t]*Code)?|Postal[ \t]*Code)[ \t]*:[ \t]*(\d{5}(?:-\d{4})?)\b", combined_text, re.IGNORECASE)
        if m_zip:
            postal_code = m_zip.group(1)

    travel_limit_miles = None
    m_travel = re.search(
        r"(?:Travel[ \t]*(?:limit|distance|radius)|Max[ \t]*travel(?:[ \t]*distance)?)[ \t]*[:\-][ \t]*(\d{1,4})[ \t]*(?:mi|miles?)?\b",
        combined_text,
        re.IGNORECASE,
    )
    if m_travel:
        travel_limit_miles = _parse_optional_travel_miles(m_travel.group(1))

    return city, state_code, postal_code, travel_limit_miles


def extract_metadata_from_extracted_files(extracted: list[tuple[str, bytes]]) -> dict:
    """Extract claimant name, ESR reference, location, travel limit, contentions, DBQs, branch, and instructions from uploaded files."""
    # Detect if payload contains synthetic/simulated markers
    is_simulated_payload = any(
        any(m in fn.lower() for m in ("synthetic", "mde-ng", "mock", "case_"))
        for fn, _ in extracted
    )

    # 1. First check for XML
    xml_items = [(fn, ct) for fn, ct in extracted if fn.lower().endswith(".xml")]
    if len(xml_items) > 1:
        raise HTTPException(422, "Choose one exam request XML per case. Attach additional XML documents separately.")
    xml_item = next(iter(xml_items), None)
    if xml_item:
        parsed = parse_exam_request(xml_item[1])
        xml_lower = xml_item[1].lower()
        is_sim_xml = (
            is_simulated_payload
            or "syn" in (parsed.reference or "").lower()
            or "test" in (parsed.reference or "").lower()
            or "example" in (parsed.claimant or "").lower()
            or b'synthetic="true"' in xml_lower
            or b'source="simulation"' in xml_lower
            or b'notvalidatedagainstvaiepd' in xml_lower
        )
        return {
            "claimantName": parsed.claimant or "",
            "esrReference": parsed.reference or "",
            "serviceBranch": parsed.service_group or "",
            "benefitProgram": parsed.program or "Compensation & Pension (C&P Live)",
            "city": parsed.city or "",
            "stateCode": parsed.state_code or "",
            "postalCode": parsed.postal_code or "",
            "travelLimitMiles": parsed.travel_limit_miles,
            "contentions": [{"title": c.title, "classification": c.classification or "Direct Service Connection", "forms": list(c.requested_forms)} for c in parsed.contentions],
            "specialInstructions": parsed.special_instructions or "",
            "fileType": f"Folder / Package ({len(extracted)} files including XML)",
            "examManagementRequest": True,
            "isSimulated": is_sim_xml,
        }

    # 2. Extract text from up to the first 6 PDFs or text files
    all_text_blocks = []
    total_pages = 0
    instructions_found = ""

    for fn, ct in extracted:
        lower_fn = fn.lower()
        if lower_fn.endswith(".pdf"):
            try:
                doc = pymupdf.open(stream=ct, filetype="pdf")
                total_pages += len(doc)
                if len(all_text_blocks) < 6:
                    pdf_text = "\n".join(doc[p].get_text() for p in range(min(3, len(doc))))
                    all_text_blocks.append((fn, pdf_text))
                    # Check specifically for instructions document
                    if "instruction" in lower_fn or "va special examination instructions" in pdf_text.lower():
                        instructions_found = pdf_text
            except Exception:
                total_pages += 1
        elif lower_fn.endswith(".txt"):
            try:
                txt = ct.decode("utf-8", errors="ignore")
                all_text_blocks.append((fn, txt))
                total_pages += 1
            except Exception:
                total_pages += 1
        else:
            total_pages += 1

    combined_text = "\n\n".join(f"[{fn}]\n{txt}" for fn, txt in all_text_blocks)

    if any(m in combined_text.lower() for m in ("synthetic", "mde-ng / synthetic", "not an official record", "fictional data")):
        is_simulated_payload = True

    # Claimant extraction
    claimant = ""
    m_claimant = re.search(r'(?:Claimant|Patient|Veteran)(?:\s*Name)?\s*:\s*([A-Za-z\s,.-]+?)(?:\s*\||\s*\n|ID:|SSN:|$)', combined_text, re.IGNORECASE)
    if m_claimant:
        c = m_claimant.group(1).strip()
        if len(c) > 1 and not any(c.lower().startswith(b) for b in ('field', 'not', 'blank', 'example record', 'unfilled')):
            claimant = c

    # ESR Reference extraction
    ref = ""
    m_ref = re.search(r'(?:Request reference|ESR(?:\s*ID|\s*Reference)?|ExaminationId)\s*[:\s]*([A-Za-z0-9\-_]+)', combined_text, re.IGNORECASE)
    if m_ref:
        r = m_ref.group(1).strip()
        if r.lower() not in ('intake', 'cover', 'page', 'not', 'blank'):
            ref = r

    # Contentions extraction
    contentions = []
    m_exam = re.search(r'(?:Requested examinations|Claimed disabilities)\s*[:\n]\s*([\s\S]+?)(?:\n\n|\n[A-Z][a-z]+ [a-z]+:|\nMedical opinion|\nScheduling|\nEvidence|\nReported|\nScope|\Z)', combined_text, re.IGNORECASE)
    if m_exam:
        raw = m_exam.group(1).strip()
        raw = re.sub(r'\s+', ' ', raw)
        parts = [p.strip() for p in re.split(r'[;\n]', raw) if p.strip()]
        for p in parts:
            if ':' in p:
                c_title, dbq = p.split(':', 1)
                contentions.append({"title": c_title.strip(), "classification": "Direct Service Connection", "forms": [dbq.strip()] if dbq.strip() else []})
            else:
                contentions.append({"title": p.strip(), "classification": "Direct Service Connection", "forms": []})

    if not contentions:
        for m in re.finditer(r'Contention\s*\d+\s*[-:]\s*([^\n]+)', combined_text, re.IGNORECASE):
            contentions.append({"title": m.group(1).strip().capitalize(), "classification": "Direct Service Connection", "forms": []})

    # Service branch extraction (dynamic from document, do not default to Armed Forces)
    branch = ""
    m_branch = re.search(r'(?:Branch|Service branch)\s*[:\s]*([A-Za-z\s]+)', combined_text, re.IGNORECASE)
    if m_branch:
        b = m_branch.group(1).strip()
        for branch_name in ("Navy", "Army", "Air Force", "Coast Guard", "Marine Corps", "Space Force", "Armed Forces"):
            if branch_name.lower() in b.lower():
                branch = branch_name
                break
    elif "coast guard" in combined_text.lower():
        branch = "Coast Guard"
    elif "marine corps" in combined_text.lower():
        branch = "Marine Corps"
    elif "air force" in combined_text.lower():
        branch = "Air Force"
    elif "navy" in combined_text.lower():
        branch = "Navy"
    elif "army" in combined_text.lower():
        branch = "Army"

    # Special instructions extraction - dynamic, preserve full text, no hardcoded fallbacks
    instructions = ""
    if instructions_found:
        m_inst = re.search(r'(Contention\s*01[\s\S]+?)(?=(?:Synthetic example|MDE-NG\s*/\s*SYNTHETIC|\Z))', instructions_found, re.IGNORECASE)
        if m_inst:
            instructions = m_inst.group(1).strip()
        else:
            m_inst2 = re.search(r'(?:VA special examination instructions|Special examination instructions)[\s\S]+?(?=(?:Synthetic example|MDE-NG\s*/\s*SYNTHETIC|\Z))', instructions_found, re.IGNORECASE)
            if m_inst2:
                instructions = m_inst2.group(0).strip()

    if not instructions:
        m_inst_gen = re.search(r'(?:Medical opinion request|Special Instructions?|VA Instructions?)\s*[:\n]\s*([\s\S]+?)(?:\n\n[A-Z][a-z]+ [a-z]+:|\nScheduling|\nEvidence|\nScope|\Z)', combined_text, re.IGNORECASE)
        if m_inst_gen:
            instructions = m_inst_gen.group(1).strip()

    city, state_code, postal_code, travel_limit_miles = _extract_location_and_travel_from_text(combined_text)

    return {
        "claimantName": claimant,
        "esrReference": ref,
        "serviceBranch": branch,
        "benefitProgram": "Compensation & Pension (C&P Live)",
        "city": city,
        "stateCode": state_code,
        "postalCode": postal_code,
        "travelLimitMiles": travel_limit_miles,
        "contentions": contentions,
        "specialInstructions": instructions,
        "fileType": f"Medical Folder ({len(extracted)} files)" if len(extracted) > 1 else extracted[0][0],
        "total_pages": total_pages,
        "isSimulated": is_simulated_payload,
    }


@router.post("/esrs/inspect")
def inspect_esr(
    file: UploadFile = File(None),
    files: list[UploadFile] = File(None),
    scenario_id: Annotated[str | None, Form()] = None,
    name: Annotated[str | None, Form()] = None,
    contentions: Annotated[str | None, Form()] = None,
    synthetic: Annotated[bool, Form()] = True,
    _user: CurrentUser = Depends(intake_actor),
    db: Session = Depends(get_db),
) -> dict:
    state = get_state(db)

    # 1. Bundled scenario inspection
    if scenario_id:
        scenario = scenario_files().get(scenario_id)
        if not scenario:
            raise HTTPException(404, "This example request is unavailable.")
        _, path = scenario
        loaded = read_bundled_request(path)
        if not loaded:
            raise HTTPException(503, "This example request could not be read.")
        parsed, content = loaded
        existing = next((esr for esr in state["esrsById"].values()
                         if esr.get("examManagement", {}).get("esrId") == parsed.reference), None)
        return {
            "claimantName": parsed.claimant or "",
            "esrReference": parsed.reference,
            "serviceBranch": parsed.service_group or "",
            "benefitProgram": parsed.program or "Compensation & Pension (C&P Live)",
            "city": parsed.city or "",
            "stateCode": parsed.state_code or "",
            "postalCode": parsed.postal_code or "",
            "travelLimitMiles": parsed.travel_limit_miles,
            "contentions": [{"title": c.title, "classification": c.classification, "forms": list(c.requested_forms)} for c in parsed.contentions],
            "specialInstructions": parsed.special_instructions or "",
            "fileType": "XML (EMS Examination Request)",
            "fileSize": len(content),
            "fileCount": 1,
            "pageCount": 1,
            "isDuplicate": bool(existing),
            "existingEsrId": existing["id"] if existing else None,
            "duplicateReason": f"An active ESR with Reference '{parsed.reference}' is already in Intake (Case ID: {existing['id']})." if existing else None,
            "isSimulated": "syn" in (parsed.reference or "").lower() or "example" in (parsed.claimant or "").lower(),
        }

    # 2. Uploaded files inspection (handles folder archive or multiple files)
    extracted = unpack_upload_files(file, files, synthetic)
    if not extracted:
        raise HTTPException(422, "Select at least one document, folder, or choose a bundled request.")

    total_size = sum(len(ct) for _, ct in extracted)
    metadata = extract_metadata_from_extracted_files(extracted)

    claimant_display = (name.strip() if name and name.strip() else metadata["claimantName"])
    if contentions and contentions.strip():
        titles = [t.strip() for t in contentions.splitlines() if t.strip()]
        metadata["contentions"] = [{"title": t, "classification": "Direct Service Connection", "forms": []} for t in titles]

    # Duplicate check on file hashes across queue
    dup_doc = None
    for fn, ct in extracted:
        fh = hashlib.sha256(ct).hexdigest()
        existing = next((doc for doc in state.get("evidenceDocumentsById", {}).values() if doc.get("sourceHash") == fh), None)
        if existing:
            dup_doc = existing
            break

    existing_claimant_esr = None
    if claimant_display:
        for esr in state.get("esrsById", {}).values():
            vet = state.get("veteransById", {}).get(esr.get("veteranId"), {})
            if vet.get("displayName", "").casefold() == claimant_display.casefold():
                existing_claimant_esr = esr["id"]
                break

    is_dup = bool(dup_doc) or bool(existing_claimant_esr)
    dup_reason = (f"File '{dup_doc.get('title')}' was already uploaded in Case '{dup_doc.get('esrId')}'." if dup_doc
                  else f"An active intake case '{existing_claimant_esr}' already exists for claimant '{claimant_display}'." if existing_claimant_esr else None)

    return {
        "claimantName": claimant_display,
        "esrReference": metadata["esrReference"],
        "serviceBranch": metadata["serviceBranch"],
        "benefitProgram": metadata["benefitProgram"],
        "city": metadata.get("city", ""),
        "stateCode": metadata.get("stateCode", ""),
        "postalCode": metadata.get("postalCode", ""),
        "travelLimitMiles": metadata.get("travelLimitMiles"),
        "contentions": metadata["contentions"],
        "specialInstructions": metadata["specialInstructions"],
        "fileType": metadata["fileType"],
        "examManagementRequest": metadata.get("examManagementRequest", False),
        "fileSize": total_size,
        "fileCount": len(extracted),
        "pageCount": metadata.get("total_pages") or len(extracted),
        "isDuplicate": is_dup,
        "existingEsrId": dup_doc.get("esrId") if dup_doc else existing_claimant_esr,
        "duplicateReason": dup_reason,
        "isSimulated": metadata.get("isSimulated", synthetic),
    }


@router.post("/esrs", status_code=202)
def create_esr(
    file: UploadFile = File(None),
    files: list[UploadFile] = File(None),
    name: Annotated[str | None, Form()] = None,
    contentions: Annotated[str | None, Form()] = None,
    special_instructions: Annotated[str | None, Form()] = None,
    service_branch: Annotated[str | None, Form()] = None,
    esr_reference: Annotated[str | None, Form()] = None,
    city: Annotated[str | None, Form()] = None,
    state_code: Annotated[str | None, Form()] = None,
    postal_code: Annotated[str | None, Form()] = None,
    travel_limit_miles: Annotated[int | None, Form()] = None,
    synthetic: Annotated[bool, Form()] = True,
    user: CurrentUser = Depends(intake_actor),
    db: Session = Depends(get_db),
) -> dict:
    extracted = unpack_upload_files(file, files, synthetic)
    if not extracted:
        raise HTTPException(422, "Select at least one document or medical folder.")

    metadata = extract_metadata_from_extracted_files(extracted)

    claimant_name = (name or "").strip() or metadata["claimantName"]
    titles = [t.strip() for t in (contentions or "").splitlines() if t.strip()]
    if not titles:
        titles = [c["title"] for c in metadata["contentions"]]

    instructions = (special_instructions or "").strip() or metadata["specialInstructions"]
    branch = (service_branch or "").strip() or metadata["serviceBranch"]
    ref = (esr_reference or "").strip() or metadata["esrReference"]
    resolved_city = (metadata.get("city") or (city or "").strip()) if metadata.get("examManagementRequest") else ((city or "").strip() or metadata.get("city", ""))
    resolved_state = normalize_state_code((metadata.get("stateCode") or (state_code or "").strip()) if metadata.get("examManagementRequest") else ((state_code or "").strip() or metadata.get("stateCode", "")))
    resolved_postal = (metadata.get("postalCode") or (postal_code or "").strip()) if metadata.get("examManagementRequest") else ((postal_code or "").strip() or metadata.get("postalCode", ""))
    resolved_travel_limit = (
        metadata.get("travelLimitMiles")
        if metadata.get("examManagementRequest") and metadata.get("travelLimitMiles") is not None
        else (_parse_optional_travel_miles(travel_limit_miles) or metadata.get("travelLimitMiles"))
    )
    is_sim = metadata.get("isSimulated", synthetic)

    state = get_state(db)
    now = timestamp()

    # Duplicate prevention: Check if exact file hash is already uploaded
    if len(extracted) == 1:
        file_hash = hashlib.sha256(extracted[0][1]).hexdigest()
        existing_doc = next((doc for doc in state.get("evidenceDocumentsById", {}).values() if doc.get("sourceHash") == file_hash), None)
        if existing_doc:
            raise HTTPException(409, f"This document was already uploaded in Case '{existing_doc.get('esrId')}'. Duplicate case creation blocked.")

    esr = add_case(state, claimant_name, titles, user.user_id, now, is_simulated=is_sim)
    esr["specialInstructions"] = instructions
    esr["isSimulated"] = is_sim
    if ref:
        esr["sourceReference"] = ref
    if resolved_postal:
        esr["postalCode"] = resolved_postal
    if resolved_travel_limit is not None:
        esr["travelLimitMiles"] = resolved_travel_limit

    vet = state["veteransById"].get(esr["veteranId"])
    if vet:
        if branch:
            vet["serviceGroup"] = branch
        if ref:
            vet["claimIdentifier"] = ref
        if resolved_city:
            vet["city"] = resolved_city
        if resolved_state:
            vet["stateCode"] = resolved_state
        if resolved_postal:
            vet["postalCode"] = resolved_postal
        if resolved_travel_limit is not None:
            vet["travelLimitMiles"] = resolved_travel_limit

    # Map requested DBQs to each created contention
    for cid in esr.get("contentionIds", []):
        c_obj = state.get("contentionsById", {}).get(cid)
        if c_obj:
            match = next((m for m in metadata.get("contentions", []) if m["title"].lower() == c_obj["title"].lower()), None)
            if match and match.get("forms"):
                c_obj["requestedDbqNames"] = list(dict.fromkeys(match["forms"]))

    # Queue ALL files from the folder/batch into the new ESR
    doc_ids = []
    for fn, ct in extracted:
        doc_id = queue_document(db, state, esr, fn, ct, now)
        doc_ids.append(doc_id)

    next_state = save_state(db, state, esr["id"])
    db.commit()
    return {"esrId": esr["id"], "documentId": doc_ids[0] if doc_ids else None, "documentIds": doc_ids, "state": with_triage_view(with_intake_views(next_state))}


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
    parsed, content = loaded
    state = get_state(db)
    existing = next((esr for esr in state["esrsById"].values()
                     if esr.get("examManagement", {}).get("esrId") == parsed.reference), None)
    if existing:
        raise HTTPException(409, f"This ESR scenario is already active in Intake (Case ID: {existing['id']}). Duplicate entry blocked.")
    return receive_exam_management(db, user, path.name, content, bundled=True)


@router.post("/exam-management/esrs", status_code=202)
def receive_exam_management_upload(
    file: UploadFile = File(None),
    files: list[UploadFile] = File(None),
    city: Annotated[str | None, Form()] = None,
    state_code: Annotated[str | None, Form()] = None,
    postal_code: Annotated[str | None, Form()] = None,
    synthetic: Annotated[bool, Form()] = True,
    user: CurrentUser = Depends(intake_actor),
    db: Session = Depends(get_db),
) -> dict:
    if not synthetic:
        raise HTTPException(422, "Confirm that the document contains no real claimant records.")
    extracted = unpack_upload_files(file, files, synthetic)
    if not extracted:
        raise HTTPException(422, "Choose an Exam Management XML file or medical folder.")

    xml_item = next(((fn, ct) for fn, ct in extracted if fn.lower().endswith(".xml")), None)
    if not xml_item:
        raise HTTPException(422, "Choose an Exam Management XML file.")

    xml_filename, xml_content = xml_item
    if len(xml_content) > MAX_XML_BYTES:
        raise HTTPException(413, "The XML file may be up to 2 MB.")

    parsed = parse_exam_request(xml_content)
    state = get_state(db)
    existing = next((esr for esr in state["esrsById"].values()
                     if esr.get("examManagement", {}).get("esrId") == parsed.reference), None)
    if existing:
        raise HTTPException(409, f"This ESR already exists in Intake (Case ID: {existing['id']}). Duplicate entry blocked.")

    res = receive_exam_management(db, user, xml_filename, xml_content)
    esr_id = res["esrId"]

    # Fill missing address fields from optional form inputs & queue all non-XML medical files
    non_xml_items = [(fn, ct) for fn, ct in extracted if fn != xml_filename and not fn.lower().endswith(".xml")]
    fallback_city = "" if parsed.city else (city or "").strip()
    fallback_state = "" if parsed.state_code else normalize_state_code(state_code)
    fallback_postal = "" if parsed.postal_code else (postal_code or "").strip()

    if non_xml_items or fallback_city or fallback_state or fallback_postal:
        state = get_state(db)
        esr = state["esrsById"][esr_id]
        vet = state["veteransById"].get(esr.get("veteranId"), {})
        if fallback_city:
            vet["city"] = fallback_city
        if fallback_state:
            vet["stateCode"] = fallback_state
        if fallback_postal:
            vet["postalCode"] = fallback_postal
            esr["postalCode"] = fallback_postal
        now = timestamp()
        doc_ids = []
        for fn, ct in non_xml_items:
            doc_id = queue_document(db, state, esr, fn, ct, now)
            doc_ids.append(doc_id)
        next_state = save_state(db, state, esr["id"])
        db.commit()
        return {"esrId": esr_id, "created": res.get("created", True), "documentIds": doc_ids, "state": with_triage_view(with_intake_views(next_state))}

    return res


@router.post("/esrs/{esr_id}/documents", status_code=202)
def attach_document(esr_id: str,
                    file: UploadFile = File(None),
                    files: list[UploadFile] = File(None),
                    synthetic: Annotated[bool, Form()] = True,
                    _user: CurrentUser = Depends(intake_actor), db: Session = Depends(get_db)) -> dict:
    if not synthetic:
        raise HTTPException(422, "Confirm that the document contains no real claimant records.")
    extracted = unpack_upload_files(file, files, synthetic)
    if not extracted:
        raise HTTPException(422, "Select at least one supported source file or medical folder.")

    state = get_state(db)
    esr = open_case(state, esr_id)
    now = timestamp()
    doc_ids = []
    for fn, ct in extracted:
        doc_id = queue_document(db, state, esr, fn, ct, now)
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


