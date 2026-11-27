"""Authoritative backend Stage 6 Delivery, Contention RAP, DB Idempotency & Rework ESR tests."""

from __future__ import annotations

import json
import sys
import unittest
from pathlib import Path

from fastapi import HTTPException
from sqlalchemy import create_engine, select
from sqlalchemy.orm import sessionmaker

BACKEND_ROOT = Path(__file__).resolve().parents[1]
if str(BACKEND_ROOT) not in sys.path:
    sys.path.insert(0, str(BACKEND_ROOT))

from app.core.database import Base
from app.models import AuditEvent, Stage6DeliveryOperation
from app.services.qa_rules import enforce_backend_stage6_delivery, run_deterministic_qa_rules


def _sample_stage6_state() -> dict:
    return {
        "revision": 1,
        "veteransById": {"vet-1": {"id": "vet-1", "displayName": "Jordan Sample"}},
        "esrsById": {
            "esr-1": {
                "id": "esr-1",
                "veteranId": "vet-1",
                "aepRecordedAt": "2026-03-01T00:00:00Z",
                "contentionIds": ["cont-1", "cont-2"],
                "evidenceDocumentIds": ["doc-1"],
            }
        },
        "contentionsById": {
            "cont-1": {
                "id": "cont-1",
                "esrId": "esr-1",
                "title": "Knee condition",
                "evidenceDocumentIds": ["doc-1"],
                "examinationRequirementIds": ["req-1"],
            },
            "cont-2": {
                "id": "cont-2",
                "esrId": "esr-1",
                "title": "Hearing loss",
                "evidenceDocumentIds": ["doc-1"],
                "examinationRequirementIds": ["req-2"],
            },
        },
        "evidenceDocumentsById": {
            "doc-1": {
                "id": "doc-1",
                "esrId": "esr-1",
                "title": "VA Record",
                "classificationStatus": "indexed",
                "classifiedPageCount": 2,
                "unreadablePageCount": 0,
                "extractedText": "Right knee MRI meniscus tear.",
            }
        },
        "examinationRequirementsById": {
            "req-1": {"id": "req-1", "esrId": "esr-1", "status": "completed", "dbqIds": ["dbq-1"]},
            "req-2": {"id": "req-2", "esrId": "esr-1", "status": "approved", "dbqIds": []},
        },
        "examinationsById": {
            "exam-1": {"id": "exam-1", "esrId": "esr-1", "examinerId": "ex-1", "requirementIds": ["req-1"], "appointmentIds": ["appt-1"], "diagnosticOrderIds": []}
        },
        "appointmentsById": {"appt-1": {"id": "appt-1", "esrId": "esr-1", "examinerId": "ex-1", "status": "completed", "startsAt": "2026-03-02T10:00:00Z"}},
        "examinersById": {
            "ex-1": {
                "id": "ex-1",
                "displayName": "Dr. Lee",
                "credentials": ["MD"],
                "licenses": [{"state": "OH", "status": "active", "expiresAt": "2028-01-01"}],
                "training": [{"course": "VA C&P", "status": "current", "expiresAt": "2028-01-01"}],
            }
        },
        "dbqsById": {
            "dbq-1": {
                "id": "dbq-1",
                "esrId": "esr-1",
                "examinationId": "exam-1",
                "contentionIds": ["cont-1"],
                "formType": "Knee and Lower Leg Conditions DBQ",
                "formVersion": "synthetic-v2",
                "status": "approved-for-package",
                "currentVersionId": "ver-1",
                "signedVersionId": "ver-1",
                "localReviewState": "review-recorded",
                "localAttestation": {"versionId": "ver-1", "staffActorId": "clin-1", "examinerId": "ex-1", "examinerName": "Dr. Lee", "credentials": ["MD"]},
            }
        },
        "dbqVersionsById": {
            "ver-1": {
                "id": "ver-1",
                "dbqId": "dbq-1",
                "versionNumber": 1,
                "formVersion": "synthetic-v2",
                "values": {"history": "Knee injury.", "findings": "ROM 0-120 deg.", "rationale": "Consistent with strain.", "recordsReviewed": "doc-1"},
            }
        },
        "qaReviewsById": {
            "qa-1": {
                "id": "qa-1",
                "esrId": "esr-1",
                "dbqId": "dbq-1",
                "versionId": "ver-1",
                "status": "approved",
                "findingIds": [],
            }
        },
        "qaFindingsById": {},
        "resultsPackagesById": {
            "pkg-1": {
                "id": "pkg-1",
                "esrId": "esr-1",
                "preparationStatus": "ready-for-review",
                "contentionDeliveries": {
                    "cont-1": {"contentionId": "cont-1", "revisionIds": {"dbq-1": "ver-1"}, "documentUploadStatus": "uploaded"}
                },
            }
        },
    }


