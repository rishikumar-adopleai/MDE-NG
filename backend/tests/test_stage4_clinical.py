import unittest

from app.services.clinical_pipeline import (
    _parse_diagnostic_uploads,
    run_clinical_pipeline,
    with_clinical_view,
)


def _sample_state():
    return {
        "veteransById": {
            "vet-1": {"id": "vet-1", "displayName": "Morgan Q Demo", "fullName": "Morgan Q Demo"}
        },
        "esrsById": {
            "esr-1": {
                "id": "esr-1",
                "veteranId": "vet-1",
                "claimantName": "Morgan Q Demo",
                "sourceReference": "TEST-ESR-001",
                "priority": "routine",
            }
        },
        "contentionsById": {
            "cont-1": {"id": "cont-1", "esrId": "esr-1", "title": "Right knee condition"}
        },
        "examinationRequirementsById": {
            "req-1": {"id": "req-1", "esrId": "esr-1", "title": "Knee and Lower Leg"}
        },
        "examinationsById": {
            "exam-1": {
                "id": "exam-1",
                "esrId": "esr-1",
                "requirementIds": ["req-1"],
                "contentionIds": ["cont-1"],
                "requiredDbqIds": ["dbq-1"],
                "diagnosticOrderIds": ["ord-1"],
            }
        },
        "diagnosticOrdersById": {
            "ord-1": {
                "id": "ord-1",
                "esrId": "esr-1",
                "examinationId": "exam-1",
                "orderType": "Imaging report review",
                "status": "ordered",
                "resultIds": [],
            }
        },
        "diagnosticResultsById": {},
        "dbqsById": {
            "dbq-1": {
                "id": "dbq-1",
                "esrId": "esr-1",
                "examinationId": "exam-1",
                "contentionIds": ["cont-1"],
                "formType": "Knee and Lower Leg Conditions DBQ",
                "formVersion": "synthetic-v2",
                "values": {},
                "currentVersionId": "ver-1",
            }
        },
        "workItemsById": {
            "work-clinical-dbq-1": {
                "id": "work-clinical-dbq-1",
                "esrId": "esr-1",
                "entityId": "dbq-1",
                "stage": "Clinical Evaluation",
                "status": "queued",
            }
        },
        "triageDossiersByEsrId": {
            "esr-1": {
                "overallMedicalSummary": "Chronic right knee pain following PT runs.",
                "packetsByContentionId": {
                    "cont-1": {
                        "rulesEval": {
                            "evidenceGaps": [
                                {
                                    "id": "gap-rom",
                                    "gapGroup": "collect-during-exam",
                                    "severity": "warning",
                                    "message": "Current ROM measurement missing.",
                                    "recommendedAction": "Measure knee flexion and extension in degrees.",
                                }
                            ]
                        }
                    }
                },
            }
        },
    }


class Stage4ClinicalPipelineTests(unittest.TestCase):
    def test_condition_1_digital_form_flags_missing_and_scope(self):
        state = _sample_state()
        out = run_clinical_pipeline(
            state,
            "dbq-1",
            mode="digital-form",
            submitted_values={"history": "Right knee pain"},
            allow_llm=False,
        )
        self.assertFalse(out["validation_passed"])
        categories = {a["category"] for a in out["validation_alerts"]}
        self.assertIn("completeness", categories)
        self.assertIn("diagnostic-flag", categories)
        self.assertIsNotNone(out["draft_medical_review"])
        self.assertEqual(out["draft_medical_review"]["status"], "pending-approval")

    def test_condition_2_uploaded_dbq_extracts_and_validates_identity(self):
        state = _sample_state()
        state["diagnosticOrdersById"]["ord-1"]["resultIds"] = ["res-1"]
        state["diagnosticResultsById"]["res-1"] = {
            "id": "res-1",
            "orderId": "ord-1",
            "sourceReference": "xray.pdf",
            "summary": "Mild joint space narrowing.",
            "reportedFlag": "abnormal",
            "reviewedAt": "2026-11-26T10:00:00Z",
        }
        good_upload = (
            b"Claimant: Morgan Q Demo\n"
            b"History: Right knee pain since 2024 PT run.\n"
            b"Examination Findings: Right knee flexion 115 degrees ROM, mild tenderness.\n"
            b"Diagnosis: Established - Right knee patellofemoral pain syndrome.\n"
            b"Clinical Rationale: Consistent with service-connected knee strain.\n"
            b"Records Reviewed: SHA_Part_A.pdf p. 17\n"
        )
        out = run_clinical_pipeline(
            state,
            "dbq-1",
            mode="uploaded-dbq",
            uploaded_dbq_file={"filename": "completed_knee_dbq.txt", "content": good_upload},
            allow_llm=False,
        )
        self.assertTrue(out["validation_passed"], f"Unexpected alerts: {out['validation_alerts']}")
        self.assertEqual(out["extracted_values"]["diagnosisOutcome"], "Established")
        self.assertIn("115 degrees", out["extracted_values"]["findings"])

        wrong_identity = good_upload.replace(b"Morgan Q Demo", b"John Doe")
        out_bad = run_clinical_pipeline(
            state,
            "dbq-1",
            mode="uploaded-dbq",
            uploaded_dbq_file={"filename": "wrong_person.txt", "content": wrong_identity},
            allow_llm=False,
        )
        self.assertFalse(out_bad["validation_passed"])
        self.assertTrue(any(a["category"] == "identity" for a in out_bad["validation_alerts"]))

    def test_diagnostic_upload_and_clinical_view(self):
        parsed = _parse_diagnostic_uploads([
            {"orderId": "ord-1", "filename": "knee_xray.txt", "content": b"Impression: Abnormal medial meniscus tear."}
        ])
        self.assertTrue(parsed[0]["readable"])
        self.assertEqual(parsed[0]["reportedFlag"], "abnormal")

        view = with_clinical_view(_sample_state())
        self.assertEqual(view["clinical"]["queueIds"], ["esr-1"])
        self.assertEqual(view["clinical"]["casesByEsrId"]["esr-1"]["totalCount"], 1)
        self.assertEqual(view["clinical"]["casesByEsrId"]["esr-1"]["signedCount"], 0)


if __name__ == "__main__":
    unittest.main()
