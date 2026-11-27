"""Unit tests for Stage 5 Internal QA deterministic VA contract rules, privacy quarantine, separation of duties, and guarded AI audit."""

from __future__ import annotations

import json
import os
import sys
import unittest
from pathlib import Path
from unittest.mock import patch

from fastapi import HTTPException

BACKEND_ROOT = Path(__file__).resolve().parents[1]
if str(BACKEND_ROOT) not in sys.path:
    sys.path.insert(0, str(BACKEND_ROOT))

os.environ.setdefault("APP_ENV", "dev")

from app.services.qa_pipeline import run_guarded_qa_audit_llm, run_qa_audit_for_state
from app.services.qa_rules import check_independent_qa_reviewer, run_deterministic_qa_rules


def _sample_qa_state(
    *,
    license_status: str = "active",
    training_status: str = "active",
    remarks: str = "Standard in-person exam completed.",
    rationale: str = "Clinical findings align with documented history and functional limitations.",
    include_raw_psych: bool = False,
    unreviewed_abnormal: bool = False,
    urgent_unescalated: bool = False,
    modality: str = "in-person",
    findings_text: str = "Left knee flexion limited to 110 degrees with painful motion.",
) -> dict:
    doc_pages = [
        {"page": 1, "text": "2023-05-15 Left knee pain after lifting injury; flexion 112 degrees noted."},
        {"page": 2, "text": "2026-06-02 SHA Part A: persistent left knee pain limiting stairs and prolonged standing."},
    ]
    orders = {}
    results = {}
    order_ids = []
    if include_raw_psych or unreviewed_abnormal:
        order_ids.append("ord-1")
        result_ids = []
        if include_raw_psych:
            result_ids.append("res-psych")
            results["res-psych"] = {
                "id": "res-psych",
                "esrId": "esr-1",
                "orderId": "ord-1",
                "examinationId": "exam-1",
                "summary": "Attached MMPI-2 raw answer sheet and psychotherapy notes.",
                "sourceReference": "MMPI2_raw_psych_protocol.pdf",
                "reviewedAt": "2026-11-26T10:00:00Z",
            }
        if unreviewed_abnormal:
            result_ids.append("res-abn")
            results["res-abn"] = {
                "id": "res-abn",
                "esrId": "esr-1",
                "orderId": "ord-1",
                "examinationId": "exam-1",
                "summary": "MRI shows severe meniscal tear.",
                "sourceReference": "Knee_MRI_Report.pdf",
                "reportedFlag": "abnormal",
            }
        orders["ord-1"] = {
            "id": "ord-1",
            "esrId": "esr-1",
            "examinationId": "exam-1",
            "requirementId": "req-1",
            "orderType": "Diagnostic Review",
            "status": "reviewed" if not unreviewed_abnormal else "result-available",
            "required": True,
            "resultIds": result_ids,
        }

    distress_alerts = []
    if urgent_unescalated:
        distress_alerts.append({
            "id": "distress-1",
            "severity": "urgent",
            "escalationStatus": "pending-escalation",
            "excerpt": "Active safety alert noted.",
        })

    return {
        "revision": 1,
        "esrsById": {"esr-1": {"id": "esr-1", "veteranId": "vet-1", "priority": "routine"}},
        "veteransById": {"vet-1": {"id": "vet-1", "displayName": "Taylor Q Mock"}},
        "contentionsById": {
            "c-1": {
                "id": "c-1",
                "esrId": "esr-1",
                "title": "Left knee pain",
                "evidenceDocumentIds": ["doc-1"],
            }
        },
        "evidenceDocumentsById": {
            "doc-1": {
                "id": "doc-1",
                "esrId": "esr-1",
                "title": "VA Medical Records",
                "sourceReference": "VA_Records.pdf",
                "classifiedPageCount": 2,
                "classificationStatus": "indexed",
                "processing": {"allPages": doc_pages},
            }
        },
        "examinationRequirementsById": {
            "req-1": {"id": "req-1", "esrId": "esr-1", "selectedModality": modality}
        },
        "examinationsById": {
            "exam-1": {
                "id": "exam-1",
                "esrId": "esr-1",
                "requirementIds": ["req-1"],
                "contentionIds": ["c-1"],
                "examinerId": "exm-1",
                "appointmentIds": ["appt-1"],
                "diagnosticOrderIds": order_ids,
            }
        },
        "appointmentsById": {
            "appt-1": {
                "id": "appt-1",
                "esrId": "esr-1",
                "examinationId": "exam-1",
                "examinerId": "exm-1",
                "facilityId": "fac-1",
                "startsAt": "2026-11-26T09:00:00Z",
                "modality": modality,
                "status": "completed",
            }
        },
        "facilitiesById": {"fac-1": {"id": "fac-1", "stateCode": "OH"}},
        "examinersById": {
            "exm-1": {
                "id": "exm-1",
                "doctorName": "Dr. Jessica Green, MD",
                "credentials": ["MD", "C&P examination training"],
                "licenses": [
                    {
                        "licenseState": "OH",
                        "licenseStatus": license_status,
                        "licenseExpiryDate": "2028-12-31" if license_status == "active" else "2025-01-01",
                    }
                ],
                "licenseStatus": license_status,
                "licenseExpiryDate": "2028-12-31" if license_status == "active" else "2025-01-01",
                "certifications": [
                    {
                        "certificationName": "C&P examination training",
                        "status": training_status,
                        "expiresAt": "2028-12-31" if training_status == "active" else "2025-01-01",
                    }
                ],
            }
        },
        "diagnosticOrdersById": orders,
        "diagnosticResultsById": results,
        "triageDossiersByEsrId": {
            "esr-1": {
                "caseOverview": "History of chronic left knee pain with limited flexion.",
                "distressAlerts": distress_alerts,
                "preparedAt": "2026-11-26T08:00:00Z",
                "packetsByContentionId": {
                    "c-1": {
                        "matchedEvidence": [
                            {"documentId": "doc-1", "documentTitle": "VA Medical Records", "page": 1, "excerpt": doc_pages[0]["text"]}
                        ],
                        "rulesEval": {
                            "evidenceGaps": [
                                {"gapGroup": "collect-during-exam", "category": "stale-measurements", "message": "Historical ROM may be too old."}
                            ]
                        },
                    }
                },
            }
        },
        "clinicalFactsById": {},
        "dbqsById": {
            "dbq-1": {
                "id": "dbq-1",
                "esrId": "esr-1",
                "examinationId": "exam-1",
                "contentionIds": ["c-1"],
                "formType": "Knee and Lower Leg Conditions DBQ",
                "formVersion": "synthetic-v2",
                "currentVersionId": "ver-1",
                "signedVersionId": "ver-1",
                "localReviewState": "review-recorded",
                "localAttestation": {
                    "versionId": "ver-1",
                    "staffActorId": "user-clinician-1",
                    "examinerId": "exm-1",
                    "examinerName": "Dr. Jessica Green, MD",
                    "credentials": ["MD"],
                    "at": "2026-11-26T11:00:00Z",
                },
            }
        },
        "dbqVersionsById": {
            "ver-1": {
                "id": "ver-1",
                "dbqId": "dbq-1",
                "versionNumber": 1,
                "formVersion": "synthetic-v2",
                "values": {
                    "history": "Chronic left knee pain since 2023 injury.",
                    "findings": findings_text,
                    "rationale": rationale,
                    "recordsReviewed": "VA_Records.pdf pp. 1-2",
                    "diagnosisOutcome": "Established",
                    "diagnosisDetails": "Left knee patellofemoral syndrome (M25.562)",
                    "remarks": remarks,
                },
            }
        },
        "qaReviewsById": {
            "qa-1": {
                "id": "qa-1",
                "version": 1,
                "createdAt": "2026-11-26T11:05:00Z",
                "updatedAt": "2026-11-26T11:05:00Z",
                "esrId": "esr-1",
                "dbqId": "dbq-1",
                "versionId": "ver-1",
                "reviewerId": "user-qa",
                "findingIds": [],
                "status": "queued",
                "reviewerComment": "",
            }
        },
        "qaFindingsById": {},
        "activitiesById": {},
        "workItemsById": {},
    }


