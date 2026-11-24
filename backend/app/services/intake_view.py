import re
from collections import defaultdict

from app.services.intake_common import example_claimant, is_legacy_example

READABLE_DOCUMENT_STATES = {"indexed", "needs-human-review"}
TRIAGE_ACTIVE_STATES = {"awaiting-review", "in-review", "clarification-required"}
READABLE_EVIDENCE_BLOCKER = "Attach a readable source document for each contention before validating Intake."


def linked_documents(state: dict, item: dict) -> list[dict]:
    return [
        document for key in item["evidenceDocumentIds"]
        if (document := state["evidenceDocumentsById"].get(key)) and document["esrId"] == item["esrId"]
    ]


def has_readable_evidence(documents: list[dict]) -> bool:
    return any(not document.get("administrativeOnly") and document["classifiedPageCount"] > 0
               and document["classificationStatus"] in READABLE_DOCUMENT_STATES
               for document in documents)


def has_intake_evidence(state: dict, esr: dict, documents: list[dict]) -> bool:
    if not has_readable_evidence(documents):
        return False
    return all(
        has_readable_evidence(linked_documents(state, item))
        for key in esr["contentionIds"]
        if (item := state["contentionsById"].get(key)) and item.get("status") in TRIAGE_ACTIVE_STATES
        and item.get("simulatedDisposition") != "cancelled"
    )


def public_processing_message(message: str) -> str:
    if "azure openai" in message.casefold().replace("-", " "):
        return "AI review is temporarily unavailable. Try again later."
    return message


def public_document(document: dict) -> dict:
    processing = document.get("processing")
    if not processing:
        return document
    facts = {key: processing[key] for key in (
        "status", "finishedAt", "summary", "retryable", "aiUnavailable", "sources",
    ) if key in processing}
    facts["provider"] = "ai" if processing.get("provider") == "azure-openai" else "local"
    if "error" in processing:
        facts["error"] = public_processing_message(processing["error"])
    return {**document, "processing": facts}


