import unittest

from fastapi import HTTPException

from app.services.clinical_nlp import extract_facts_for_document, search_relevant_evidence
from app.services.intake_store import add_case, apply_intake_command, refresh_intake_work, timestamp
from app.services.triage import apply_triage_command, with_triage_view
from app.services.triage_pipeline import run_triage_pipeline_for_state
from app.services.va_rules import analyze_evidence_gaps_and_pathway, detect_claim_programs, resolve_exam_suggestion
from app.services.workspace_store import empty_workspace


class Stage2PipelineTests(unittest.TestCase):
    def test_medical_nlp_extracts_rom_dates_codes_negation_and_distress(self):
        doc = {
            "id": "doc-sf600",
            "title": "SF 600 Service Treatment Record",
            "category": "service-treatment",
            "classificationStatus": "indexed",
            "pageCount": 2,
            "classifiedPageCount": 2,
            "processing": {
                "allPages": [
                    {
                        "page": 1,
                        "text": (
                            "06/15/2013: Left knee pain after shipboard ladder drill. Dx: M25.562.\n"
                            "Measured left knee flexion 120 deg, extension 0 degrees.\n"
                            "Patient denies right knee pain or instability."
                        ),
                    },
                    {
                        "page": 2,
                        "text": (
                            "09/12/2014: Bilateral hearing loss after ship machinery noise exposure. ICD-10 H90.3.\n"
                            "Mental health note: patient endorsed suicidal ideation and wants help."
                        ),
                    },
                ]
            },
        }
        extracted = extract_facts_for_document("esr-1", doc)
        facts = extracted["facts"]
        self.assertGreaterEqual(len(facts), 3)

        left_knee_facts = [f for f in facts if f["bodyPart"] == "knee" and not f["isNegated"]]
        self.assertTrue(any(f["laterality"] == "left" and f["clinicalDate"] == "2013-06-15" for f in left_knee_facts))
        self.assertTrue(any(f["measurements"].get("flexion") == 120 and f["measurements"].get("extension") == 0 for f in left_knee_facts))
        self.assertTrue(any(any(c["code"] == "M25.562" for c in f["codes"]) for f in left_knee_facts))

        negated_facts = [f for f in facts if f["isNegated"]]
        self.assertEqual(len(negated_facts), 1)
        self.assertEqual(negated_facts[0]["assertion"], "negated")

        alerts = extracted["distressAlerts"]
        self.assertEqual(len(alerts), 1)
        self.assertEqual(alerts[0]["pageNumber"], 2)

    def test_transparent_search_returns_all_matching_pages_and_excludes_removed_docs(self):
        docs = [
            {
                "id": "doc-1",
                "title": "Clinic Note",
                "category": "service-treatment",
                "classificationStatus": "indexed",
                "processing": {
                    "allPages": [
                        {"page": 1, "text": "2014-01-10 Left knee arthralgia and meniscus tenderness."},
                        {"page": 2, "text": "2016-05-20 Contact dermatitis rash on hands."},
                        {"page": 3, "text": "2024-05-15 Follow-up knee flexion 115 degrees."},
                    ]
                },
            },
            {
                "id": "doc-excluded",
                "title": "Duplicate Knee Record",
                "category": "service-treatment",
                "classificationStatus": "excluded",
                "isExcluded": True,
                "processing": {"allPages": [{"page": 1, "text": "Left knee duplicate record"}]},
            },
        ]
        contention = {"id": "c-knee", "title": "Left Knee Condition", "requestedDbqNames": ["Knee and Lower Leg"]}
        res = search_relevant_evidence("esr-1", contention, docs, [])
        pages = [m["page"] for m in res["matchedPages"]]
        doc_ids = {m["documentId"] for m in res["matchedPages"]}
        self.assertEqual(pages, [1, 3])
        self.assertEqual(doc_ids, {"doc-1"})

    def test_rfp_rules_bdd_bva_tera_and_missing_audiogram(self):
        esr = {
            "id": "esr-rfp",
            "specialInstructions": "BVA Remand case: BDD Separation Health Assessment and burn pit TERA toxic exposure.",
            "schedulingProgram": "BDD",
        }
        vet = {"city": "APO", "stateCode": "AE"}
        contention = {"id": "c-hear", "title": "Bilateral Hearing Loss", "requestedDbqNames": ["Hearing Loss and Tinnitus"]}
        flags = detect_claim_programs(esr, vet, [contention])
        self.assertTrue(flags["isBddIdes"])
        self.assertTrue(flags["isBvaRemand"])
        self.assertTrue(flags["isTera"])
        self.assertTrue(flags["isInternational"])

        exam_info = resolve_exam_suggestion(contention, [])
        self.assertEqual(exam_info["examCode"], "hearing")
        doc = {"id": "d1", "classificationStatus": "indexed", "classifiedPageCount": 1, "administrativeOnly": False}
        eval_res = analyze_evidence_gaps_and_pathway(contention, exam_info, [{"page": 1}], [], [doc], flags)
        self.assertEqual(eval_res["proposedPathway"], "in-person-candidate")
        self.assertIn("Audiology test", eval_res["suggestedDiagnostics"])
        self.assertTrue(any(g["category"] == "missing-diagnostics" for g in eval_res["evidenceGaps"]))

    def test_full_case_dossier_and_clinician_ace_authorization_flow(self):
        state = empty_workspace()
        now = timestamp()
        esr = add_case(state, "Riley Q. Example", ["Left Knee Condition", "Migraine Headaches", "Contact Dermatitis"], "staff", now)
        esr_id = esr["id"]
        c_knee, c_migraine, c_skin = esr["contentionIds"]
        state["contentionsById"][c_knee]["requestedDbqNames"] = ["Knee and Lower Leg"]

        doc_id = "doc-riley-1"
        state["evidenceDocumentsById"][doc_id] = {
            "id": doc_id,
            "esrId": esr_id,
            "title": "SF 600 & Prior DBQs",
            "sourceReference": "sf600.pdf",
            "category": "service-treatment",
            "pageCount": 3,
            "classifiedPageCount": 3,
            "unreadablePageCount": 0,
            "classificationStatus": "indexed",
            "contentionIds": list(esr["contentionIds"]),
            "processing": {
                "allPages": [
                    {"page": 1, "text": "06/15/2013 Left knee pain after ladder drill. Flexion 120°, extension 0°."},
                    {"page": 2, "text": "08/10/2023 Prostrating migraine headaches occurring twice monthly, responsive to sumatriptan."},
                    {"page": 3, "text": "01/04/2020 Historical contact dermatitis flare on hands."},
                ]
            },
        }
        esr["evidenceDocumentIds"] = [doc_id]
        for cid in esr["contentionIds"]:
            state["contentionsById"][cid]["evidenceDocumentIds"] = [doc_id]
        refresh_intake_work(state, esr_id, now)

        apply_intake_command(state, "validateIntake", [esr_id], "staff")
        self.assertEqual(esr["triageProcessing"]["status"], "queued")

        dossier = run_triage_pipeline_for_state(state, esr_id, allow_llm=False)
        self.assertIsNotNone(dossier)
        self.assertEqual(dossier["contentionsById"][c_knee]["extractedMetrics"][0]["value"], "120°")
        self.assertEqual(dossier["contentionsById"][c_knee]["rulesEval"]["proposedPathway"], "in-person-candidate")
        self.assertEqual(dossier["contentionsById"][c_skin]["rulesEval"]["proposedPathway"], "requires-clinical-review")
        self.assertEqual(dossier["contentionsById"][c_migraine]["rulesEval"]["proposedPathway"], "ace-candidate")

        view = with_triage_view(state)["triage"]
        case_view = view["casesByEsrId"][esr_id]
        self.assertEqual(case_view["requestedCount"], 3)
        self.assertEqual(case_view["approvedCount"], 0)

        # Authorizing ACE explicitly as clinician enables ace-records-review
        apply_triage_command(state, "authorizeTriageAce", [c_migraine, "Evidence cohesive; clinician approves ACE eligibility."], "clinician")
        updated_view = with_triage_view(state)["triage"]
        migraine_row = next(r for r in updated_view["rows"] if r["contentionId"] == c_migraine)
        self.assertIn("ace-records-review", migraine_row["pathwaysByExam"]["migraine"])

        # Approve Left Knee contention
        apply_triage_command(
            state,
            "approveTriagePlan",
            [c_knee, {
                "examCode": "knee",
                "modality": "in-person",
                "dbqs": ["Knee and Lower Leg Conditions DBQ"],
                "diagnostics": ["Imaging report review"],
                "rationale": "In-person exam confirmed for current goniometer ROM measurements.",
            }],
            "clinician",
        )
        after_approve = with_triage_view(state)["triage"]["casesByEsrId"][esr_id]
        self.assertEqual(after_approve["approvedCount"], 1)
        self.assertEqual(after_approve["pendingCount"], 2)

    def test_gap_groups_unverified_rules_esr_pathway_and_stale_evidence_invalidation(self):
        state = empty_workspace()
        now = timestamp()
        esr = add_case(state, "Jordan R. Test", ["Left Knee Condition", "Unmapped Rare Syndrome"], "staff", now)
        esr_id = esr["id"]
        esr["specialInstructions"] = "In-person exam required per VA instructions."
        c_knee, c_rare = esr["contentionIds"]
        state["contentionsById"][c_knee]["requestedDbqNames"] = ["Knee and Lower Leg"]

        doc_id = "doc-jordan-1"
        state["evidenceDocumentsById"][doc_id] = {
            "id": doc_id,
            "esrId": esr_id,
            "title": "Orthopedic & Nerve Note",
            "sourceReference": "ortho.pdf",
            "category": "service-treatment",
            "pageCount": 1,
            "classifiedPageCount": 1,
            "unreadablePageCount": 0,
            "classificationStatus": "indexed",
            "sourceHash": "hash-v1",
            "version": 1,
            "contentionIds": [c_knee, c_rare],
            "processing": {
                "allPages": [
                    {
                        "page": 1,
                        "text": (
                            "05/01/2024 Left knee pain and tenderness.\n"
                            "Patient denies left knee instability.\n"
                            "Neurology note: radiculopathy and peripheral neuropathy present."
                        ),
                    },
                ]
            },
        }
        esr["evidenceDocumentIds"] = [doc_id]
        for cid in esr["contentionIds"]:
            state["contentionsById"][cid]["evidenceDocumentIds"] = [doc_id]
        refresh_intake_work(state, esr_id, now)
        apply_intake_command(state, "validateIntake", [esr_id], "staff")

        dossier = run_triage_pipeline_for_state(state, esr_id, allow_llm=False, force=True)
        self.assertIn(doc_id, dossier.get("documentFactCache", {}))

        # Knee gaps should separate collect-during-exam (ROM) vs clinical-review-requirement (Peripheral Nerves DBQ)
        knee_eval = dossier["contentionsById"][c_knee]["rulesEval"]
        gap_groups = {g["gapGroup"] for g in knee_eval["evidenceGaps"]}
        self.assertIn("collect-during-exam", gap_groups)
        self.assertIn("clinical-review-requirement", gap_groups)
        self.assertEqual(knee_eval["vaSpecifiedPathway"], "in-person")
        self.assertEqual(knee_eval["permittedModalities"], ["in-person"])

        # Unmapped condition must be marked unverified
        rare_eval = dossier["contentionsById"][c_rare]["rulesEval"]
        self.assertEqual(rare_eval["ruleVerificationStatus"], "unverified-requires-review")

        # Authoritative ESR in-person instruction blocks tele-c-and-p approval
        with self.assertRaisesRegex(HTTPException, "DBQs, pathways"):
            apply_triage_command(
                state,
                "approveTriagePlan",
                [c_knee, {
                    "examCode": "knee",
                    "modality": "tele-c-and-p",
                    "dbqs": ["Knee and Lower Leg Conditions DBQ"],
                    "diagnostics": [],
                    "rationale": "Attempting telehealth despite VA instruction.",
                }],
                "clinician",
            )

        # Approving with Peripheral Nerves DBQ + in-person succeeds and stores approvedEvidenceDigest
        res = apply_triage_command(
            state,
            "approveTriagePlan",
            [c_knee, {
                "examCode": "knee",
                "modality": "in-person",
                "dbqs": ["Knee and Lower Leg Conditions DBQ", "Peripheral Nerves DBQ"],
                "diagnostics": [],
                "rationale": "In-person exam with secondary Peripheral Nerves DBQ for radiculopathy.",
            }],
            "clinician",
        )
        req_id = res["value"]["entityId"]
        self.assertEqual(state["examinationRequirementsById"][req_id]["status"], "approved")
        self.assertTrue(state["examinationRequirementsById"][req_id]["approvedEvidenceDigest"])

        # Revising evidence (e.g., document updated/excluded) invalidates approved plan & cancels Scheduling
        from app.services.triage import invalidate_stale_triage_plans
        state["evidenceDocumentsById"][doc_id]["classificationStatus"] = "excluded"
        state["evidenceDocumentsById"][doc_id]["isExcluded"] = True
        invalidated = invalidate_stale_triage_plans(state, esr_id, timestamp(), "Document excluded after revision")
        self.assertEqual(invalidated, [c_knee])
        self.assertEqual(state["examinationRequirementsById"][req_id]["status"], "superseded-by-evidence-revision")
        self.assertEqual(state["workItemsById"][f"work-scheduling-{req_id}"]["status"], "cancelled")
        self.assertEqual(state["contentionsById"][c_knee]["status"], "awaiting-review")
        self.assertTrue(state["contentionsById"][c_knee]["triageStaleReason"])


if __name__ == "__main__":
    unittest.main()
