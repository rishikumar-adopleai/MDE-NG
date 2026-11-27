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
    special_instructions: str = ""
    postal_code: str = ""
    travel_limit_miles: int | None = None

def clean(value: str | None, limit: int) -> str:
    return " ".join((value or "").split())[:limit]


def _parse_optional_miles(value: str | None) -> int | None:
    text = clean(value, 32)
    if not text:
        return None
    m = re.match(r"^(\d{1,4})(?:\s*(?:mi|miles?))?$", text, re.IGNORECASE)
    if not m:
        return None
    miles = int(m.group(1))
    return miles if 1 <= miles <= 5000 else None


def _first_text(node: ElementTree.Element | None, paths: tuple[str, ...], limit: int) -> str:
    if node is None:
        return ""
    for path in paths:
        val = clean(node.findtext(path), limit)
        if val:
            return val
    return ""


def _extract_travel_limit(nodes: tuple[ElementTree.Element | None, ...]) -> int | None:
    tag_paths = (
        "{*}TravelLimitMiles",
        "{*}MaxTravelMiles",
        "{*}MaxTravelDistance",
        "{*}TravelDistanceLimit",
        ".//{*}TravelLimitMiles",
        ".//{*}MaxTravelMiles",
        ".//{*}MaxTravelDistance",
        ".//{*}TravelDistanceLimit",
    )
    attr_names = ("travelLimitMiles", "maxTravelMiles", "maxTravelDistance", "travelDistanceLimit")
    for node in nodes:
        if node is None:
            continue
        for attr in attr_names:
            if parsed := _parse_optional_miles(node.get(attr)):
                return parsed
        for path in tag_paths:
            if parsed := _parse_optional_miles(node.findtext(path)):
                return parsed
    return None

@lru_cache(maxsize=1)
def exam_management_namespace() -> str:
    try:
        return ElementTree.fromstring(reference_bytes(EXCHANGE_SCHEMA)).attrib["targetNamespace"]
    except Exception:
        return "http://va.gov/service/spec/esr/4.7"

def exchange_xml(content: bytes, max_bytes: int = MAX_XML_BYTES) -> ElementTree.Element:
    if not content or len(content) > max_bytes:
        raise ValueError
    xml = content.decode("utf-8-sig")
    if re.search(r"<!\s*(?:DOCTYPE|ENTITY)\b", xml, re.IGNORECASE):
        raise ValueError
    return ElementTree.fromstring(xml)

