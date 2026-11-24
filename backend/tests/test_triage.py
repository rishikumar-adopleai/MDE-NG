import unittest

from fastapi import HTTPException

from app.services.intake_store import add_case, apply_intake_command, refresh_intake_work, timestamp
from app.services.intake_view import with_intake_views
from app.services.triage import apply_triage_command, with_triage_view
from app.services.workspace_store import empty_workspace


class TriageTest(unittest.TestCase):
    def setUp(self):
        self.plan = {
            "examCode": "knee", "modality": "in-person",
            "dbqs": ["Knee and Lower Leg Conditions DBQ"], "diagnostics": [],
            "rationale": "Reviewed the synthetic source excerpt for this contention.",
        }
        self.state = {
            "revision": 1,
            "esrsById": {"esr": {
                "id": "esr", "intakeStatus": "ready-for-triage", "priority": "routine",
                "contentionIds": ["knee", "headache"], "evidenceDocumentIds": ["doc"],
            }},
            "contentionsById": {
                key: {"id": key, "esrId": "esr", "title": title, "version": 1,
                      "status": "awaiting-review", "evidenceDocumentIds": evidence,
                      "examinationRequirementIds": []}
                for key, title, evidence in [
                    ("knee", "Knee condition", ["doc"]), ("headache", "Headaches", []),
                ]
            },
            "evidenceDocumentsById": {"doc": {
                "id": "doc", "esrId": "esr", "classificationStatus": "indexed",
                "classifiedPageCount": 1,
            }},
            "examinationRequirementsById": {}, "activitiesById": {}, "workItemsById": {
                key: {"stage": "Medical Triage", "status": "queued", "entityId": key, "priority": "routine", "esrId": "esr"}
                for key in ("knee", "headache")
            },
        }

    def command(self, action, data, item="knee"):
        return apply_triage_command(self.state, action, [item, data], "synthetic-reviewer")

    def test_draft_clarification_and_exact_contention_handoff(self):
        self.command("saveTriageDraft", self.plan)
        self.command("requestTriageClarification", "Confirm the referenced page.")
        rows = with_triage_view(self.state)["triage"]["rows"]
        self.assertTrue(next(row for row in rows if row["contentionId"] == "knee")["awaitingResponse"])
        with self.assertRaisesRegex(HTTPException, "Record the clarification response"):
            self.command("approveTriagePlan", self.plan)
        self.command("resumeTriageReview", "Source page confirmed.")
        self.assertEqual(self.state["contentionsById"]["knee"]["triageDraft"], self.plan)
        self.command("approveTriagePlan", self.plan)
        requirement, = self.state["examinationRequirementsById"].values()
        self.assertEqual(requirement["contentionIds"], ["knee"])
        self.assertEqual(self.state["contentionsById"]["headache"]["status"], "awaiting-review")
        scheduled, = (item for item in self.state["workItemsById"].values() if item["stage"] == "Scheduling")
        self.assertEqual(scheduled["entityId"], requirement["id"])
        self.assertEqual({row["contentionId"] for row in with_triage_view(self.state)["triage"]["rows"]}, {"headache"})

    def test_evidence_and_primary_dbq_are_required(self):
        with self.assertRaisesRegex(HTTPException, "readable evidence"):
            self.command("approveTriagePlan", self.plan, "headache")
        with self.assertRaisesRegex(HTTPException, "primary DBQ"):
            self.command("approveTriagePlan", {**self.plan, "dbqs": ["Medical Opinion DBQ"]})
        self.assertEqual(self.state["examinationRequirementsById"], {})

    def test_intake_requires_readable_evidence_for_each_contention(self):
        state = empty_workspace()
        now = timestamp()
        actor = "reviewer"
        esr = add_case(state, "Example claimant", ["Knee condition", "Headaches"], actor, now)
        esr_id = esr["id"]
        knee, headache = (state["contentionsById"][key] for key in esr["contentionIds"])
        esr["evidenceDocumentIds"] = ["unreadable"]
        unreadable = {
            "id": "unreadable", "esrId": esr_id, "classificationStatus": "needs-human-review",
            "classifiedPageCount": 0, "pageCount": 1, "unreadablePageCount": 1,
        }
        state["evidenceDocumentsById"]["unreadable"] = unreadable
        knee["evidenceDocumentIds"] = ["unreadable"]
        headache["evidenceDocumentIds"] = ["unreadable"]
        state["intakeExceptionsById"]["issue"] = {
            "id": "issue", "esrId": esr_id, "documentId": "unreadable", "title": "Unreadable source",
            "status": "open", "detail": "No text layer.", "version": 1, "createdAt": now,
        }
        refresh_intake_work(state, esr_id, now)
        apply_intake_command(state, "reviewIntakeException", ["issue", "escalated", "Request a readable copy."], actor)

        def view():
            return with_intake_views(state)["intake"]["viewsByEsrId"][esr_id]

        self.assertTrue(view()["canAttach"])
        self.assertTrue(view()["needsReadableEvidence"])
        with self.assertRaisesRegex(HTTPException, "Attach a readable source document"):
            apply_intake_command(state, "validateIntake", [esr_id], actor)
        self.assertEqual(with_triage_view(state)["triage"]["rows"], [])

        state["evidenceDocumentsById"]["replacement"] = {
            **unreadable, "id": "replacement", "classificationStatus": "indexed",
            "classifiedPageCount": 1, "unreadablePageCount": 0,
        }
        esr["evidenceDocumentIds"].append("replacement")
        knee["evidenceDocumentIds"].append("replacement")
        self.assertFalse(view()["canValidate"])
        headache["evidenceDocumentIds"].append("replacement")
        self.assertTrue(view()["canValidate"])
        apply_intake_command(state, "validateIntake", [esr_id], actor)
        self.assertEqual(esr["intakeStatus"], "ready-for-triage")
        rows = with_triage_view(state)["triage"]["rows"]
        self.assertEqual(len(rows), 2)
        self.assertTrue(all(row["readableEvidence"] for row in rows))

    def test_ace_requires_authorization_and_unvalidated_intake_stays_out(self):
        ace_plan = {**self.plan, "examCode": "migraine", "modality": "ace-records-review", "dbqs": ["Headaches DBQ"]}
        with self.assertRaisesRegex(HTTPException, "offered for this examination"):
            self.command("approveTriagePlan", ace_plan)
        self.state["contentionsById"]["knee"]["aceAuthorizationReference"] = "SYNTHETIC-AUTH-1"
        self.assertEqual(with_triage_view(self.state)["triage"]["rows"][0]["pathwaysByExam"]["migraine"][-1], "ace-records-review")
        self.state["esrsById"]["esr"]["intakeStatus"] = "requires-attention"
        self.assertEqual(with_triage_view(self.state)["triage"]["rows"], [])
        with self.assertRaisesRegex(HTTPException, "Intake must be validated"):
            self.command("approveTriagePlan", ace_plan)
