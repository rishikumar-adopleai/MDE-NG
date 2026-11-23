import re
from dataclasses import dataclass
from datetime import UTC, datetime
from functools import lru_cache
from hashlib import sha256
from pathlib import PurePosixPath
from uuid import UUID, uuid4
from xml.etree import ElementTree

from fastapi import HTTPException
from sqlalchemy.orm import Session

from app.models import OutboxJob
from app.services.intake_store import add_case, record, refresh_intake_work, timestamp
from app.services.reference_store import EXCHANGE_ROOT, EXCHANGE_SCHEMA, reference_bytes, reference_paths

MAX_XML_BYTES = 2 * 1024 * 1024
DOCUMENT_REFERENCE_VERSION = 2
SAMPLE_SCENARIOS = {
    "basic": ("Examination request", "basic_exam_schedule_request"),
    "multiple-contentions": ("Request with multiple contentions", "basic_exam_schedule_request_with_mult_contentions"),
    "special-instructions": ("Request with special instructions", "basic_exam_schedule_request_with_special_instructions"),
    "document-manifest": ("Request with document references", "Veteran Documents Example 2 (20 Docs)"),
}


@dataclass(frozen=True)
class ContentionRequest:
    reference: str
    title: str
    classification: str
    requested_forms: tuple[str, ...]


@dataclass(frozen=True)
class ExamRequest:
    reference: str
    event_id: str
    received_at: str
    claimant: str | None
    city: str
    state_code: str
    service_group: str
    program: str
    contentions: tuple[ContentionRequest, ...]


def clean(value: str | None, limit: int) -> str:
    return " ".join((value or "").split())[:limit]


@lru_cache(maxsize=1)
def exam_management_namespace() -> str:
    try:
        return ElementTree.fromstring(reference_bytes(EXCHANGE_SCHEMA)).attrib["targetNamespace"]
    except (ElementTree.ParseError, KeyError) as exc:
        raise HTTPException(503, "The Exam Management schema is unavailable.") from exc


def exchange_xml(content: bytes, max_bytes: int = MAX_XML_BYTES) -> ElementTree.Element:
    if not content or len(content) > max_bytes:
        raise ValueError
    xml = content.decode("utf-8-sig")
    if re.search(r"<!\s*(?:DOCTYPE|ENTITY)\b", xml, re.IGNORECASE):
        raise ValueError
    return ElementTree.fromstring(xml)


def parse_exam_request(content: bytes) -> ExamRequest:
    namespace = exam_management_namespace()
    try:
        root = exchange_xml(content)
        if root.tag != f"{{{namespace}}}ExamManagementEventNotification":
            raise ValueError
        event, = [child for child in root if child.tag in {
            f"{{{namespace}}}ExamSchedulingRequestCreatedEvent", f"{{{namespace}}}ReworkExamSchedulingRequestCreatedEvent",
        }]
        reference = str(UUID(event.attrib["examSchedulingRequestUuid"]))
        event_id = str(UUID(event.attrib["eventUuid"]))
        received = datetime.fromisoformat(event.attrib["eventDtg"].replace("Z", "+00:00"))
        if received.tzinfo is None:
            raise ValueError
    except (ElementTree.ParseError, ValueError, KeyError) as exc:
        raise HTTPException(422, "Choose a UTF-8 Exam Management 4.7 examination request XML file.") from exc

    person = event.find(".//{*}VeteranServiceMemberInfo")
    if person is None:
        person = event.find(".//{*}ClaimantInfo")
    name = None
    if person is not None:
        name = clean(", ".join(filter(None, (person.get("lastName"), person.get("firstName"))))
                     or person.get("fullName"), 100) or None
    city = clean(person.findtext("{*}Address/{*}City") if person is not None else None, 80)
    state_code = clean(person.findtext("{*}Address/{*}State") if person is not None else None, 15)
    period = person.find("{*}PeriodsOfService/{*}Period") if person is not None else None
    branch = clean(period.get("branchOfService") if period is not None else None, 60)
    groups = {name.casefold(): name for name in ("Army", "Navy", "Air Force", "Marine Corps", "Coast Guard", "Space Force")}
    claim = next((child for child in event if child.tag.rsplit("}", 1)[-1].endswith("ClaimInformation")), None)
    program = clean(claim.get("benefitProgramType") if claim is not None else None, 100)
    contentions = []
    seen = set()
    entries = event.findall("{*}ExamRelatedContentions/{*}ExamRelatedContention")
    entries += event.findall("{*}ExamReworkRelatedContentions/{*}ReworkContention")
    for item in entries:
        if (item.findtext("{*}HasBeenRemoved") or "").strip().lower() in {"true", "1"}:
            continue
        try:
            key = str(UUID(item.attrib["examRelatedContentionUuid"]))
        except (KeyError, ValueError) as exc:
            raise HTTPException(422, "Each active contention needs a valid request identifier.") from exc
        title = clean(item.findtext("{*}ContentionName"), 120)
        if key in seen or not title:
            raise HTTPException(422, "Each active contention needs a unique identifier and a title.")
        seen.add(key)
        classification = item.find("{*}Classification")
        forms = tuple(dict.fromkeys(
            label for form in item.findall("{*}DbqRequests/{*}DbqRequest") if (label := clean(form.get("name"), 160))
        ))
        contentions.append(ContentionRequest(key, title, clean(classification.get("name") if classification is not None else None, 120), forms))
    if not 1 <= len(contentions) <= 100:
        raise HTTPException(422, "The request must contain between 1 and 100 active contentions.")
    return ExamRequest(reference, event_id, received.astimezone(UTC).isoformat(), name, city, state_code,
                       groups.get(branch.casefold(), ""), program, tuple(contentions))


