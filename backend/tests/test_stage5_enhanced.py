"""Comprehensive Stage 5 QA Enhancement Tests & Scalability Benchmark."""

from __future__ import annotations

import json
import sys
import time
import tracemalloc
import unittest
from pathlib import Path
from unittest.mock import patch

from fastapi import HTTPException
from sqlalchemy import create_engine, select
from sqlalchemy.orm import sessionmaker

BACKEND_ROOT = Path(__file__).resolve().parents[1]
if str(BACKEND_ROOT) not in sys.path:
    sys.path.insert(0, str(BACKEND_ROOT))

from app.core.database import Base
from app.models import AuditEvent, OutboxJob, Stage3Examiner, Stage5QAAuditJob
from app.services.qa_pipeline import (
    enqueue_qa_audit_job,
    run_guarded_qa_audit_llm,
    run_qa_audit_for_state,
)
from app.services.qa_rules import (
    enforce_backend_qa_approval,
    invalidate_stale_qa_reviews,
    run_deterministic_qa_rules,
)


def _base_state(
    *,
    num_docs: int = 2,
    chars_per_doc: int = 600,
    num_facts: int = 4,
    examiner_state: str = "TX",
    facility_state: str = "TX",
    examiner_portability: bool = True,
    examiner_cred: str = "MD",
    examiner_npi: str = "1234567890",
    license_expiry: str = "2028-12-31T00:00:00Z",
    training_completed: str = "2026-01-15T00:00:00Z",
    exam_starts: str = "2026-03-01T14:00:00Z",
    signing_examiner_id: str | None = None,
    reconciliation_note: str = "",
    rationale: str = "Clinical rationale connects documented pathology to service event without rating commentary.",
    findings: str = "Right knee ROM 0 to 125 degrees with pain at endpoint.",
    remarks: str = "Reviewed all linked evidence.",
    is_bva: bool = False,
    form_type: str = "Knee DBQ",
    requested_form: str = "Knee and Lower Leg Disability Benefits Questionnaire",
    dbq_version_id: str = "ver-1",
    num_versions: int = 1,
) -> dict:
    doc_ids = [f"doc-{i}" for i in range(1, num_docs + 1)]
    docs = {}
    for i, did in enumerate(doc_ids, 1):
        docs[did] = {
            "id": did,
            "esrId": "esr-1",
            "title": f"Medical Evidence {i}",
            "category": "va-medical-records",
            "sourceReference": f"efolder_{i}.pdf",
            "classificationStatus": "indexed",
            "classifiedPageCount": 2,
            "unreadablePageCount": 0,
            "extractedPages": [
                {"pageNumber": 1, "text": f"Record {i} page 1 clinical detail: " + ("X" * max(50, chars_per_doc // 2))},
                {"pageNumber": 2, "text": f"Record {i} page 2 imaging & measurements: " + ("Y" * max(50, chars_per_doc // 2))},
            ],
        }
    facts = {}
    for i in range(1, num_facts + 1):
        fid = f"fact-{i}"
        facts[fid] = {
            "id": fid,
            "esrId": "esr-1",
            "contentionId": "cont-1",
            "factType": "diagnosis" if i % 2 else "measurement",
            "label": f"Right knee finding {i}",
            "laterality": "right",
            "date": f"2025-0{(i % 9) + 1}-10",
            "measurement": f"{110 + i} deg",
            "documentId": doc_ids[(i - 1) % len(doc_ids)],
            "page": 1,
        }

    examiner_obj = {
        "id": "exam-doc-1",
        "displayName": "Dr. Jane Smith, MD",
        "credentials": [examiner_cred],
        "specialties": ["Orthopedics"],
        "npi": examiner_npi,
        "licenses": [{"state": examiner_state, "status": "active", "expiresAt": license_expiry}],
        "licenseStatus": "active",
        "licenseExpiresAt": license_expiry,
        "interstatePortabilityAuthorized": examiner_portability,
        "training": [{
            "course": "VA C&P Initial Certification",
            "status": "current",
            "completedAt": training_completed,
            "expiresAt": "2027-01-15T00:00:00Z",
        }],
        "trainingCompletedAt": training_completed,
    }
    versions = {
        "ver-1": {
            "id": "ver-1",
            "dbqId": "dbq-1",
            "versionNumber": 1,
            "formVersion": "synthetic-v2",
            "values": {
                "history": "Right knee injury history.",
                "findings": findings,
                "rationale": rationale,
                "recordsReviewed": "STR and VA MRI reviewed.",
                "remarks": remarks,
                "diagnosisOutcome": "Established",
                "diagnosisDetails": "Right knee strain",
                **({"examinerReconciliationNote": reconciliation_note} if reconciliation_note else {}),
            },
        }
    }
    if dbq_version_id != "ver-1" or num_versions > 1:
        versions[dbq_version_id] = {
            "id": dbq_version_id,
            "dbqId": "dbq-1",
            "versionNumber": 2,
            "formVersion": "synthetic-v2",
            "values": dict(versions["ver-1"]["values"]),
        }

    return {
        "revision": 1,
        "veteransById": {"vet-1": {"id": "vet-1", "displayName": "Alex Morgan"}},
        "esrsById": {
            "esr-1": {
                "id": "esr-1",
                "veteranId": "vet-1",
                "aepRecordedAt": "2026-02-01T00:00:00Z",
                "contentionIds": ["cont-1"],
                "evidenceDocumentIds": doc_ids,
            }
        },
        "contentionsById": {
            "cont-1": {
                "id": "cont-1",
                "esrId": "esr-1",
                "title": "Right knee condition",
                "condition": "Right knee condition",
                "laterality": "right",
                "requestedDbqNames": [requested_form],
                "evidenceDocumentIds": doc_ids,
                "triageDraft": {},
            }
        },
        "triageDossiersByEsrId": {
            "esr-1": {
                "esrId": "esr-1",
                "preparedAt": "2026-02-20T00:00:00Z",
                "programFlags": {"isBvaRemand": is_bva},
                "evidenceGaps": [{"id": "gap-1", "contentionId": "cont-1", "title": "Check ROM endpoint pain"}],
                "distressAlerts": [],
            }
        },
        "clinicalFactsById": facts,
        "evidenceDocumentsById": docs,
        "examinationRequirementsById": {
            "req-1": {
                "id": "req-1",
                "esrId": "esr-1",
                "status": "scheduled",
                "selectedModality": "in-person",
                "requestedDbqTypes": [form_type],
                "requiredDbqTypes": [form_type],
                "dbqIds": ["dbq-1"],
            }
        },
        "facilitiesById": {"fac-1": {"id": "fac-1", "stateCode": facility_state}},
        "examinersById": {"exam-doc-1": examiner_obj},
        "appointmentsById": {
            "appt-1": {
                "id": "appt-1",
                "esrId": "esr-1",
                "facilityId": "fac-1",
                "examinerId": "exam-doc-1",
                "modality": "in-person",
                "status": "completed",
                "startsAt": exam_starts,
            }
        },
        "examinationsById": {
            "exam-1": {
                "id": "exam-1",
                "esrId": "esr-1",
                "examinerId": "exam-doc-1",
                "requirementIds": ["req-1"],
                "appointmentIds": ["appt-1"],
                "requiredDbqIds": ["dbq-1"],
                "diagnosticOrderIds": [],
                "stage4": {"draftMedicalReview": {"summary": "Right knee ROM consistent with strain."}},
            }
        },
        "diagnosticOrdersById": {},
        "diagnosticResultsById": {},
        "dbqsById": {
            "dbq-1": {
                "id": "dbq-1",
                "esrId": "esr-1",
                "examinationId": "exam-1",
                "contentionIds": ["cont-1"],
                "formType": form_type,
                "formVersion": "synthetic-v2",
                "status": "ready-for-qa",
                "currentVersionId": dbq_version_id,
                "signedVersionId": dbq_version_id,
                "versionIds": list(versions.keys()),
                "localReviewState": "review-recorded",
                "localAttestation": {
                    "versionId": dbq_version_id,
                    "staffActorId": signing_examiner_id or "exam-doc-1",
                    "examinerId": signing_examiner_id or "exam-doc-1",
                    "examinerName": "Dr. Jane Smith",
                    "credentials": [examiner_cred],
                    "at": "2026-03-02T10:00:00Z",
                },
            }
        },
        "dbqVersionsById": versions,
        "qaReviewsById": {
            "qa-1": {
                "id": "qa-1",
                "esrId": "esr-1",
                "dbqId": "dbq-1",
                "versionId": dbq_version_id,
                "status": "in-review",
                "findingIds": [],
            }
        },
        "qaFindingsById": {},
        "resultsPackagesById": {},
        "activitiesById": {},
    }


class Stage5EnhancedTests(unittest.TestCase):
    def _mem_session(self):
        eng = create_engine("sqlite:///:memory:")
        Base.metadata.create_all(eng)
        return sessionmaker(bind=eng, autoflush=False, autocommit=False)

    # --- 1. Evidence Coverage, Unbounded Excerpts & Multi-Batch Processing ---
    def test_large_efolder_processes_more_than_14_excerpts_via_multi_batch_without_truncation(self) -> None:
        # 22 docs * 6,000 chars = 132,000 chars -> must plan >= 2 batches and review ALL 22 docs!
        state = _base_state(num_docs=22, chars_per_doc=6000, num_facts=30)
        captured_batches: list[dict] = []

        def fake_llm(schema_name, schema, instructions, sources, **kwargs):
            payload = json.loads(sources[0]["text"])
            captured_batches.append(payload)
            return json.dumps({
                "summary": f"Batch {payload['batchIndex']} reviewed cleanly.",
                "warnings": [{
                    "title": "Recurring ROM note",
                    "sectionReference": "Findings",
                    "issueCategory": "advisory",
                    "severity": "advisory",
                    "rationale": "Verified in batch.",
                    "sourceIds": [payload["eFolderExcerpts"][0]["sourceId"]],
                }] if payload.get("eFolderExcerpts") else [],
            })

        with patch("app.services.qa_pipeline.settings.intake_provider", "azure-openai"), \
             patch("app.services.qa_pipeline.azure_review_completion", side_effect=fake_llm), \
             patch("app.services.qa_pipeline.get_last_completion_provider", return_value="gpt-5.4"), \
             patch("app.services.qa_pipeline.get_last_completion_audit", return_value={"fallbackUsed": False}):
            out = run_qa_audit_for_state(None, state, "qa-1", allow_llm=True, force=True)

        cov = out["evidenceCoverage"]
        self.assertGreaterEqual(cov["batchesPlanned"], 2)
        self.assertEqual(cov["batchesSucceeded"], cov["batchesPlanned"])
        self.assertTrue(cov["completeCoverage"])
        self.assertEqual(cov["unavailableOrUnreadableCount"], 0)
        # Ensure doc-22 and fact-30 were NOT truncated by old 14-doc / 20-fact limits
        all_source_ids = {sid for b in captured_batches for sid in b.get("validSourceIds", [])}
        self.assertIn("S22", all_source_ids)
        self.assertIn("FACT-fact-30", all_source_ids)
        # Recurring duplicate warnings across batches merged cleanly into 1 warning with combined citations
        self.assertEqual(out["aiAudit"]["warningCount"], 1)

    def test_partial_batch_and_total_ai_failure_require_manual_override(self) -> None:
        state = _base_state(num_docs=20, chars_per_doc=6000)

        # Partial batch failure: batch 1 ok, batch 2 raises
        calls = {"n": 0}

        def partial_llm(*args, **kwargs):
            calls["n"] += 1
            if calls["n"] <= 1:
                return json.dumps({"summary": "Batch 1 ok.", "warnings": []})
            raise RuntimeError("Upstream provider error")

        with patch("app.services.qa_pipeline.settings.intake_provider", "azure-openai"), \
             patch("app.services.qa_pipeline.azure_review_completion", side_effect=partial_llm), \
             patch("app.services.qa_pipeline.get_last_completion_provider", return_value="azure"), \
             patch("app.services.qa_pipeline.get_last_completion_audit", return_value={"fallbackUsed": True}):
            out = run_qa_audit_for_state(None, state, "qa-1", allow_llm=True, force=True)

        self.assertEqual(out["aiAudit"]["status"], "partial")
        self.assertTrue(out["aiAudit"]["manualReviewRequired"])
        self.assertFalse(out["evidenceCoverage"]["completeCoverage"])

        # Backend approval must reject without explicit manual override + note >= 10 chars
        with self.assertRaises(HTTPException) as err:
            enforce_backend_qa_approval(None, state, "qa-1", "qa-reviewer-1", "qa", "ok", False)
        self.assertEqual(err.exception.status_code, 422)

        # With manual override + 10+ char note -> succeeds
        meta = enforce_backend_qa_approval(
            None, state, "qa-1", "qa-reviewer-1", "qa", "Manually verified all 20 evidence records.", True
        )
        self.assertTrue(meta["manualAiOverride"])

    # --- 2. P.L. 116-315 § 2002 Portability, Exam Date & NPI Rules ---
    def test_interstate_portability_allows_md_and_blocks_non_covered_or_unlinked(self) -> None:
        # Out-of-state TX MD examining in FL with P.L. 116-315 portability -> passed (with note)
        state = _base_state(examiner_state="TX", facility_state="FL", examiner_portability=True, examiner_cred="MD")
        res = run_deterministic_qa_rules(None, state, "qa-1")
        doc_chk = next(c for c in res["checks"] if c["key"] == "doctor-credentials")
        self.assertEqual(doc_chk["status"], "passed")
        self.assertIn("2002", doc_chk["detail"])

        # Same cross-state without portability -> failed
        state2 = _base_state(examiner_state="TX", facility_state="FL", examiner_portability=False, examiner_cred="MD")
        res2 = run_deterministic_qa_rules(None, state2, "qa-1")
        self.assertEqual(next(c for c in res2["checks"] if c["key"] == "doctor-credentials")["status"], "failed")

        # License expired AFTER the exam date (valid ON exam date) -> passed
        state3 = _base_state(exam_starts="2026-03-01T14:00:00Z", license_expiry="2026-04-01T00:00:00Z")
        res3 = run_deterministic_qa_rules(None, state3, "qa-1")
        self.assertEqual(next(c for c in res3["checks"] if c["key"] == "doctor-credentials")["status"], "passed")

        # Assigned vs signing examiner mismatch without reconciliation note -> failed
        state4 = _base_state(signing_examiner_id="other-doc")
        res4 = run_deterministic_qa_rules(None, state4, "qa-1")
        self.assertEqual(next(c for c in res4["checks"] if c["key"] == "doctor-credentials")["status"], "failed")

        # With reconciliation note -> passed (when signing examiner also has valid directory record)
        state5 = _base_state(signing_examiner_id="other-doc", reconciliation_note="Supervisory co-signer reconciled.")
        state5["examinersById"]["other-doc"] = {
            **state5["examinersById"]["exam-doc-1"],
            "id": "other-doc",
            "displayName": "Dr. Other Signer, MD",
        }
        res5 = run_deterministic_qa_rules(None, state5, "qa-1")
        self.assertEqual(next(c for c in res5["checks"] if c["key"] == "doctor-credentials")["status"], "passed")

    # --- 3. Ambiguous vs Unambiguous Forbidden Commentary ---
    def test_forbidden_words_allows_clinical_percentages_and_history_while_flagging_ambiguous(self) -> None:
        # Legitimate clinical percentage (% BSA), ROM, service connection history -> passed!
        s_clean = _base_state(
            findings="Psoriasis covers 15% body surface area (BSA); knee flexion 115 degrees.",
            rationale="Claimant has prior treatment with oral ibuprofen in 2022. Per VA requested medical opinion, right knee arthritis is at least as likely as not related to service injury.",
        )
        r_clean = run_deterministic_qa_rules(None, s_clean, "qa-1")
        self.assertEqual(next(c for c in r_clean["checks"] if c["key"] == "forbidden-words")["status"], "passed")

        # Ambiguous term "service connection" outside VA opinion anchor -> human-review-required (NOT blocking!)
        s_amb = _base_state(
            rationale="The clinical findings indicate service connection applies to the left shoulder.",
        )
        r_amb = run_deterministic_qa_rules(None, s_amb, "qa-1")
        fw_amb = next(c for c in r_amb["checks"] if c["key"] == "forbidden-words")
        self.assertEqual(fw_amb["status"], "human-review-required")
        self.assertEqual(fw_amb["severity"], "review")

    # --- 4. Raw Psych vs Authorized Summary vs Uncertain Psych & 48h Non-Critical Safety SLA ---
    def test_uncertain_psych_and_48h_non_critical_abnormal_slas(self) -> None:
        state = _base_state()
        state["examinationsById"]["exam-1"]["diagnosticOrderIds"] = ["ord-1"]
        state["diagnosticOrdersById"]["ord-1"] = {
            "id": "ord-1",
            "esrId": "esr-1",
            "examinationId": "exam-1",
            "testName": "CBC Panel",
            "status": "reviewed",
            "resultIds": ["res-abn"],
        }
        # Non-critical abnormal communicated within 48h without critical incident report -> passed!
        state["diagnosticResultsById"]["res-abn"] = {
            "id": "res-abn",
            "esrId": "esr-1",
            "orderId": "ord-1",
            "reportedFlag": "abnormal",
            "sourceReference": "CBC Panel",
            "createdAt": "2026-03-01T10:00:00Z",
            "reviewedAt": "2026-03-02T12:00:00Z",
            "patientNotifiedAt": "2026-03-02T14:00:00Z",
            "classificationUncertain": True,  # Uncertain document triggers psych privacy review
        }
        res = run_deterministic_qa_rules(None, state, "qa-1")
        self.assertEqual(next(c for c in res["checks"] if c["key"] == "labs-safety-24h")["status"], "passed")
        self.assertEqual(next(c for c in res["checks"] if c["key"] == "privacy-raw-psych")["status"], "human-review-required")

    # --- 5. BVA Remand Scope & Cross-Version Resubmission Reconciliation ---
    def test_bva_remand_unapproved_substitution_and_cross_version_reconciliation(self) -> None:
        state = _base_state(
            is_bva=True,
            form_type="Back Conditions DBQ",
            requested_form="MUSC Knee and Lower Leg",
        )
        res1 = run_deterministic_qa_rules(None, state, "qa-1")
        bva_chk = next(c for c in res1["checks"] if c["key"] == "dbq-scope-bva")
        self.assertEqual(bva_chk["status"], "failed")
        f_v1 = [f for f in state["qaFindingsById"].values() if f["checkKey"] == "dbq-scope-bva"]
        self.assertEqual(len(f_v1), 1)
        f_v1[0]["status"] = "correction-requested"

        # Add a manual human review finding from v1 that should stay OPEN across v2 resubmission
        state["qaFindingsById"]["finding-human-v1"] = {
            "id": "finding-human-v1",
            "qaReviewId": "qa-1",
            "origin": "reviewer",
            "severity": "review",
            "status": "correction-requested",
            "title": "Verify imaging date",
            "versionId": "ver-1",
        }
        state["qaReviewsById"]["qa-1"]["findingIds"].append("finding-human-v1")

        # Resubmit corrected v2 with authorized Knee DBQ & same signoff
        state["dbqsById"]["dbq-1"]["formType"] = "Knee and Lower Leg Conditions DBQ"
        state["dbqsById"]["dbq-1"]["currentVersionId"] = "ver-2"
        state["dbqsById"]["dbq-1"]["signedVersionId"] = "ver-2"
        state["dbqsById"]["dbq-1"]["localAttestation"]["versionId"] = "ver-2"
        state["dbqVersionsById"]["ver-2"] = {**state["dbqVersionsById"]["ver-1"], "id": "ver-2", "versionNumber": 2}
        state["qaReviewsById"]["qa-1"]["versionId"] = "ver-2"

        res2 = run_deterministic_qa_rules(None, state, "qa-1")
        self.assertEqual(next(c for c in res2["checks"] if c["key"] == "dbq-scope-bva")["status"], "passed")
        self.assertEqual(state["qaFindingsById"][f_v1[0]["id"]]["status"], "resolved")
        self.assertEqual(state["qaFindingsById"]["finding-human-v1"]["status"], "correction-requested")

        # Unresolved review-severity human finding STILL blocks approval!
        with self.assertRaises(HTTPException):
            enforce_backend_qa_approval(None, state, "qa-1", "qa-reviewer-1", "qa", "Try approve", False)

    # --- 6. Selective Stale Invalidation & Unrelated DBQ Isolation ---
    def test_selective_stale_invalidation_preserves_unrelated_dbq(self) -> None:
        state = _base_state(num_docs=2)
        # Add second independent contention & DBQ linked only to doc-2
        state["contentionsById"]["cont-2"] = {
            "id": "cont-2", "esrId": "esr-1", "title": "Left ear", "evidenceDocumentIds": ["doc-2"],
        }
        state["contentionsById"]["cont-1"]["evidenceDocumentIds"] = ["doc-1"]
        state["examinationsById"]["exam-2"] = {
            "id": "exam-2", "esrId": "esr-1", "examinerId": "exam-doc-1",
            "requirementIds": ["req-1"], "appointmentIds": ["appt-1"], "diagnosticOrderIds": [],
        }
        state["dbqVersionsById"]["ver-2-dbq2"] = {
            **state["dbqVersionsById"]["ver-1"],
            "id": "ver-2-dbq2",
            "dbqId": "dbq-2",
        }
        state["dbqsById"]["dbq-2"] = {
            **state["dbqsById"]["dbq-1"],
            "id": "dbq-2",
            "examinationId": "exam-2",
            "contentionIds": ["cont-2"],
            "currentVersionId": "ver-2-dbq2",
            "signedVersionId": "ver-2-dbq2",
            "versionIds": ["ver-2-dbq2"],
            "localAttestation": {**state["dbqsById"]["dbq-1"]["localAttestation"], "versionId": "ver-2-dbq2"},
        }
        state["qaReviewsById"]["qa-2"] = {
            **state["qaReviewsById"]["qa-1"],
            "id": "qa-2", "dbqId": "dbq-2", "versionId": "ver-2-dbq2", "status": "approved",
        }
        run_deterministic_qa_rules(None, state, "qa-1")
        run_deterministic_qa_rules(None, state, "qa-2")
        state["qaReviewsById"]["qa-2"]["status"] = "approved"

        # Mutate doc-1 text only and invalidate for doc-1
        state["evidenceDocumentsById"]["doc-1"]["extractedPages"][0]["text"] = "UPDATED doc-1 medical text!"
        invalidated = invalidate_stale_qa_reviews(
            state, "esr-1", "2026-03-03T00:00:00Z", "doc-1 re-indexed", affected_doc_ids={"doc-1"}
        )
        self.assertEqual(invalidated, ["qa-1"])
        self.assertTrue(state["qaReviewsById"]["qa-1"]["isStale"])
        self.assertFalse(state["qaReviewsById"]["qa-2"].get("isStale", False))
        self.assertEqual(state["qaReviewsById"]["qa-2"]["status"], "approved")

    # --- 7. DB Job Deduplication & Non-PHI Audit Events ---
    def test_db_job_deduplication_and_non_phi_audit_logging(self) -> None:
        from app.models import WorkspaceSnapshot
        Session = self._mem_session()
        state = _base_state()
        with Session() as db:
            db.add(WorkspaceSnapshot(id="mde-ng-demo-workspace", state=state, revision=1))
            db.commit()

            j1 = enqueue_qa_audit_job(db, "qa-1", force=False)
            db.commit()
            j2 = enqueue_qa_audit_job(db, "qa-1", force=False)
            db.commit()
            self.assertEqual(j1, j2)
            jobs = db.scalars(select(Stage5QAAuditJob)).all()
            self.assertEqual(len(jobs), 1)
            events = db.scalars(select(AuditEvent)).all()
            self.assertEqual(len(events), 1)
            self.assertNotIn("Alex Morgan", events[0].detail)
            self.assertNotIn("Right knee injury history", events[0].detail)

    # --- 8. Stage 5 Performance Benchmark Across Small / Medium / Large eFolders ---
    def test_benchmark_small_medium_large_synthetic_efolders(self) -> None:
        tiers = [
            ("small", 3, 800, 5),
            ("medium", 25, 4000, 40),
            ("large", 60, 6000, 120),
        ]
        results = []
        tracemalloc.start()
        for name, n_docs, c_per_doc, n_facts in tiers:
            state = _base_state(num_docs=n_docs, chars_per_doc=c_per_doc, num_facts=n_facts)
            t0 = time.perf_counter()
            with patch("app.services.qa_pipeline.settings.intake_provider", "azure-openai"), \
                 patch("app.services.qa_pipeline.azure_review_completion", return_value=json.dumps({"summary": "Clean.", "warnings": []})), \
                 patch("app.services.qa_pipeline.get_last_completion_provider", return_value="gpt-5.4"), \
                 patch("app.services.qa_pipeline.get_last_completion_audit", return_value={"fallbackUsed": False}):
                out = run_qa_audit_for_state(None, state, "qa-1", allow_llm=True, force=True)
            elapsed_ms = (time.perf_counter() - t0) * 1000.0
            results.append((name, n_docs, out["evidenceCoverage"]["batchesPlanned"], out["evidenceCoverage"]["processedCount"], round(elapsed_ms, 2)))
            self.assertTrue(out["evidenceCoverage"]["completeCoverage"])
            self.assertLess(elapsed_ms, 5000.0)
        _curr, peak = tracemalloc.get_traced_memory()
        tracemalloc.stop()
        print(f"BENCHMARK_STAGE5={results} peak_mb={round(peak / (1024 * 1024), 2)}")


if __name__ == "__main__":
    unittest.main()
