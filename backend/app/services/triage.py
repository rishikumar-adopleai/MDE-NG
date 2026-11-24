from uuid import uuid4

from fastapi import HTTPException
from pydantic import BaseModel, Field, ValidationError

from app.services.intake_store import record, refresh_intake_work, timestamp, touch
from app.services.intake_view import TRIAGE_ACTIVE_STATES, has_readable_evidence, linked_documents
from app.services.va_dbq_catalog import catalog_references, requested_form_label

STANDARD_MODALITIES = ["in-person", "tele-c-and-p"]
RECORDS_REVIEW_MODALITIES = [*STANDARD_MODALITIES, "ace-records-review"]
EXAM_CATALOG = {
    "knee": ("Knee and Lower Leg", "Orthopedics", ["Board certified"], "Knee and Lower Leg Conditions DBQ", STANDARD_MODALITIES, ["Imaging report review"]),
    "hearing": ("Hearing Loss and Tinnitus", "Audiology", ["AuD", "State licensed"], "Hearing Loss and Tinnitus DBQ", STANDARD_MODALITIES, ["Audiology test"]),
    "back": ("Back (Thoracolumbar Spine)", "Orthopedics", ["Board certified"], "Back Conditions DBQ", STANDARD_MODALITIES, ["Imaging report review"]),
    "mental": ("Mental Disorders", "Psychology", ["Licensed psychologist"], "Mental Disorders DBQ", RECORDS_REVIEW_MODALITIES, []),
    "migraine": ("Headaches", "Neurology", ["Board certified"], "Headaches DBQ", RECORDS_REVIEW_MODALITIES, ["Diagnostic study review"]),
    "skin": ("Skin Diseases", "Dermatology", ["Board certified"], "Skin Diseases DBQ", STANDARD_MODALITIES, []),
    "nerve": ("Peripheral Nerves", "Neurology", ["Board certified"], "Peripheral Nerves DBQ", RECORDS_REVIEW_MODALITIES, ["Diagnostic study review"]),
    "shoulder": ("Shoulder and Arm", "Orthopedics", ["Board certified"], "Shoulder and Arm Conditions DBQ", STANDARD_MODALITIES, ["Imaging report review"]),
}
EXAM_ALIASES = {
    "knee": ("knee",), "hearing": ("hearing", "tinnitus"), "back": ("back", "thoracolumbar"),
    "mental": ("mental", "depression", "ptsd", "anxiety"),
    "migraine": ("migraine", "headache"), "skin": ("skin", "rash"),
    "nerve": ("nerve", "neuropathy"), "shoulder": ("shoulder",),
}
ADDITIONAL_DBQS = ["Medical Opinion DBQ", "General Medical DBQ"]
TRIAGE_COMMANDS = frozenset({
    "saveTriageDraft", "approveTriagePlan", "requestTriageClarification", "resumeTriageReview",
})


class Plan(BaseModel):
    examCode: str = Field(default="", max_length=32)
    modality: str = Field(default="", max_length=40)
    dbqs: list[str] = Field(default_factory=list, max_length=8)
    diagnostics: list[str] = Field(default_factory=list, max_length=8)
    rationale: str = Field(default="", max_length=3000)


def exam_choices() -> list[dict]:
    return [
        {"code": code, "title": item[0], "specialty": item[1], "credentials": item[2], "primaryDbq": item[3], "dbqs": [item[3], *ADDITIONAL_DBQS],
         "modalities": item[4], "diagnostics": item[5]}
        for code, item in EXAM_CATALOG.items()
    ]


def permitted_modalities(item: dict, modalities: list[str]) -> list[str]:
    if isinstance(reference := item.get("aceAuthorizationReference"), str) and reference.strip():
        return modalities
    return [modality for modality in modalities if modality != "ace-records-review"]


def initial_plan(item: dict) -> Plan:
    if "triageDraft" in item:
        return Plan.model_validate(item["triageDraft"])
    title = item["title"].casefold()
    requests = [label for name in item.get("requestedDbqNames", []) if (label := requested_form_label(name))]
    candidates = [code for code, mapping in EXAM_CATALOG.items() if mapping[3] in requests]
    code = candidates[0] if len(candidates) == 1 else ""
    if not candidates:
        code = next((code for code, terms in EXAM_ALIASES.items() if any(term in title for term in terms)), "")
    mapped = EXAM_CATALOG.get(code)
    forms = list(dict.fromkeys([mapped[3], *(label for label in requests if label in ADDITIONAL_DBQS)])) if mapped else []
    return Plan(examCode=code, dbqs=forms)