@lru_cache(maxsize=160)
def read_bundled_request(path: PurePosixPath) -> tuple[ExamRequest, bytes] | None:
    content = reference_bytes(path)
    try:
        return parse_exam_request(content), content
    except HTTPException as exc:
        if exc.status_code != 422:
            raise
        return None


@lru_cache(maxsize=1)
def scenario_files() -> dict[str, tuple[str, PurePosixPath]]:
    candidates = [path for path in reference_paths() if path.is_relative_to(EXCHANGE_ROOT) and path.match("*RequestCreatedEvent*.xml")]
    scenarios = {
        key: (title, path)
        for key, (title, directory) in SAMPLE_SCENARIOS.items()
        if (path := next((item for item in candidates if item.parent == EXCHANGE_ROOT / directory
                          and item.match("1_ExamSchedulingRequestCreatedEvent*.xml")), None))
    }
    selected = {path for _, path in scenarios.values()}
    for path in candidates:
        if path in selected or not read_bundled_request(path):
            continue
        relative = path.relative_to(EXCHANGE_ROOT)
        key = f"bundle-{sha256(relative.as_posix().encode()).hexdigest()[:16]}"
        title = " · ".join(re.sub(r"(?<=[a-z])(?=[A-Z])", " ", part.replace("_", " ")) for part in relative.parts[:-1])
        if sum(item.parent == path.parent for item in candidates) > 1:
            title = f"{title} · {path.stem.split('_', 1)[0]}"
        scenarios[key] = (title, path)
    return scenarios