def parse_exam_request(content: bytes) -> ExamRequest:
    try:
        root = exchange_xml(content)
        tag_name = root.tag.rsplit("}", 1)[-1]
        
        # Format 1: Full Notification envelope
        if tag_name == "ExamManagementEventNotification":
            event = next(
                (child for child in root if child.tag.rsplit("}", 1)[-1] in {
                    "ExamSchedulingRequestCreatedEvent", "ReworkExamSchedulingRequestCreatedEvent"
                }),
                None
            )
            if event is None:
                raise ValueError
            reference = str(UUID(event.attrib["examSchedulingRequestUuid"]))
            event_id = str(UUID(event.attrib["eventUuid"]))
            received = datetime.fromisoformat(event.attrib["eventDtg"].replace("Z", "+00:00"))
            if received.tzinfo is None:
                raise ValueError
            
            person = event.find(".//{*}VeteranServiceMemberInfo")
            if person is None:
                person = event.find(".//{*}ClaimantInfo")
            name = None
            if person is not None:
                name = clean(", ".join(filter(None, (person.get("lastName"), person.get("firstName"))))
                             or person.get("fullName"), 100) or None
            city = _first_text(person, ("{*}Address/{*}City",), 80)
            state_code = _first_text(person, ("{*}Address/{*}State",), 15).upper()
            postal_code = _first_text(
                person,
                ("{*}Address/{*}PostalCode", "{*}Address/{*}ZipCode", "{*}Address/{*}ZIPCode", "{*}Address/{*}Zip"),
                20,
            )
            period = person.find("{*}PeriodsOfService/{*}Period") if person is not None else None
            branch = clean(period.get("branchOfService") if period is not None else None, 60)
            groups = {b.casefold(): b for b in ("Army", "Navy", "Air Force", "Marine Corps", "Coast Guard", "Space Force")}
            claim = next((child for child in event if child.tag.rsplit("}", 1)[-1].endswith("ClaimInformation")), None)
            program = clean(claim.get("benefitProgramType") if claim is not None else None, 100)
            travel_limit_miles = _extract_travel_limit((event, person, claim))
            
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
            
            return ExamRequest(
                reference,
                event_id,
                received.astimezone(UTC).isoformat(),
                name,
                city,
                state_code,
                groups.get(branch.casefold(), branch),
                program,
                tuple(contentions),
                postal_code=postal_code,
                travel_limit_miles=travel_limit_miles,
            )
        
        # Format 2: Direct ExamSchedulingRequest document
        elif tag_name == "ExamSchedulingRequest":
            reference = (
                root.findtext(".//{*}SchedulingRequestUniqueIdentifier")
                or root.attrib.get("examSchedulingRequestUuid")
                or str(uuid4())
            ).strip()
            event_id = root.attrib.get("eventUuid") or str(uuid4())
            received = datetime.now(UTC)

            # Claimant / Veteran Name
            name = None
            vet_name = root.find(".//{*}VeteranName")
            if vet_name is None:
                vet_name = root.find(".//{*}PersonName")
            if vet_name is None:
                vet_name = root.find(".//{*}ClaimantName")
            if vet_name is not None:
                first = vet_name.findtext("{*}FirstName") or ""
                middle = vet_name.findtext("{*}MiddleName") or ""
                last = vet_name.findtext("{*}LastName") or ""
                full = " ".join(filter(None, [first, middle, last]))
                name = clean(full, 100) or None
            
            if not name:
                person = root.find(".//{*}Veteran") or root.find(".//{*}Claimant")
                if person is not None:
                    name = clean(person.findtext("{*}FullName") or person.get("fullName"), 100) or None

            city = _first_text(root, (".//{*}Address/{*}City",), 80)
            state_code = _first_text(root, (".//{*}Address/{*}State",), 15).upper()
            postal_code = _first_text(
                root,
                (".//{*}Address/{*}PostalCode", ".//{*}Address/{*}ZipCode", ".//{*}Address/{*}ZIPCode", ".//{*}Address/{*}Zip"),
                20,
            )
            branch = clean(root.findtext(".//{*}BranchOfService") or root.findtext(".//{*}PeriodOfService/{*}BranchOfService"), 60)
            groups = {b.casefold(): b for b in ("Army", "Navy", "Air Force", "Marine Corps", "Coast Guard", "Space Force")}
            
            program = clean(root.findtext(".//{*}ProgramTypeCode") or root.findtext(".//{*}ClaimLabel") or "Disability Compensation", 100)
            instructions = clean(root.findtext(".//{*}SpecialInstructions") or "", 500)
            travel_limit_miles = _extract_travel_limit((root, root.find(".//{*}Veteran"), root.find(".//{*}Claimant"), root.find(".//{*}Claim")))

            contentions = []
            seen = set()
            entries = root.findall(".//{*}Contention")
            for idx, item in enumerate(entries, 1):
                key = (item.findtext("{*}ContentionUniqueIdentifier") or str(uuid4())).strip()
                title = clean(item.findtext("{*}ContentionName") or item.findtext("{*}Name"), 120)
                if not title:
                    continue
                seen.add(key)
                classification = clean(item.findtext("{*}Classification") or "Direct Service Connection", 120)
                
                # DBQs / Forms
                forms = []
                for dbq in item.findall(".//{*}DBQ"):
                    dbq_name = clean(dbq.findtext("{*}DBQName") or dbq.findtext("{*}Name") or dbq.get("name"), 160)
                    if dbq_name:
                        forms.append(dbq_name)
                for form in item.findall(".//{*}DbqRequest"):
                    form_name = clean(form.get("name") or form.findtext("{*}Name"), 160)
                    if form_name:
                        forms.append(form_name)
                
                contentions.append(ContentionRequest(key, title, classification, tuple(dict.fromkeys(forms))))
            
            if not contentions:
                contentions.append(ContentionRequest(str(uuid4()), "Medical Condition", "Direct Service Connection", ()))

            return ExamRequest(
                reference=reference,
                event_id=event_id,
                received_at=received.isoformat(),
                claimant=name,
                city=city,
                state_code=state_code,
                postal_code=postal_code,
                travel_limit_miles=travel_limit_miles,
                service_group=groups.get(branch.casefold(), branch),
                program=program,
                contentions=tuple(contentions),
                special_instructions=instructions,
            )

        else:
            raise ValueError(f"Unrecognized XML root tag: {tag_name}")

    except (ElementTree.ParseError, ValueError, KeyError) as exc:
        raise HTTPException(422, "Choose a UTF-8 Exam Management 4.7 examination request XML file.") from exc

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
    esr.update(sourceReference=request.reference, specialInstructions=request.special_instructions, examManagement={
        "esrId": request.reference, "eventId": request.event_id, "sourceEventAt": request.received_at,
        "sourceHash": digest, "program": request.program, "schemaVersion": "4.7",
        "provenance": "exam-management-request",
        "city": request.city,
        "stateCode": request.state_code,
        "postalCode": request.postal_code,
        **({"travelLimitMiles": request.travel_limit_miles} if request.travel_limit_miles is not None else {}),
    })
    if request.travel_limit_miles is not None:
        esr["travelLimitMiles"] = request.travel_limit_miles
    claimant = state["veteransById"][esr["veteranId"]]
    if not request.claimant:
        claimant["initials"] = ""
    claimant["claimIdentifier"] = request.reference
    claimant["serviceGroup"] = request.service_group
    claimant["city"] = request.city
    claimant["stateCode"] = request.state_code
    claimant["postalCode"] = request.postal_code
    if request.travel_limit_miles is not None:
        claimant["travelLimitMiles"] = request.travel_limit_miles
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

def scenario_files() -> dict[str, tuple[str, PurePosixPath]]:
    return {}

def read_bundled_request(path: PurePosixPath) -> tuple[ExamRequest, bytes] | None:
    return None

def pending_scenarios(state: dict) -> dict[str, tuple[str, PurePosixPath]]:
    return {}

def add_bundled_requests(db: Session, state: dict, scenarios: dict) -> None:
    pass
