import time
import unittest

from fastapi import HTTPException

from app.services.clinical_nlp import (
    detect_clinical_contradictions,
    extract_facts_for_document,
    search_relevant_evidence,
)
from app.services.intake_store import add_case, apply_intake_command, refresh_intake_work, timestamp
from app.services.triage import apply_triage_command
from app.services.triage_pipeline import run_triage_pipeline_for_state
from app.services.va_rules import detect_claim_programs
from app.services.workspace_store import empty_workspace


class Stage2GeneralizationTests(unittest.TestCase):
    def test_unseen_conditions_abbreviations_and_excerpt_precision_recall(self):
        """Measure retrieval precision/recall on unseen conditions (GERD, OSA, RA) mixed on multi-topic pages."""
        docs = [
            {
                "id": "doc-mixed-1",
                "title": "GI & Pulmonary Clinic Note",
                "category": "va-medical-records",
                "classificationStatus": "indexed",
                "pageCount": 2,
                "classifiedPageCount": 2,
                "unreadablePageCount": 0,
                "processing": {
                    "allPages": [
                        {
                            "page": 1,
                            "text": (
                                "03/10/2024 Assessment: GERD (K21.9) with daily heartburn and reflux; prescribed omeprazole.\n"
                                "03/10/2024 Unrelated dermatology note: mild dry skin on forearms."
                            ),
                        },
                        {
                            "page": 2,
                            "text": (
                                "04/12/2024 Pulmonary: OSA confirmed on sleep study, compliant with CPAP nightly.\n"
                                "04/12/2024 Dental checkup: routine cleaning, no cavities."
                            ),
                        },
                    ]
                },
            }
        ]
        extracted = extract_facts_for_document("esr-unseen", docs[0])
        facts = extracted["facts"]
        self.assertTrue(any(any(c["code"] == "K21.9" for c in f["codes"]) for f in facts))
        self.assertTrue(any("omeprazole" in (f.get("treatments") or []) for f in facts))

        gerd_contention = {"id": "c-gerd", "title": "Gastroesophageal Reflux Disease (GERD)", "requestedDbqNames": []}
        res = search_relevant_evidence("esr-unseen", gerd_contention, docs, facts)
        self.assertFalse(res["usedFallback"])
        self.assertEqual(len(res["matchedPages"]), 1)
        self.assertEqual(res["matchedPages"][0]["page"], 1)
        # Focused excerpt must exclude unrelated skin sentence and include GERD sentence (precision=1.0, recall=1.0)
        self.assertIn("GERD", res["matchedPages"][0]["excerpt"])
        self.assertNotIn("dermatology", res["matchedPages"][0]["excerpt"].lower())

        # Unseen GERD condition routes to dynamic/unverified review and requires unverifiedRuleAcknowledged
        state = empty_workspace()
        now = timestamp()
        esr = add_case(state, "Taylor Unseen", ["Gastroesophageal Reflux Disease (GERD)"], "staff", now)
        esr_id = esr["id"]
        c_id = esr["contentionIds"][0]
        state["evidenceDocumentsById"]["doc-1"] = {**docs[0], "id": "doc-1", "esrId": esr_id, "contentionIds": [c_id]}
        esr["evidenceDocumentIds"] = ["doc-1"]
        state["contentionsById"][c_id]["evidenceDocumentIds"] = ["doc-1"]
        refresh_intake_work(state, esr_id, now)
        apply_intake_command(state, "validateIntake", [esr_id], "staff")
        dossier = run_triage_pipeline_for_state(state, esr_id, allow_llm=False, force=True)
        self.assertEqual(dossier["contentionsById"][c_id]["rulesEval"]["ruleVerificationStatus"], "unverified-requires-review")

        with self.assertRaisesRegex(HTTPException, "Verified applicable VA rules"):
            apply_triage_command(
                state,
                "approveTriagePlan",
                [c_id, {
                    "examCode": "dynamic",
                    "modality": "in-person",
                    "dbqs": ["Gastroesophageal Reflux Disease (GERD) DBQ"],
                    "diagnostics": [],
                    "rationale": "Attempt approval without verified VA rules.",
                }],
                "clinician",
            )

        # Unverified conditions route safely to VA clarification
        apply_triage_command(
            state,
            "requestTriageClarification",
            [c_id, "Request VA clarification on applicable DBQ and examiner qualification rules for GERD."],
            "clinician",
        )
        self.assertEqual(state["contentionsById"][c_id]["status"], "clarification-required")

    def test_context_aware_contradictions_and_distress_temporality(self):
        doc = {
            "id": "doc-ctx",
            "title": "Ortho & Psych Progress Note",
            "category": "va-medical-records",
            "classificationStatus": "indexed",
            "processing": {
                "allPages": [
                    {
                        "page": 1,
                        "text": (
                            "05/01/2024 Left knee pain and instability.\n"
                            "05/01/2024 Patient denies right knee pain or instability.\n"
                            "02/01/2019 History of left knee swelling, now resolved.\n"
                            "05/01/2024 Patient denies suicidal ideation."
                        ),
                    },
                    {
                        "page": 2,
                        "text": (
                            "03/01/2016 Historical note: past suicidal ideation in 2016.\n"
                            "05/01/2024 Mental health: patient endorsed active suicidal ideation today."
                        ),
                    },
                ]
            },
        }
        extracted = extract_facts_for_document("esr-ctx", doc)
        # Opposite laterality (left vs right) and resolved historical findings must NOT trigger false contradictions
        conflicts = detect_clinical_contradictions(extracted["facts"])
        self.assertEqual(conflicts, [])

        # Negated distress must NOT trigger alert; historical vs current must be distinguished
        alerts = extracted["distressAlerts"]
        self.assertEqual(len(alerts), 2)
        self.assertEqual(alerts[0]["temporality"], "historical")
        self.assertEqual(alerts[1]["temporality"], "current")
        self.assertEqual(alerts[1]["severity"], "urgent")

    def test_bva_structured_override_bdd_rad_date_and_tera_keyword_guard(self):
        # 1. TERA keyword in contention without authoritative ESR flag must NOT confirm TERA
        flags_kw = detect_claim_programs(
            {"id": "e1", "specialInstructions": ""},
            {},
            [{"title": "Asthma secondary to burn pit exposure", "requestedDbqNames": []}],
        )
        self.assertFalse(flags_kw["teraConfirmed"])
        self.assertTrue(flags_kw["teraMentionedUnconfirmed"])

        # 2. BDD/IDES with future RAD date selects SHA; missing RAD date requires review
        flags_bdd_missing = detect_claim_programs({"id": "e2", "schedulingProgram": "BDD"}, {}, [])
        self.assertEqual(flags_bdd_missing["radStatus"], "missing")
        flags_bdd_future = detect_claim_programs(
            {"id": "e3", "schedulingProgram": "BDD", "releaseFromActiveDutyDate": "2099-01-01"}, {}, []
        )
        self.assertEqual(flags_bdd_future["radStatus"], "pre-discharge")
        self.assertEqual(flags_bdd_future["bddRecommendedAssessment"], "Separation Health Assessment (SHA) DBQ")

        # 3. BVA Remand rejects free-text "remand" keyword bypass and requires structured authorization
        state = empty_workspace()
        now = timestamp()
        esr = add_case(state, "Morgan BVA", ["Left Knee Condition"], "staff", now)
        esr_id = esr["id"]
        esr["specialInstructions"] = "BVA Remand order."
        c_id = esr["contentionIds"][0]
        state["contentionsById"][c_id]["requestedDbqNames"] = ["Knee and Lower Leg", "Medical Opinion"]
        state["evidenceDocumentsById"]["doc-bva"] = {
            "id": "doc-bva", "esrId": esr_id, "title": "STR", "category": "service-treatment",
            "classificationStatus": "indexed", "pageCount": 1, "classifiedPageCount": 1, "unreadablePageCount": 0,
            "contentionIds": [c_id],
            "processing": {"allPages": [{"page": 1, "text": "05/01/2024 Left knee pain."}]},
        }
        esr["evidenceDocumentIds"] = ["doc-bva"]
        state["contentionsById"][c_id]["evidenceDocumentIds"] = ["doc-bva"]
        refresh_intake_work(state, esr_id, now)
        apply_intake_command(state, "validateIntake", [esr_id], "staff")

        with self.assertRaisesRegex(HTTPException, "structured scope-change authorization"):
            apply_triage_command(
                state,
                "approveTriagePlan",
                [c_id, {
                    "examCode": "knee",
                    "modality": "in-person",
                    "dbqs": ["Knee and Lower Leg Conditions DBQ"],
                    "diagnostics": [],
                    "rationale": "Dropping remand medical opinion via keyword only.",
                }],
                "clinician",
            )

    def test_large_efolder_incremental_processing_and_cross_patient_isolation(self):
        state = empty_workspace()
        now = timestamp()
        esr_a = add_case(state, "Patient Alpha", ["Back Pain"], "staff", now)
        esr_b = add_case(state, "Patient Beta", ["Hearing Loss"], "staff", now)

        pages_40 = [{"page": i, "text": f"01/10/2024 Lumbar spine pain follow-up page {i}."} for i in range(1, 41)]
        state["evidenceDocumentsById"]["doc-a-40"] = {
            "id": "doc-a-40", "esrId": esr_a["id"], "title": "Alpha Large STR", "category": "service-treatment",
            "classificationStatus": "indexed", "pageCount": 40, "classifiedPageCount": 40,
            "sourceHash": "alpha-40", "version": 1, "contentionIds": esr_a["contentionIds"],
            "processing": {"allPages": pages_40},
        }
        esr_a["evidenceDocumentIds"] = ["doc-a-40"]
        state["contentionsById"][esr_a["contentionIds"][0]]["evidenceDocumentIds"] = ["doc-a-40"]

        state["evidenceDocumentsById"]["doc-b-1"] = {
            "id": "doc-b-1", "esrId": esr_b["id"], "title": "Beta Audiogram", "category": "diagnostic-tests",
            "classificationStatus": "indexed", "pageCount": 1, "classifiedPageCount": 1,
            "sourceHash": "beta-1", "version": 1, "contentionIds": esr_b["contentionIds"],
            "processing": {"allPages": [{"page": 1, "text": "02/02/2024 Bilateral hearing loss 45 dB."}]},
        }
        esr_b["evidenceDocumentIds"] = ["doc-b-1"]
        state["contentionsById"][esr_b["contentionIds"][0]]["evidenceDocumentIds"] = ["doc-b-1"]

        t0 = time.perf_counter()
        dossier_a = run_triage_pipeline_for_state(state, esr_a["id"], allow_llm=False, force=True)
        elapsed_first = time.perf_counter() - t0
        dossier_b = run_triage_pipeline_for_state(state, esr_b["id"], allow_llm=False, force=True)

        self.assertLess(elapsed_first, 2.0)
        self.assertIn("doc-a-40", dossier_a["documentFactCache"])
        # Cross-patient isolation: Alpha's dossier must never reference Beta's document
        alpha_doc_ids = {m["documentId"] for m in dossier_a["contentionsById"][esr_a["contentionIds"][0]]["matchedEvidence"]}
        self.assertEqual(alpha_doc_ids, {"doc-a-40"})
        beta_doc_ids = {m["documentId"] for m in dossier_b["contentionsById"][esr_b["contentionIds"][0]]["matchedEvidence"]}
        self.assertEqual(beta_doc_ids, {"doc-b-1"})

    def test_missing_historical_records_production_rule_gate_and_digest_page_boundaries(self):
        from app.services.triage import compute_contention_evidence_digest

        state = empty_workspace()
        now = timestamp()
        esr = add_case(state, "Sam Baseline", ["Unmapped Rare Condition"], "staff", now)
        esr_id = esr["id"]
        c_id = esr["contentionIds"][0]
        state["evidenceDocumentsById"]["doc-empty-hist"] = {
            "id": "doc-empty-hist", "esrId": esr_id, "title": "Blank Referral Cover",
            "category": "service-treatment", "classificationStatus": "needs-human-review",
            "pageCount": 1, "classifiedPageCount": 0, "unreadablePageCount": 1,
            "sourceHash": "h1", "version": 1,
            "sections": [{"sectionId": "s1", "startPage": 1, "endPage": 1, "status": "needs-human-review"}],
            "contentionIds": [c_id],
        }
        esr["evidenceDocumentIds"] = ["doc-empty-hist"]
        state["contentionsById"][c_id]["evidenceDocumentIds"] = ["doc-empty-hist"]
        esr["intakeStatus"] = "ready-for-triage"
        refresh_intake_work(state, esr_id, now)

        dossier = run_triage_pipeline_for_state(state, esr_id, allow_llm=False, force=True)
        rules_eval = dossier["contentionsById"][c_id]["rulesEval"]
        self.assertIn("pathwayAssessment", rules_eval)
        self.assertTrue(rules_eval["pathwayAssessment"]["aiRecommendation"]["advisoryOnly"])
        self.assertTrue(any(g["category"] == "missing-historical-evidence" and g["gapGroup"] == "collect-during-exam" for g in rules_eval["evidenceGaps"]))

        # Digest must change when section page boundaries change
        d1 = compute_contention_evidence_digest(state, state["contentionsById"][c_id])
        state["evidenceDocumentsById"]["doc-empty-hist"]["sections"][0]["endPage"] = 2
        d2 = compute_contention_evidence_digest(state, state["contentionsById"][c_id])
        self.assertNotEqual(d1, d2)

        plan_payload = {
            "examCode": "dynamic",
            "modality": "in-person",
            "dbqs": ["Unmapped Rare Condition DBQ"],
            "diagnostics": [],
            "rationale": "In-person exam intended to collect missing baseline findings.",
        }
        # Production-only standard blocks unverified rule approval and routes to VA clarification
        with self.assertRaisesRegex(HTTPException, "Verified applicable VA rules"):
            apply_triage_command(state, "approveTriagePlan", [c_id, plan_payload], "clinician")


if __name__ == "__main__":
    unittest.main()