def import_exam_request(db: Session, state: dict, request: ExamRequest, original: bytes,
                        filename: str, actor: str) -> tuple[dict, bool]:
    digest = sha256(original).hexdigest()
    for existing in state["esrsById"].values():
        if existing.get("examManagement", {}).get("esrId") == request.reference:
            if existing["examManagement"]["sourceHash"] != digest:
                raise HTTPException(409, "This ESR already exists with a different source payload.")
            return existing, False

    now = timestamp()
    esr = add_case(state, request.claimant or "Claimant not provided", [item.title for item in request.contentions], actor, now)
    esr.pop("intakeOrigin", None)
    esr.update(sourceReference=request.reference, examManagement={
        "esrId": request.reference, "eventId": request.event_id, "sourceEventAt": request.received_at,
        "sourceHash": digest, "program": request.program, "schemaVersion": "4.7",
        "provenance": "exam-management-request",
    })
    claimant = state["veteransById"][esr["veteranId"]]
    if not request.claimant:
        claimant["initials"] = ""
    claimant["claimIdentifier"] = request.reference
    claimant["serviceGroup"] = request.service_group
    claimant["city"] = request.city
    claimant["stateCode"] = request.state_code
    claimant["homeFacilityId"] = next((
        facility["id"] for facility in state["facilitiesById"].values()
        if request.city and request.state_code and facility["city"].casefold() == request.city.casefold()
        and facility["stateCode"].casefold() == request.state_code.casefold()
    ), "")
    archive_id = str(uuid4())
    doc_id = f"doc-{archive_id}"

    excerpts = []
    for page, (key, item) in enumerate(zip(esr["contentionIds"], request.contentions, strict=True), 1):
        contention = state["contentionsById"][key]
        contention.update({"sourceReference": item.reference, "requestedDbqNames": list(item.requested_forms),
                           "externalClassification": item.classification, "evidenceDocumentIds": [doc_id]})
        text = "\n".join(filter(None, [
            f"Requested contention: {item.title}",
            f"Classification: {item.classification}" if item.classification else "",
            f"Requested forms: {', '.join(item.requested_forms)}" if item.requested_forms else "",
        ]))
        excerpts.append({"id": f"{doc_id}-{page}", "pageNumber": page, "contentionId": key, "text": text})

    state["evidenceDocumentsById"][doc_id] = {
        **record(doc_id, now), "esrId": esr["id"], "title": "Examination scheduling request", "category": "va-request",
        "sourceReference": filename, "sourceHash": digest, "pageCount": 0, "classifiedPageCount": 0,
        "unreadablePageCount": 0, "classificationStatus": "indexed", "administrativeOnly": True,
        "contentionIds": esr["contentionIds"][:], "syntheticExcerpts": excerpts,
    }
    esr["evidenceDocumentIds"] = [doc_id]
    esr["intakeStatus"] = "requires-attention"
    db.add(OutboxJob(id=archive_id, task_name="intake.archive_exam_request", status="completed",
                     payload={"documentId": doc_id, "filename": filename, "xml": original.decode("utf-8-sig")}))
    refresh_intake_work(state, esr["id"], now)
    return esr, True


def pending_scenarios(state: dict) -> dict[str, tuple[str, PurePosixPath]]:
    loaded = set(state.get("loadedExamRequestScenarios", [])) if state.get("documentReferenceVersion") == DOCUMENT_REFERENCE_VERSION else set()
    return {key: value for key, value in scenario_files().items() if key not in loaded}


def add_bundled_requests(db: Session, state: dict, scenarios: dict[str, tuple[str, PurePosixPath]]) -> None:
    from app.services.va_exchange_records import document_references

    references = {esr["examManagement"]["esrId"]: esr for esr in state["esrsById"].values() if esr.get("examManagement")}
    processed = set(state.get("loadedExamRequestScenarios", []))
    for key, (_, path) in scenarios.items():
        loaded = read_bundled_request(path)
        if not loaded:
            continue
        request, content = loaded
        if request.reference not in references:
            esr, _ = import_exam_request(db, state, request, content, path.name, "workspace-initialization")
            references[request.reference] = esr
        esr = references[request.reference]
        if esr["examManagement"]["sourceHash"] == sha256(content).hexdigest():
            for attachment_path, item in document_references(path.parent, request.reference):
                doc_id = f"exchange-document-{sha256((esr['id'] + item['id']).encode()).hexdigest()[:24]}"
                if doc_id in state["evidenceDocumentsById"]:
                    continue
                issues = []
                if not item["manifestMatches"]:
                    issues.append("The attachment points to a different manifest.")
                if not item["verified"]:
                    issues.append("The embedded file failed its integrity check.")
                state["evidenceDocumentsById"][doc_id] = {
                    **record(doc_id, timestamp()), "esrId": esr["id"], "title": item["title"],
                    "sourceReference": attachment_path.name, "category": "other", "pageCount": 0,
                    "classifiedPageCount": 0, "unreadablePageCount": 0, "administrativeOnly": True,
                    "classificationStatus": "needs-human-review", "contentionIds": [],
                    "processing": {"status": "needs-review", "error": " ".join(issues or ["Document reference verified."])
                                   + " Attach a verified copy after staff review."},
                }
                esr["evidenceDocumentIds"].append(doc_id)
        processed.add(key)
    state["loadedExamRequestScenarios"] = sorted(processed)
    state["documentReferenceVersion"] = DOCUMENT_REFERENCE_VERSION