class Stage6DeliveryBackendTests(unittest.TestCase):
    def _mem_session(self):
        eng = create_engine("sqlite:///:memory:")
        Base.metadata.create_all(eng)
        return sessionmaker(bind=eng, autoflush=False, autocommit=False)

    def test_stage6_rbac_and_digest_staleness_guard(self) -> None:
        state = _sample_stage6_state()
        run_deterministic_qa_rules(None, state, "qa-1")
        state["qaReviewsById"]["qa-1"]["status"] = "approved"
        state["qaReviewsById"]["qa-1"]["approvedEvidenceDigest"] = state["qaReviewsById"]["qa-1"]["evidenceDigest"]

        # Clinician / QA roles cannot perform delivery commands
        with self.assertRaises(HTTPException) as err_role:
            enforce_backend_stage6_delivery(None, state, "sendContentionRap", ["pkg-1", ["cont-1"]], "user-1", "clinician")
        self.assertEqual(err_role.exception.status_code, 403)

        # Clean delivery succeeds even if requirement status/version incremented during QA approval
        state["examinationRequirementsById"]["req-1"]["version"] = 2
        Session = self._mem_session()
        with Session() as db:
            meta = enforce_backend_stage6_delivery(db, state, "sendContentionRap", ["pkg-1", ["cont-1"]], "user-del", "delivery")
            self.assertEqual(meta["dbOpKind"], "rap-send")

            # Mutate evidence doc prior to delivery -> must reject with 422 and mark QA stale
            state["evidenceDocumentsById"]["doc-1"]["extractedText"] = "NEW CONTRADICTORY IMAGING FINDING"
            with self.assertRaises(HTTPException) as err_stale:
                enforce_backend_stage6_delivery(db, state, "sendContentionRap", ["pkg-1", ["cont-1"]], "user-del", "delivery")
            self.assertEqual(err_stale.exception.status_code, 422)
            self.assertTrue(state["qaReviewsById"]["qa-1"]["isStale"])

    def test_db_idempotency_blocks_duplicate_rap_and_duplicate_rework_esr(self) -> None:
        state = _sample_stage6_state()
        run_deterministic_qa_rules(None, state, "qa-1")
        state["qaReviewsById"]["qa-1"]["status"] = "approved"
        state["qaReviewsById"]["qa-1"]["approvedEvidenceDigest"] = state["qaReviewsById"]["qa-1"]["evidenceDigest"]
        Session = self._mem_session()
        with Session() as db:
            # First RAP send registers idempotency key
            meta = enforce_backend_stage6_delivery(db, state, "sendContentionRap", ["pkg-1", ["cont-1"]], "user-del", "delivery")
            db.add(Stage6DeliveryOperation(esr_id="esr-1", operation_kind=meta["dbOpKind"], idempotency_key=meta["dbOpKey"], status="completed", result_summary={}))
            db.commit()

            # Duplicate RAP send with same revision lock raises 409
            with self.assertRaises(HTTPException) as dup_rap:
                enforce_backend_stage6_delivery(db, state, "sendContentionRap", ["pkg-1", ["cont-1"]], "user-del", "delivery")
            self.assertEqual(dup_rap.exception.status_code, 409)

            # Duplicate incoming Rework ESR also blocked with 409
            rw_arg = [{"originalEsrId": "esr-1", "affectedContentionIds": ["cont-1", "cont-2"], "reworkCategory": "insufficient", "vaSpecialInstructions": "Add ROM degrees."}]
            rw_meta = enforce_backend_stage6_delivery(db, state, "createLinkedReworkESR", rw_arg, "user-del", "delivery")
            db.add(Stage6DeliveryOperation(esr_id="esr-1", operation_kind=rw_meta["dbOpKind"], idempotency_key=rw_meta["dbOpKey"], status="completed", result_summary={}))
            db.commit()

            with self.assertRaises(HTTPException) as dup_rw:
                enforce_backend_stage6_delivery(db, state, "createLinkedReworkESR", rw_arg, "user-del", "delivery")
            self.assertEqual(dup_rw.exception.status_code, 409)


if __name__ == "__main__":
    unittest.main()