def with_triage_view(state: dict) -> dict:
    rows = []
    for work in state["workItemsById"].values():
        if work["stage"] != "Medical Triage" or work["status"] in {"completed", "cancelled"}:
            continue
        item = state["contentionsById"].get(work["entityId"])
        esr = state["esrsById"].get(item["esrId"]) if item else None
        if not esr or esr.get("workflowClosedAt") or esr["intakeStatus"] not in {"ready-for-triage", "completed"}:
            continue
        if item["status"] not in TRIAGE_ACTIVE_STATES or item.get("simulatedDisposition") == "cancelled":
            continue
        documents = linked_documents(state, item)
        awaiting_response = item["status"] == "clarification-required"
        vet = state.get("veteransById", {}).get(esr.get("veteranId"), {})
        is_sim = esr.get("isSimulated")
        if is_sim is None:
            is_sim = bool(
                esr.get("id", "").startswith("esr-local")
                or "syn" in esr.get("id", "").lower()
                or "example" in (vet.get("displayName") or "").lower()
                or "demo" in (vet.get("displayName") or "").lower()
            )
        rows.append({
            "contentionId": item["id"], "esrId": item["esrId"], "priority": work["priority"],
            "documentIds": [doc["id"] for doc in documents],
            "readableEvidence": has_readable_evidence(documents),
            "awaitingResponse": awaiting_response,
            "requestedDbqs": item.get("requestedDbqNames", []),
            "pathwaysByExam": {code: permitted_modalities(item, exam[4]) for code, exam in EXAM_CATALOG.items()},
            "initialPlan": initial_plan(item).model_dump(),
            "specialInstructions": esr.get("specialInstructions") or "",
            "serviceBranch": vet.get("serviceGroup") or "",
            "isSimulated": is_sim,
        })
    return {**state, "triage": {"rows": rows, "examChoices": exam_choices()}}


def read_plan(payload: object, item: dict) -> Plan:
    try:
        plan = Plan.model_validate(payload)
    except ValidationError as exc:
        raise HTTPException(422, "Enter a valid examination plan.") from exc
    mapping = EXAM_CATALOG.get(plan.examCode)
    if (plan.examCode and not mapping) or (not mapping and (plan.modality or plan.dbqs or plan.diagnostics)):
        raise HTTPException(422, "Select an examination type from the catalog.")
    if mapping and (
        (plan.modality and plan.modality not in permitted_modalities(item, mapping[4]))
        or any(dbq not in [mapping[3], *ADDITIONAL_DBQS] for dbq in plan.dbqs)
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
    value = {}
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
    else:
        plan = read_plan(args[1], item)
        note = plan.rationale
        if action == "saveTriageDraft":
            if plan.examCode and EXAM_CATALOG[plan.examCode][3] not in plan.dbqs:
                plan.dbqs.insert(0, EXAM_CATALOG[plan.examCode][3])
            touch(item, now, triageDraft=plan.model_dump(), status="in-review")
            event = "triage-plan-draft-saved"
        else:
            if not has_readable_evidence(linked_documents(state, item)):
                raise HTTPException(422, "Approval requires readable evidence linked to this contention.")
            if not plan.examCode or not plan.modality or not note:
                raise HTTPException(422, "Select an examination, pathway, and at least one DBQ, and record your rationale.")
            mapping = EXAM_CATALOG[plan.examCode]
            if mapping[3] not in plan.dbqs:
                raise HTTPException(422, "Include the primary DBQ required for this examination.")
            requirement_id = f"req-{uuid4().hex}"
            requirement = {
                **record(requirement_id, now), "esrId": esr["id"], "contentionIds": [item["id"]],
                "title": f"{mapping[0]} examination", "examinationType": mapping[0],
                "requiredSpecialties": [mapping[1]], "requiredCredentials": mapping[2],
                "permittedModalities": permitted_modalities(item, mapping[4]), "selectedModality": plan.modality,
                "requestedDbqTypes": plan.dbqs, "requestedDiagnostics": plan.diagnostics,
                "dbqIds": [], "diagnosticOrderIds": [], "status": "approved", "reviewerRationale": note,
            }
            references = catalog_references(plan.dbqs) if esr.get("examManagement") or esr.get("practiceScenario") else {}
            if references:
                requirement["dbqCatalogReferences"] = references
            state["examinationRequirementsById"][requirement_id] = requirement
            touch(item, now, status="approved-for-exam", triageDraft=plan.model_dump(), reviewerRationale=note,
                  clarificationReason=None, examinationRequirementIds=[*item["examinationRequirementIds"], requirement_id])
            key = f"work-scheduling-{requirement_id}"
            state["workItemsById"][key] = {
                **record(key, now), "esrId": esr["id"], "entityId": requirement_id,
                "entityType": "examination-requirement", "stage": "Scheduling",
                "title": requirement["title"], "priority": esr["priority"], "status": "queued",
                "blockingReasons": [], "assignedTo": esr.get("assignedTeamId"),
            }
            event = "triage-contention-approved"
            value["entityId"] = requirement_id

    key = f"activity-{uuid4()}"
    state["activitiesById"][key] = {
        "id": key, "esrId": esr["id"], "entityId": item["id"], "entityType": "contention",
        "at": now, "actorId": actor, "action": event, "rationale": note,
    }
    refresh_intake_work(state, esr["id"], now)
    state.update(revision=state["revision"] + 1, updatedAt=now)
    return {"state": state, "value": value}