class Stage5QATests(unittest.TestCase):
    def test_clean_report_passes_all_10_contract_rules(self) -> None:
        state = _sample_qa_state()
        res = run_deterministic_qa_rules(None, state, "qa-1")
        self.assertEqual(len(res["checks"]), 12)
        self.assertEqual(res["failedCount"], 0)
        self.assertEqual(res["reviewNeededCount"], 0)
        self.assertTrue(all(c["status"] in {"passed", "not-applicable"} for c in res["checks"]))
        self.assertEqual(len(state["qaFindingsById"]), 0)

    def test_expired_doctor_license_and_training_create_blocking_finding(self) -> None:
        state = _sample_qa_state(license_status="expired", training_status="expired")
        res = run_deterministic_qa_rules(None, state, "qa-1")
        doc_check = next(c for c in res["checks"] if c["key"] == "doctor-credentials")
        self.assertEqual(doc_check["status"], "failed")
        findings = list(state["qaFindingsById"].values())
        self.assertTrue(any(f["checkKey"] == "doctor-credentials" and f["severity"] == "blocking" and f["origin"] == "deterministic" for f in findings))

    def test_forbidden_rating_and_treatment_phrases_are_blocked(self) -> None:
        state = _sample_qa_state(
            rationale="Veteran deserves a 30% disability rating and claim will win service connection. Patient should take ibuprofen 800mg daily.",
        )
        res = run_deterministic_qa_rules(None, state, "qa-1")
        fw_check = next(c for c in res["checks"] if c["key"] == "forbidden-words")
        self.assertEqual(fw_check["status"], "failed")
        self.assertIn("rating", fw_check["detail"].lower())
        self.assertIn("treatment", fw_check["detail"].lower())

    def test_raw_psych_sheet_is_quarantined_locally_for_3_years(self) -> None:
        state = _sample_qa_state(include_raw_psych=True)
        res = run_deterministic_qa_rules(None, state, "qa-1")
        self.assertEqual(len(res["quarantinedPrivacyFiles"]), 1)
        psych_res = state["diagnosticResultsById"]["res-psych"]
        self.assertTrue(psych_res["containsRestrictedRawMaterial"])
        self.assertTrue(psych_res["privacyQuarantined"])
        self.assertEqual(psych_res["quarantineRetentionYears"], 3)

    def test_unreviewed_abnormal_lab_and_unescalated_distress_and_ace_remarks_fail(self) -> None:
        state = _sample_qa_state(
            unreviewed_abnormal=True,
            urgent_unescalated=True,
            modality="ace-records-review",
            findings_text="Physical exam skipped.",
            remarks="",
        )
        res = run_deterministic_qa_rules(None, state, "qa-1")
        by_key = {c["key"]: c["status"] for c in res["checks"]}
        self.assertEqual(by_key["labs-safety-24h"], "failed")
        self.assertEqual(by_key["ace-remarks"], "failed")

    def test_separation_of_duties_blocks_signing_clinician_self_review(self) -> None:
        state = _sample_qa_state()
        review = state["qaReviewsById"]["qa-1"]
        with self.assertRaises(HTTPException) as ctx:
            check_independent_qa_reviewer(state, review, "user-clinician-1", "qa")
        self.assertEqual(ctx.exception.status_code, 403)
        # Independent QA reviewer or admin succeeds cleanly
        check_independent_qa_reviewer(state, review, "user-qa-2", "qa")

    def test_guarded_ai_audit_repairs_fabricated_citations_and_never_creates_blockers(self) -> None:
        state = _sample_qa_state(findings_text="Normal joint exam, though claimant describes severe pain on stairs.")
        fake_first = json.dumps({
            "summary": "Possible contradiction between normal exam and severe pain.",
            "warnings": [{
                "title": "Internal finding contradiction",
                "sectionReference": "Examination findings",
                "issueCategory": "evidence-consistency",
                "severity": "review",
                "rationale": "Normal exam phrase conflicts with severe pain.",
                "sourceIds": ["S99"],
            }],
        })
        repaired_second = json.dumps({
            "summary": "Possible contradiction between normal exam and severe pain.",
            "warnings": [{
                "title": "Internal finding contradiction",
                "sectionReference": "Examination findings",
                "issueCategory": "evidence-consistency",
                "severity": "advisory",
                "rationale": "Normal exam phrase conflicts with severe pain.",
                "sourceIds": ["S1"],
            }],
        })
        with patch("app.services.qa_pipeline.settings.intake_provider", "azure-openai"), \
             patch("app.services.qa_pipeline.azure_review_completion", side_effect=[fake_first, repaired_second]), \
             patch("app.services.qa_pipeline.get_last_completion_provider", return_value="azure-openai"), \
             patch("app.services.qa_pipeline.get_last_completion_audit", return_value={"fallbackUsed": False}):
            out = run_qa_audit_for_state(None, state, "qa-1", allow_llm=True, force=True)

        self.assertEqual(len(out["checks"]), 12)
        ai_findings = [f for f in state["qaFindingsById"].values() if f.get("origin") == "ai-audit"]
        self.assertEqual(len(ai_findings), 1)
        self.assertIn(ai_findings[0]["severity"], ("review", "advisory"))
        self.assertNotEqual(ai_findings[0]["severity"], "blocking")
        self.assertEqual(ai_findings[0]["citedSources"][0]["sourceId"], "S1")


if __name__ == "__main__":
    unittest.main()
