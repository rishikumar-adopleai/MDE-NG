from base64 import b64decode
from binascii import Error as Base64Error
from datetime import UTC, datetime
from functools import lru_cache
from hashlib import sha256
from pathlib import PurePosixPath
from xml.etree import ElementTree

from app.services.reference_store import EXCHANGE_ROOT, reference_bytes, reference_paths
from app.services.va_samples import (
    clean, exchange_xml, exam_management_namespace,
    read_bundled_request, scenario_files,
)

TRIAGE_EVENTS = {
    "ExamSchedulingRequestCreatedEvent": "Examination requested",
    "ReworkExamSchedulingRequestCreatedEvent": "Examination rework requested",
    "ClarificationRequestEvent": "Clarification requested",
    "EditedRequestClarificationResponseEvent": "Clarification response recorded",
    "ReworkEditedRequestClarificationResponseEvent": "Rework clarification response recorded",
    "SpecialInstructionsEvent": "Special instructions recorded",
    "AddressChangeEvent": "Address change recorded",
    "ContentionCancellationRequestedEvent": "Contention cancellation requested",
    "ContentionCanceledEvent": "Contention canceled",
}
SCHEDULING_EVENTS = {
    "ReadyToScheduleEvent": "Ready to schedule",
    "AppointmentScheduledEvent": "Appointment scheduled",
    "AppointmentCanceledEvent": "Appointment canceled",
    "AppointmentCancelRequestEvent": "Appointment cancellation requested",
    "RescheduleRequestEvent": "Reschedule requested",
}
STAGE_EVENTS = {
    "Medical Triage": TRIAGE_EVENTS,
    "Scheduling": SCHEDULING_EVENTS,
    "Clinical Evaluation": {"AppointmentCompletedEvent": "Appointment completion recorded"},
    "VA Delivery & Review": {"ResultPackageAvailableEvent": "Result package available",
                             "ExamSchedulingRequestCancellationEvent": "Request cancellation recorded"},
}
DOCUMENT_EVENTS = {"VeteranDocumentManifestEvent", "VeteranDocumentEvent"}
MAX_BUNDLED_EVENT_BYTES = 8 * 1024 * 1024


def event_time(value: str | None) -> str:
    if not value:
        return ""
    try:
        result = datetime.fromisoformat(value.replace("Z", "+00:00"))
        return result.astimezone(UTC).isoformat() if result.tzinfo else ""
    except ValueError:
        return ""


def attachment_facts(event: ElementTree.Element) -> dict:
    attachment = event.find("{*}Attachment")
    if attachment is None:
        return {}
    try:
        binary = b64decode("".join((attachment.findtext("{*}DocumentBinary") or "").split()), validate=True)
        declared = clean(attachment.findtext("{*}Sha256Hash"), 64).lower()
        verified = bool(binary and sha256(binary).hexdigest() == declared
                        and len(binary) == int(attachment.findtext("{*}FileSizeBytes") or -1))
    except (Base64Error, ValueError):
        verified = False
    return {
        "id": clean(attachment.findtext("{*}DocumentId"), 70),
        "title": clean(attachment.findtext("{*}DocumentType"), 150) or "Referenced document",
        "verified": verified,
        "manifestId": clean(event.findtext("{*}ManifestEventUuid"), 70),
    }


