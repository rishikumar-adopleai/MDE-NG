import json
import unittest
from unittest.mock import patch

from fastapi import HTTPException

from app.api.workspace import AIReviewRequest, ai_review
from app.core.security import CurrentUser
from app.services.stage_ai import generate_stage_review
from app.services.workspace_store import empty_workspace


class StageAITests(unittest.TestCase):
    def setUp(self):
        self.state = empty_workspace()
        self.ids = {
            "Intake": "esr", "Medical Triage": "knee", "Scheduling": "exam",
            "Clinical Evaluation": "dbq", "Internal QA": "qa", "VA Delivery & Review": "pack",
        }
        self.state["esrsById"]["esr"] = {
            "id": "esr", "intakeStatus": "ready-for-triage", "contentionIds": ["knee"], "evidenceDocumentIds": ["doc"],
            "receivedAt": "2026-11-20", "receiptStatus": "received", "aepRecordedAt": "2026-11-20",
        }
        for stage, entity in self.ids.items():
            self.state["workItemsById"][stage] = {"id": stage, "stage": stage, "entityId": entity,
                "esrId": "esr", "status": "queued", "priority": "routine"}
        self.state["contentionsById"]["knee"] = {
            "id": "knee", "esrId": "esr", "title": "Knee condition", "status": "awaiting-review", "evidenceDocumentIds": ["doc", "other"],
        }
        self.state["evidenceDocumentsById"]["doc"] = {
            "id": "doc", "esrId": "esr", "title": "Synthetic source", "pageCount": 1, "classifiedPageCount": 1,
            "unreadablePageCount": 0, "classificationStatus": "indexed",
            "syntheticExcerpts": [{"contentionId": "knee", "pageNumber": 2, "text": "Linked synthetic evidence."}],
        }
        self.state["evidenceDocumentsById"]["other"] = {
            "id": "other", "esrId": "unrelated", "title": "Unrelated record", "classifiedPageCount": 1,
        }
        self.state["dbqsById"]["dbq"] = {"esrId": "esr", "examinationId": "exam", "formType": "Example DBQ",
            "contentionIds": ["knee"], "signedVersionId": "signed", "values": {"findings": "Unrelated unsigned revision."}}
        self.state["examinationsById"]["exam"] = {"diagnosticOrderIds": ["order"]}
        self.state["diagnosticOrdersById"]["order"] = {"esrId": "esr", "orderType": "Synthetic test", "resultIds": ["result"]}
        self.state["diagnosticResultsById"]["result"] = {"orderId": "order", "reportedFlag": "critical"}
        self.state["qaReviewsById"]["qa"] = {"dbqId": "dbq", "versionId": "signed"}
        self.state["dbqVersionsById"]["signed"] = {"dbqId": "dbq", "values": {"findings": "Signed findings."}}
        self.state["resultsPackagesById"]["pack"] = {"externalState": "not-enabled", "preparationStatus": "draft"}

    def test_each_stage_cites_only_its_selected_records(self):
        expected = {
            "Intake": "Intake record",
            "Medical Triage": "Synthetic source · page 2",
            "Scheduling": "Scheduling requirements and eligible options",
            "Clinical Evaluation": "Diagnostic source designation",
            "Internal QA": "Signed report field · findings",
            "VA Delivery & Review": "Selected results package",
        }
        baseline = json.dumps(self.state, sort_keys=True)
        with patch("app.services.stage_ai.run_engine", return_value={
            "detail": {"requirements": [], "candidates": [], "accessibilityNeeds": []}
        }):
            for stage, entity in self.ids.items():
                with self.subTest(stage=stage):
                    def complete(_schema_name, _schema, prompt, sources):
                        self.assertIn(stage, prompt)
                        serialized = json.dumps(sources)
                        self.assertNotIn("Unrelated record", serialized)
                        self.assertNotIn("Unrelated unsigned revision.", serialized)
                        reference = next(source["id"] for source in sources if source["label"] == expected[stage])
                        return json.dumps({"overview": {"text": "Synthetic review.", "source_ids": [reference]}, "review_points": []})

                    with patch("app.services.stage_ai.azure_review_completion", side_effect=complete):
                        result = generate_stage_review(self.state, stage, entity)
                    self.assertEqual([item["label"] for item in result["sources"]], [expected[stage]])
        self.assertEqual(json.dumps(self.state, sort_keys=True), baseline)

    def test_invalid_citations_and_wrong_stage_are_rejected(self):
        response = '{"overview":{"text":"Unsupported claim.","source_ids":["S999"]},"review_points":[]}'
        with patch("app.services.stage_ai.azure_review_completion", return_value=response):
            with self.assertRaisesRegex(HTTPException, "invalid or missing citations"):
                generate_stage_review(self.state, "Intake", "esr")
            with self.assertRaisesRegex(HTTPException, "open work item"):
                generate_stage_review(self.state, "Intake", "dbq")

    def test_delivery_accepts_more_than_six_verified_sources(self):
        sources = [{"id": f"S{i}", "label": f"Synthetic source {i}", "excerpt": "Package evidence."}
                   for i in range(1, 12)]
        ids = [source["id"] for source in sources]
        response = json.dumps({"overview": {"text": "Synthetic package remains blocked.", "source_ids": ids[::-1]},
                               "review_points": []})
        with patch("app.services.stage_ai.stage_sources", return_value=sources), patch(
            "app.services.stage_ai.azure_review_completion", return_value=response
        ) as complete:
            result = generate_stage_review(self.state, "VA Delivery & Review", "pack")
        self.assertEqual([source["id"] for source in result["sources"]], ids[::-1])
        schema = complete.call_args.args[1]
        self.assertEqual(schema["$defs"]["ReviewPoint"]["properties"]["source_ids"]["items"]["enum"], ids)

    def test_invalid_model_response_gets_one_repair_attempt(self):
        invalid = '{"overview":{"text":"Unsupported claim.","source_ids":["S999"]},"review_points":[]}'
        repaired = '{"overview":{"text":"Package is a draft.","source_ids":["S2"]},"review_points":[]}'
        with patch("app.services.stage_ai.azure_review_completion", side_effect=[invalid, repaired]) as complete:
            result = generate_stage_review(self.state, "VA Delivery & Review", "pack")
        self.assertEqual(complete.call_count, 2)
        self.assertEqual(result["sources"][0]["label"], "Selected results package")

    def test_intake_summary_uses_validation_readiness_instead_of_stored_workflow_status(self):
        self.state["esrsById"]["esr"]["intakeStatus"] = "requires-attention"

        def complete(_name, _schema, _prompt, sources):
            self.assertIn("Ready to validate", sources[0]["excerpt"])
            self.assertNotIn("requires-attention", json.dumps(sources))
            self.assertNotIn("queued", json.dumps(sources))
            return '{"overview":{"text":"Synthetic source available for review.","source_ids":["S1"]},"review_points":[]}'

        with patch("app.services.stage_ai.azure_review_completion", side_effect=complete):
            generate_stage_review(self.state, "Intake", "esr")

    def test_document_checklist_retries_an_incomplete_review(self):
        overview = {"text": "The document includes related evidence.", "source_ids": ["S3"]}
        review_points = [
            {"text": "The document is indexed.", "source_ids": ["S2"]},
            {"text": "Confirm the linked evidence matches the request.", "source_ids": ["S3"]},
        ]
        incomplete = json.dumps({"overview": overview, "review_points": []})
        complete = json.dumps({"overview": overview, "review_points": review_points})
        with patch("app.services.stage_ai.azure_review_completion", side_effect=[incomplete, complete]) as model:
            result = generate_stage_review(self.state, "Intake", "esr", document_checklist=True)
        self.assertEqual(model.call_count, 2)
        self.assertEqual(result["review_points"], review_points)
        self.assertEqual({source["id"] for source in result["sources"]}, {"S2", "S3"})

    def test_production_cannot_request_local_ai_review(self):
        user = CurrentUser(user_id="demo", display_name="Demo", role="scheduler")
        body = AIReviewRequest(stage="Clinical Evaluation", entityId="dbq")
        with patch("app.api.workspace.settings.app_env", "prod"), self.assertRaises(HTTPException) as error:
            ai_review(body, None, user)
        self.assertEqual(error.exception.status_code, 404)
