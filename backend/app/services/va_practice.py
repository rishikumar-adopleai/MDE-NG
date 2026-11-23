from app.services.intake_examples import example_claimant
from app.services.intake_store import add_case, apply_intake_command, record, refresh_intake_work, timestamp
from app.services.triage import EXAM_CATALOG, apply_triage_command, initial_plan
from app.services.va_dbq_catalog import catalog_references, requested_form_label
from app.services.va_samples import read_bundled_request, scenario_files
from app.services.workspace_store import run_engine

PRACTICE_COHORT = "exchange-workflow-2"
EXERCISES = {
    "Intake": ("Ready to validate", "Missing evidence", "Acknowledgement pending", "Document exception"),
    "Medical Triage": ("Plan ready to start", "Draft plan", "Clarification pending", "Priority review"),
    "Scheduling": ("Match examiner", "Slot held", "Claimant reached", "Logistics reviewed"),
    "Clinical Evaluation": ("Encounter pending", "Report draft", "Diagnostic review", "QA correction"),
    "Internal QA": ("New signed report", "Checks passed", "Blocking finding", "Advisory finding"),
    "VA Delivery & Review": ("Draft package", "Package validated", "Ready to send", "Awaiting response"),
}


def add_practice_workflow(state: dict) -> dict:
    sources = {}
    for _, path in scenario_files().values():
        loaded = read_bundled_request(path)
        if not loaded:
            continue
        request, _ = loaded
        for contention in request.contentions:
            forms = contention.requested_forms
            plan = initial_plan({"title": contention.title, "requestedDbqNames": forms})
            if plan.examCode and plan.dbqs and any(requested_form_label(form) == plan.dbqs[0] for form in forms):
                if catalog_references(plan.dbqs):
                    sources.setdefault(plan.examCode, (request, contention, path.name, plan))
    if not sources:
        return state

    now = timestamp()
    targets = []
    exercises = ((code, stage, variant, exercise) for stage, labels in EXERCISES.items()
                 for code in sorted(sources) for variant, exercise in enumerate(labels))
    for index, (code, stage, variant, exercise) in enumerate(exercises, 1):
        request, source, filename, plan = sources[code]
        reference = f"PR-{code.upper()}-{index:03}"
        name, _ = example_claimant(f"{PRACTICE_COHORT}:{reference}")
        esr = add_case(state, name, [source.title], "practice-intake", now)
        esr.update(sourceReference=reference, practiceScenario={
            "cohort": PRACTICE_COHORT, "requestReference": request.reference, "sourceFile": filename,
            "name": EXAM_CATALOG[code][0], "exercise": exercise,
        })
        claimant = state["veteransById"][esr["veteranId"]]
        claimant.update(claimIdentifier=reference, serviceGroup=request.service_group,
                        homeFacilityId="", city=request.city, stateCode=request.state_code)
        item = state["contentionsById"][esr["contentionIds"][0]]
        item.update(requestedDbqNames=list(source.requested_forms), externalClassification=source.classification)
        doc_id = f"document-{esr['id']}-practice-note"
        excerpt = (f"Practice exercise: {exercise}. Requested contention: {source.title}. "
                   f"The exchange request lists {', '.join(source.requested_forms)}. "
                   "The exercise provides request scope and a source reference for documentation review. No claimant findings are supplied.")
        if stage != "Intake" or variant != 1:
            state["evidenceDocumentsById"][doc_id] = {
                **record(doc_id, now), "esrId": esr["id"], "title": "Examination request review note",
                "category": "other", "sourceReference": f"{reference} · {filename}",
                "pageCount": 1, "classifiedPageCount": 1, "unreadablePageCount": 0,
                "classificationStatus": "indexed", "contentionIds": [item["id"]],
                "syntheticExcerpts": [{"id": f"{doc_id}-1", "pageNumber": 1, "contentionId": item["id"], "text": excerpt}],
            }
            esr["evidenceDocumentIds"].append(doc_id)
            item["evidenceDocumentIds"].append(doc_id)
        if stage == "Intake" and variant == 2:
            esr["intakeStatus"] = "acknowledgement-pending"
            for name in ("aepRecordedAt", "receiptReference"):
                esr.pop(name, None)
            for key, event in list(state["activitiesById"].items()):
                if event["esrId"] == esr["id"] and event["action"] == "intake-aep-recorded":
                    del state["activitiesById"][key]
        if stage == "Intake" and variant == 3:
            issue_id = f"exception-{esr['id']}"
            state["intakeExceptionsById"][issue_id] = {
                **record(issue_id, now), "esrId": esr["id"], "documentId": doc_id, "kind": "missing-document",
                "title": "Referenced attachment missing", "detail": "Confirm whether the requested supporting attachment is available.",
                "sourceReference": reference, "status": "open",
            }
        if stage == "Medical Triage" and variant == 3:
            esr["priority"] = "urgent"
        refresh_intake_work(state, esr["id"], now)
        if stage == "Intake":
            continue
        state = apply_intake_command(state, "validateIntake", [esr["id"]], "practice-intake")["state"]
        if stage == "Medical Triage":
            if variant == 1:
                state = apply_triage_command(state, "saveTriageDraft", [item["id"], plan.model_dump()], "practice-triage")["state"]
            if variant == 2:
                state = apply_triage_command(state, "requestTriageClarification", [item["id"], "Confirm the examination scope and associated records."], "practice-triage")["state"]
            continue
        approved = apply_triage_command(state, "approveTriagePlan", [item["id"], plan.model_copy(update={
            "modality": "tele-c-and-p", "rationale": "Practice review of the requested scope and reference form.",
            "diagnostics": EXAM_CATALOG[code][5][:1] if stage == "Clinical Evaluation" and variant == 2 else [],
        }).model_dump()], "practice-triage")
        state = approved["state"]
        targets.append({"stage": stage, "variant": variant, "requirementId": approved["value"]["entityId"]})

    state = run_engine({"action": "$prepare-practice", "state": state, "targets": targets})
    state["practiceCohort"] = PRACTICE_COHORT
    return state