@lru_cache(maxsize=512)
def read_events(path: PurePosixPath, namespace: str) -> tuple[dict, ...]:
    try:
        root = exchange_xml(reference_bytes(path), MAX_BUNDLED_EVENT_BYTES)
    except (ValueError, ElementTree.ParseError):
        return ()
    if root.tag != f"{{{namespace}}}ExamManagementEventNotification":
        return ()
    events = []
    for event in root:
        if not event.tag.startswith(f"{{{namespace}}}") or not event.get("examSchedulingRequestUuid"):
            continue
        kind = event.tag.split("}", 1)[1]
        facts = {
            "Request reference": clean(event.get("examSchedulingRequestUuid"), 70),
            "Event reference": clean(event.get("eventUuid"), 70),
            "Examination": clean(event.get("examName"), 120),
            "Appointment reference": clean(event.get("examAppointmentUuid"), 70),
            "Contention reference": clean(event.get("examRelatedContentionUuid"), 70),
            "Document reference": clean(event.findtext("{*}documentReferenceId"), 70),
        }
        facility = event.find("{*}ExaminingFacility")
        if facility is not None:
            facts["Examining facility"] = clean(facility.get("name"), 160)
        examiner = event.find("{*}nameOfExaminer")
        if examiner is not None:
            facts["Examiner"] = clean(" ".join(filter(None, (examiner.findtext("{*}FirstName"), examiner.findtext("{*}LastName")))), 120)
        events.append({
            "kind": kind, "at": event_time(event.get("eventDtg")),
            "appointmentAt": event_time(event.get("scheduledDateTime")),
            "facts": {key: value for key, value in facts.items() if value},
            "attachment": attachment_facts(event) if kind == "VeteranDocumentEvent" else {},
            "documentIds": tuple(clean(node.text, 70) for node in event.findall("{*}DocumentId")),
        })
    return tuple(events)


def scenario_events(directory: PurePosixPath, kinds: set[str] | dict[str, str]):
    namespace = exam_management_namespace()
    for path in reference_paths():
        if not path.is_relative_to(directory) or path.suffix != ".xml":
            continue
        for event in read_events(path, namespace):
            if event["kind"] in kinds:
                yield path, event


def exchange_records(stage: str) -> list[dict]:
    kinds = STAGE_EVENTS.get(stage, {})
    if not kinds:
        return []
    records = {}
    scenarios = {}
    for scenario, request_path in scenario_files().values():
        loaded = read_bundled_request(request_path)
        if loaded:
            request, _ = loaded
            scenarios.setdefault(request_path.parent, {})[request.reference] = (scenario, request_path, request)
    for directory, requests in scenarios.items():
        for path, event in scenario_events(directory, kinds):
            reference = event["facts"].get("Request reference", "").lower()
            scenario, request_path, request = requests.get(reference, (directory.name.replace("_", " "), None, None))
            relative = path.relative_to(EXCHANGE_ROOT).as_posix()
            key = sha256(f"{relative}:{event['kind']}:{event['facts'].get('Event reference', '')}".encode()).hexdigest()[:24]
            facts = event["facts"].copy()
            if request and event["kind"] in TRIAGE_EVENTS and path == request_path:
                facts["Requested contentions"] = "; ".join(item.title for item in request.contentions)
                facts["Requested forms"] = "; ".join(dict.fromkeys(form for item in request.contentions for form in item.requested_forms))
            if request is None:
                facts["Request link"] = "No matching request in this exchange example."
            records[key] = {
                "id": key, "title": kinds[event["kind"]], "scenario": scenario,
                "claimant": (request.claimant or "") if request else "", "at": event["at"], "appointmentAt": event["appointmentAt"],
                "source": path.name, "facts": {name: value for name, value in facts.items() if value},
            }
    return sorted(records.values(), key=lambda row: (row["at"], row["scenario"], row["id"]), reverse=True)


def document_references(directory: PurePosixPath, reference: str) -> list[tuple[PurePosixPath, dict]]:
    events = [(path, event) for path, event in scenario_events(directory, DOCUMENT_EVENTS)
              if event["facts"].get("Request reference", "").lower() == reference]
    manifests = {
        event["facts"].get("Event reference"): set(event["documentIds"])
        for _, event in events if event["kind"] == "VeteranDocumentManifestEvent"
    }
    listed_ids = set().union(*manifests.values())
    references = {}
    for path, event in events:
        attachment = event["attachment"]
        if not attachment or attachment["id"] not in listed_ids:
            continue
        manifest_matches = attachment["id"] in manifests.get(attachment["manifestId"], set())
        references[attachment["id"]] = (path, {**attachment, "manifestMatches": manifest_matches})
    return list(references.values())