def with_intake_views(state: dict) -> dict:
    """Build display state without writing derived fields into the workspace snapshot."""
    exceptions = defaultdict(list)
    activities = defaultdict(list)
    for issue in state["intakeExceptionsById"].values():
        exceptions[issue["esrId"]].append(issue)
    for event in state["activitiesById"].values():
        activities[event["esrId"]].append(event)

    queue_ids = {
        item.get("esrId") for item in state["workItemsById"].values()
        if item.get("esrId") in state["esrsById"] and item.get("stage") == "Intake" and item.get("status") not in {"completed", "cancelled"}
    }
    veterans = state["veteransById"].copy()
    example_ids = set()
    for esr in state["esrsById"].values():
        if esr.get("intakeOrigin") == "example":
            example_ids.add(esr["id"])
        elif is_legacy_example(state, esr):
            example_ids.add(esr["id"])
            veteran_id = esr["veteranId"]
            name, initials = example_claimant(esr["id"])
            veterans[veteran_id] = {**veterans[veteran_id], "displayName": name, "initials": initials}

    views = {}
    queue_order = sorted(queue_ids, key=lambda key: (key not in example_ids, state["esrsById"][key]["receivedAt"], key),
                         reverse=True)
    for esr_id in queue_order:
        esr = state["esrsById"][esr_id]
        documents = [
            state["evidenceDocumentsById"][key] for key in esr["evidenceDocumentIds"]
            if key in state["evidenceDocumentsById"]
        ]
        issues = sorted(exceptions[esr_id], key=lambda issue: issue.get("createdAt", ""))
        open_count = sum(issue["status"] == "open" for issue in issues)
        escalated_count = sum(issue["status"] == "escalated" for issue in issues)
        receipt_ready = esr.get("receiptStatus") == "received" and bool(esr.get("aepRecordedAt"))
        pending = [item for item in documents if item["classificationStatus"] in {"queued", "processing"}]
        evidence_ready = has_intake_evidence(state, esr, documents)
        blocker = None
        if not receipt_ready:
            blocker = "Record the receipt and acknowledgement first."
        elif not documents:
            blocker = "Attach a source document before validating Intake."
        elif open_count:
            blocker = "Resolve or escalate all open exceptions before validating Intake."
        elif pending:
            blocker = "Wait for document processing before validating Intake."
        elif not evidence_ready:
            blocker = READABLE_EVIDENCE_BLOCKER

        page_count = sum(item["pageCount"] for item in documents)
        readable_pages = sum(item["classifiedPageCount"] for item in documents)
        unreadable_pages = sum(item["unreadablePageCount"] for item in documents)

        # EMS Document Manifest & Receipt Verification
        manifest = esr.get("documentManifest")
        expected_docs = manifest.get("expectedDocuments", []) if manifest else []
        manifest_status = "unverified"
        manifest_summary = "Unverified · No authoritative document manifest attached"
        missing_docs = []

        if manifest:
            received_titles = [doc.get("title", doc.get("sourceReference", "")).lower() for doc in documents]
            received_refs = [doc.get("sourceReference", doc.get("title", "")).lower() for doc in documents]
            for exp in expected_docs:
                exp_title = exp.get("title", "").lower()
                exp_id = exp.get("id", "").lower()
                matched = any(exp_title in t or t in exp_title for t in received_titles if t) or any(exp_id in r or r in exp_id for r in received_refs if r)
                if not matched:
                    missing_docs.append(exp)

            if missing_docs:
                manifest_status = "discrepancy"
                manifest_summary = f"{len(documents)} of {len(expected_docs)} received · {len(missing_docs)} missing"
            else:
                manifest_status = "verified"
                manifest_summary = f"{len(documents)} of {len(expected_docs)} received · Verified complete"
        else:
            manifest_status = "unverified"
            manifest_summary = f"{len(documents)} received · Manifest unverified"

        cat_counts = defaultdict(int)
        for doc in documents:
            cat_counts[doc.get("category", "other")] += 1

        # AI Document Organizer data
        medical_docs = [d for d in documents if not d.get("administrativeOnly") and d.get("category") not in {"va-request", "examination-requests"}]
        metadata_docs = [d for d in documents if d.get("administrativeOnly") or d.get("category") in {"va-request", "examination-requests"}]

        # Build Logical Document Segments as Primary Organization Units
        logical_medical_docs = []
        logical_metadata_docs = []

        open_duplicate_exceptions = {
            ex.get("documentId")
            for ex in state.get("intakeExceptionsById", {}).values()
            if ex.get("esrId") == esr_id and ex.get("kind") in {"duplicate-document", "duplicate"} and ex.get("status") in {"open", "escalated"}
        }

        for d in documents:
            if d.get("classificationStatus") == "excluded" or d.get("isExcluded"):
                continue
            is_meta = d.get("administrativeOnly") or d.get("category") in {"va-request", "examination-requests"}
            sections = d.get("sections", [])
            if sections:
                for sec in sections:
                    sec_cat = sec.get("category", d.get("category", "other"))
                    if sec_cat == "va-request":
                        sec_cat = "examination-requests"
                    sec_is_meta = sec_cat == "examination-requests" or is_meta
                    is_dup_sec = sec.get("isDuplicate", False) and (d["id"] in open_duplicate_exceptions or d.get("classificationStatus") == "needs-human-review")
                    sec_issues = [i for i in sec.get("issues", []) if is_dup_sec or not i.lower().startswith("possible duplicate")]
                    sec_action = (sec.get("actionRequired", False) or sec.get("status") == "needs-human-review" or d.get("classificationStatus") == "needs-human-review") and (is_dup_sec or not sec.get("isDuplicate"))
                    item = {
                        "id": f"{d['id']}::{sec['sectionId']}" if len(sections) > 1 else d["id"],
                        "parentDocumentId": d["id"],
                        "sectionId": sec["sectionId"],
                        "title": sec.get("title", d.get("title")),
                        "category": sec_cat,
                        "startPage": sec.get("startPage", 1),
                        "endPage": sec.get("endPage", sec.get("startPage", 1)),
                        "pageCount": sec.get("pageCount", d.get("pageCount", 1)),
                        "sourceReference": sec.get("sourceReference") or f"{d.get('sourceReference', d.get('title'))} (pp. {sec.get('startPage', 1)}–{sec.get('endPage', 1)})",
                        "parentFileName": d.get("sourceReference", d.get("title", "")),
                        "classificationStatus": "indexed" if not sec_action and d.get("classificationStatus") != "needs-human-review" else sec.get("status", "indexed"),
                        "confidence": sec.get("confidence", 95),
                        "readabilityScore": sec.get("readabilityScore", 100),
                        "ocrApplied": sec.get("ocrApplied", False),
                        "ocrConfidence": sec.get("ocrConfidence", 95),
                        "isDuplicate": is_dup_sec,
                        "duplicateOf": sec.get("duplicateOf") if is_dup_sec else None,
                        "claimantMatch": sec.get("claimantMatch", "aligned"),
                        "wrongPatient": sec.get("wrongPatient", False),
                        "actionRequired": sec_action,
                        "issues": sec_issues,
                        "extractedData": sec.get("extractedData", {}),
                        "summary": sec.get("summary", ""),
                        "administrativeOnly": sec_is_meta,
                    }
                    if sec_is_meta:
                        logical_metadata_docs.append(item)
                    else:
                        logical_medical_docs.append(item)
            else:
                is_doc_dup = (d.get("isDuplicate", False) or d["id"] in open_duplicate_exceptions) and (d["id"] in open_duplicate_exceptions or d.get("classificationStatus") == "needs-human-review")
                doc_action = (d.get("classificationStatus") == "needs-human-review" or is_doc_dup)
                item = {
                    "id": d["id"],
                    "parentDocumentId": d["id"],
                    "sectionId": "sec-1",
                    "title": d.get("title", "Clinical Document"),
                    "category": d.get("category", "other"),
                    "startPage": 1,
                    "endPage": d.get("pageCount", 1),
                    "pageCount": d.get("pageCount", 1),
                    "sourceReference": d.get("sourceReference", d.get("title", "")),
                    "parentFileName": d.get("sourceReference", d.get("title", "")),
                    "classificationStatus": "indexed" if not doc_action else d.get("classificationStatus", "indexed"),
                    "confidence": 95,
                    "readabilityScore": (d.get("quality", {}) or {}).get("readabilityScore", 100),
                    "ocrApplied": (d.get("quality", {}) or {}).get("ocrApplied", False),
                    "ocrConfidence": (d.get("quality", {}) or {}).get("ocrConfidence", 100),
                    "isDuplicate": is_doc_dup,
                    "duplicateOf": d.get("duplicateOf") if is_doc_dup else None,
                    "claimantMatch": (d.get("quality", {}) or {}).get("claimantMatch", "aligned"),
                    "wrongPatient": (d.get("quality", {}) or {}).get("claimantMatch") == "mismatch",
                    "actionRequired": doc_action,
                    "issues": [i for i in d.get("issues", []) if is_doc_dup or not i.lower().startswith("possible duplicate")],
                    "extractedData": d.get("extractedData", {}),
                    "summary": (d.get("processing", {}) or {}).get("summary", ""),
                    "administrativeOnly": is_meta,
                }
                if is_meta:
                    logical_metadata_docs.append(item)
                else:
                    logical_medical_docs.append(item)

        # Duplicate detection by SHA-256: count only redundant duplicate files that are still open/unreviewed
        duplicate_ids = set()
        seen_shas = set()
        for d in documents:
            if d.get("classificationStatus") == "excluded" or d.get("isExcluded"):
                continue
            sha = d.get("sourceHash") or (d.get("quality", {}) or {}).get("sha256")
            if sha:
                if sha in seen_shas:
                    if d["id"] in open_duplicate_exceptions or d.get("classificationStatus") == "needs-human-review":
                        duplicate_ids.add(d["id"])
                else:
                    seen_shas.add(sha)
            elif d.get("isDuplicate") and (d["id"] in open_duplicate_exceptions or d.get("classificationStatus") == "needs-human-review"):
                duplicate_ids.add(d["id"])

        unreadable_docs = [item["id"] for item in logical_medical_docs if item.get("readabilityScore", 100) == 0]
        corrupted_docs = [d["id"] for d in documents if (d.get("quality", {}) or {}).get("fileIntegrity") == "corrupt" or d.get("classificationStatus") == "failed"]
        mismatch_docs = [item["id"] for item in logical_medical_docs if item.get("wrongPatient") or item.get("claimantMatch") == "mismatch"]
        action_required_docs = [item["id"] for item in logical_medical_docs if item.get("actionRequired")]

        CATEGORY_CONFIG = [
            ("claims-and-applications", "Claims and Applications"),
            ("service-treatment", "Service Treatment Records (STR)"),
            ("military-personnel", "Military Personnel Records"),
            ("va-medical-records", "VA Medical Records"),
            ("private-medical-records", "Private Medical Records"),
            ("prior-examination", "Previous Examinations & DBQs"),
            ("laboratory", "Laboratory Reports"),
            ("imaging", "Diagnostic Imaging"),
            ("diagnostic-tests", "Diagnostic & Ancillary Tests"),
            ("hospital-surgical", "Hospital & Surgical Records"),
            ("medication-prescription", "Medication & Prescription Records"),
            ("medical-opinions", "Medical Opinions"),
            ("health-assessments", "Health Assessments"),
            ("authorization-consent", "Authorization & Consent"),
            ("lay-witness", "Lay & Witness Statements"),
            ("representation", "Representation Documents"),
            ("decisions-appeals", "Disability Decisions & Appeals"),
            ("insurance-financial", "Insurance & Financial Records"),
            ("administrative-correspondence", "Administrative Correspondence"),
            ("other", "Others"),
        ]

        organizer_categories = []
        for cat_key, cat_label in CATEGORY_CONFIG:
            matching = [
                item for item in logical_medical_docs
                if item.get("category") == cat_key or (cat_key == "va-medical-records" and item.get("category") == "hospital")
            ]
            organizer_categories.append({
                "key": cat_key,
                "label": cat_label,
                "docCount": len(matching),
                "pageCount": sum(item.get("pageCount", 0) for item in matching),
                "readablePages": sum(item.get("pageCount", 0) for item in matching if item.get("readabilityScore", 100) > 0),
                "documentIds": [item["id"] for item in matching],
            })

        med_pages = sum(item.get("pageCount", 0) for item in logical_medical_docs)
        med_readable = sum(item.get("pageCount", 0) for item in logical_medical_docs if item.get("readabilityScore", 100) > 0)
        quality_score = round(med_readable * 100 / max(1, med_pages)) if med_pages else 100

        ai_organizer = {
            "categories": organizer_categories,
            "medicalDocuments": [d["id"] for d in medical_docs],
            "metadataDocuments": [d["id"] for d in metadata_docs],
            "logicalDocuments": logical_medical_docs,
            "logicalMetadataDocuments": logical_metadata_docs,
            "totalPhysicalFiles": len(documents),
            "totalPhysicalMedicalFiles": len(medical_docs),
            "totalLogicalDocs": len(logical_medical_docs),
            "totalMetadataDocs": len(metadata_docs) + len(logical_metadata_docs),
            "totalMedicalPages": med_pages,
            "readableMedicalPages": med_readable,
            "qualityScore": quality_score,
            "duplicateCount": len(duplicate_ids),
            "duplicateDocumentIds": list(duplicate_ids),
            "unreadableCount": len(unreadable_docs),
            "corruptedCount": len(corrupted_docs),
            "mismatchCount": len(mismatch_docs),
            "actionRequiredCount": len(action_required_docs),
            "processingComplete": not bool(pending),
        }

        quality_rate = round(readable_pages * 100 / max(1, page_count)) if page_count else 100
        timeline = sorted(activities[esr_id], key=lambda event: event["at"], reverse=True)[:10]

        vet = state["veteransById"].get(esr.get("veteranId"), {})
        contentions_list = [state["contentionsById"][cid] for cid in esr.get("contentionIds", []) if cid in state["contentionsById"]]
        requested_dbqs = []
        for c in contentions_list:
            requested_dbqs.extend(c.get("requestedDbqNames", []))
        if not requested_dbqs and contentions_list:
            requested_dbqs = [c["title"] for c in contentions_list]

        is_simulated = esr.get("isSimulated")
        if is_simulated is None:
            is_simulated = bool(
                esr_id.startswith("esr-local")
                or "syn" in esr_id.lower()
                or "example" in (vet.get("displayName") or "").lower()
                or "demo" in (vet.get("displayName") or "").lower()
            )

        va_instructions = {
            "specialInstructions": esr.get("specialInstructions") or "",
            "serviceBranch": vet.get("serviceGroup") or "",
            "claimantName": vet.get("displayName") or esr.get("claimantName") or "",
            "claimIdentifier": vet.get("claimIdentifier") or esr.get("sourceReference") or esr_id,
            "benefitProgram": esr.get("examManagement", {}).get("program") or "",
            "priorityHandling": (esr.get("priority") or "routine").upper(),
            "requestedDbqs": list(dict.fromkeys(requested_dbqs)),
            "contentionCount": len(contentions_list),
            "contentions": [
                {
                    "title": c["title"],
                    "status": c.get("status", "awaiting-review"),
                    "dbqs": c.get("requestedDbqNames", []),
                }
                for c in contentions_list
            ],
            "expedited": esr.get("priority") in {"expedited", "critical", "terminal"},
            "isSimulated": is_simulated,
        }

        views[esr_id] = {
            "documentIds": [item["id"] for item in documents],
            "exceptionIds": [issue["id"] for issue in issues],
            "activityIds": [event["id"] for event in timeline],
            "openExceptions": open_count, "escalatedExceptions": escalated_count,
            "pageCount": page_count, "readablePages": readable_pages,
            "unreadablePages": unreadable_pages,
            "progress": round(readable_pages * 100 / page_count) if page_count else 0,
            "manifestStatus": manifest_status,
            "manifestSummary": manifest_summary,
            "expectedCount": len(expected_docs) if manifest else len(documents),
            "receivedCount": len(documents),
            "missingDocuments": missing_docs,
            "categorizedCounts": dict(cat_counts),
            "aiOrganizer": ai_organizer,
            "vaInstructions": va_instructions,
            "qualityRate": quality_rate,
            "receiptReady": receipt_ready, "canAttach": receipt_ready,
            "canProcessLegacy": receipt_ready and any(not item.get("processing") for item in pending),
            "canValidate": blocker is None, "blocker": blocker,
            "needsReadableEvidence": blocker == READABLE_EVIDENCE_BLOCKER,
            "queueLabel": ("Ready to validate" if blocker is None else "Needs readable evidence"
                           if blocker == READABLE_EVIDENCE_BLOCKER else esr["intakeStatus"].replace("-", " ")),
            "steps": 1 if not receipt_ready else 2 if pending or not documents else 3 if open_count else 2 if not evidence_ready else 4,
        }
    public_documents = {key: public_document(doc) for key, doc in state["evidenceDocumentsById"].items()}
    public_exceptions = {key: {**issue, "detail": public_processing_message(issue.get("detail") or issue.get("message") or "")}
                         for key, issue in state["intakeExceptionsById"].items()}
    return {**state, "veteransById": veterans, "evidenceDocumentsById": public_documents, "intakeExceptionsById": public_exceptions,
            "intake": {"queueIds": queue_order, "viewsByEsrId": views}}
