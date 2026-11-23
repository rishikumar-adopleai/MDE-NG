import unittest
from unittest.mock import Mock
from xml.etree import ElementTree

from fastapi import HTTPException
from sqlalchemy import create_engine, func, inspect, select
from sqlalchemy.orm import Session

from app.core.database import Base, engine as workspace_engine
from app.models import OutboxJob, ReferenceFile, WorkspaceSnapshot
from app.services.intake_store import add_case, apply_intake_command, refresh_intake_work
from app.services.intake_view import with_intake_views
from app.services.reference_store import reference_bytes
from app.services.triage import with_triage_view
from app.services.va_dbq_catalog import reference_forms
from app.services.va_exchange_records import exchange_records
from app.services.va_samples import SAMPLE_SCENARIOS, import_exam_request, parse_exam_request, read_bundled_request, scenario_files
from app.services.workspace_store import LEGACY_SNAPSHOT_ID, WORKSPACE_ID, empty_workspace, get_state, run_engine


class ExamManagementTests(unittest.TestCase):
    def setUp(self):
        if not inspect(workspace_engine).has_table(ReferenceFile.__tablename__):
            self.skipTest("Full-library integration requires a populated reference database.")
        with Session(workspace_engine) as db:
            if db.scalar(select(ReferenceFile.path).limit(1)) is None:
                self.skipTest("Full-library integration requires a populated reference database.")
        self.scenarios = scenario_files()
        if "basic" not in self.scenarios or "multiple-contentions" not in self.scenarios:
            self.fail("The test workspace database needs the Exam Management reference library.")

    def test_bundled_cases_can_share_an_event_id_and_keep_distinct_records(self):
        db = Mock()
        state = empty_workspace()
        imported = []
        for key in SAMPLE_SCENARIOS.keys() & self.scenarios.keys():
            with self.subTest(key=key):
                _, path = self.scenarios[key]
                content = reference_bytes(path)
                request = parse_exam_request(content)
                esr, created = import_exam_request(db, state, request, content, path.name, "reviewer")
                self.assertTrue(created)
                self.assertEqual(esr["sourceReference"], request.reference)
                self.assertEqual(len(esr["contentionIds"]), len(request.contentions))
                self.assertEqual(import_exam_request(db, state, request, content, path.name, "reviewer"), (esr, False))
                imported.append(esr)
        self.assertEqual(db.add.call_count, len(imported))
        self.assertEqual(len({item["evidenceDocumentIds"][0] for item in imported}), len(imported))

    def test_request_metadata_requires_evidence_and_preserves_requested_form(self):
        _, path = self.scenarios["multiple-contentions"]
        content = reference_bytes(path)
        state = empty_workspace()
        esr, _ = import_exam_request(Mock(), state, parse_exam_request(content), content, path.name, "reviewer")
        with self.assertRaisesRegex(HTTPException, "readable source"):
            apply_intake_command(state, "validateIntake", [esr["id"]], "reviewer")
        source = state["evidenceDocumentsById"][esr["evidenceDocumentIds"][0]]
        self.assertTrue(source["administrativeOnly"])
        self.assertTrue(with_intake_views(state)["intake"]["viewsByEsrId"][esr["id"]]["canAttach"])

        state["evidenceDocumentsById"]["review-source"] = {
            "id": "review-source", "esrId": esr["id"], "classifiedPageCount": 1, "pageCount": 1,
            "unreadablePageCount": 0, "classificationStatus": "indexed",
        }
        esr["evidenceDocumentIds"].append("review-source")
        for identifier in esr["contentionIds"]:
            state["contentionsById"][identifier]["evidenceDocumentIds"].append("review-source")
        apply_intake_command(state, "validateIntake", [esr["id"]], "reviewer")
        rows = with_triage_view(state)["triage"]["rows"]
        self.assertEqual(len(rows), 2)
        self.assertTrue(all(row["initialPlan"]["examCode"] == "knee" for row in rows))
        self.assertTrue(all(row["requestedDbqs"] == ["MUSC Knee and Lower Leg"] for row in rows))

    def test_unrelated_xml_is_rejected(self):
        with self.assertRaisesRegex(HTTPException, "Exam Management 4.7"):
            parse_exam_request(b"<ExamManagementEventNotification/>")

    def test_schema_location_hint_is_ignored(self):
        _, path = self.scenarios["basic"]
        original = reference_bytes(path)
        root = ElementTree.fromstring(original)
        hint = next(key for key in root.attrib if key.endswith("}schemaLocation"))
        root.set(hint, "urn:unused missing-schema.xsd")
        self.assertEqual(parse_exam_request(ElementTree.tostring(root)), parse_exam_request(original))

    def test_workspace_load_adds_missing_bundled_requests_once(self):
        engine = create_engine("sqlite://")
        Base.metadata.create_all(engine, tables=[WorkspaceSnapshot.__table__, OutboxJob.__table__])
        with Session(engine) as db:
            state = empty_workspace()
            state["revision"] = 7
            existing = add_case(state, "Existing Claimant", ["Knee condition"], "reviewer", "2025-01-01T00:00:00Z")
            refresh_intake_work(state, existing["id"], existing["receivedAt"])
            example = add_case(state, None, ["Knee condition"], "reviewer", "2025-01-01T00:00:00Z")
            refresh_intake_work(state, example["id"], example["receivedAt"])
            _, path = self.scenarios["basic"]
            original = reference_bytes(path)
            received, _ = import_exam_request(db, state, parse_exam_request(original), original, path.name, "reviewer")
            db.add(WorkspaceSnapshot(id=WORKSPACE_ID, revision=7, state=state))
            db.commit()

            loaded = get_state(db, backfill_requests=True)
            refs = {read_bundled_request(path)[0].reference for _, path in self.scenarios.values()}
            self.assertEqual(loaded["revision"], 8)
            self.assertEqual(len(loaded["esrsById"]), len(refs) + 1)
            self.assertEqual(loaded["esrsById"][existing["id"]], existing)
            self.assertEqual(loaded["esrsById"][received["id"]], received)
            self.assertNotIn(example["id"], loaded["esrsById"])
            self.assertIn(example["id"], db.get(WorkspaceSnapshot, LEGACY_SNAPSHOT_ID).state["esrsById"])
            queue = with_intake_views(loaded)["intake"]["queueIds"]
            self.assertEqual(queue[-1], existing["id"])
            self.assertEqual(get_state(db, backfill_requests=True)["revision"], 8)
            self.assertEqual(db.scalar(select(func.count()).select_from(OutboxJob)), len(refs))
            references = [doc for doc in loaded["evidenceDocumentsById"].values() if doc["id"].startswith("exchange-document-")]
            if "document-manifest" in self.scenarios:
                self.assertEqual(len(references), 20)
            self.assertTrue(all(doc["administrativeOnly"] and doc["classifiedPageCount"] == 0 for doc in references))

    def test_stage_sources_keep_unmatched_events_unassigned_and_forms_as_references(self):
        for stage in ("Medical Triage", "Scheduling", "Clinical Evaluation", "VA Delivery & Review"):
            with self.subTest(stage=stage):
                events = exchange_records(stage)
                self.assertTrue(events)
                self.assertTrue(all(event["facts"].get("Request reference") for event in events))
                self.assertTrue(all(not event["claimant"] for event in events if "Request link" in event["facts"]))
        forms = reference_forms()
        if forms:
            diabetes = next(form for form in forms if form["revision"] == 2631)
            self.assertGreater(diabetes["fieldCount"], 0)
            self.assertGreater(diabetes["ruleCount"], 0)
            self.assertTrue(diabetes["sections"])

    def test_practice_cases_activate_each_queue_and_keep_availability_separate(self):
        from app.services.va_practice import EXERCISES, PRACTICE_COHORT

        engine = create_engine("sqlite://")
        Base.metadata.create_all(engine, tables=[WorkspaceSnapshot.__table__, OutboxJob.__table__])
        with Session(engine) as db:
            legacy = empty_workspace()
            legacy_case = add_case(legacy, "Prior Practice Case", ["Knee condition"], "reviewer", "2025-01-01T00:00:00Z")
            legacy_case["practiceScenario"] = {"cohort": "exchange-workflow-1"}
            legacy["practiceCohort"] = "exchange-workflow-1"
            db.add(WorkspaceSnapshot(id=WORKSPACE_ID, revision=0, state=legacy))
            db.commit()
            state = get_state(db, backfill_requests=True, load_practice=True)
            self.assertEqual(state["esrsById"][legacy_case["id"]], legacy_case)
            practice = {case["id"]: case["practiceScenario"] for case in state["esrsById"].values()
                        if case.get("practiceScenario", {}).get("cohort") == PRACTICE_COHORT}
            self.assertEqual({case["name"] for case in practice.values()}, {"Knee and Lower Leg", "Hearing Loss and Tinnitus"})
            self.assertEqual(len(practice), 48)
            for stage, expected in EXERCISES.items():
                for name in {case["name"] for case in practice.values()}:
                    with self.subTest(stage=stage, scenario=name):
                        ids = {item["esrId"] for item in state["workItemsById"].values()
                               if item["stage"] == stage and item["status"] not in {"completed", "cancelled"}
                               and practice.get(item["esrId"], {}).get("name") == name}
                        self.assertEqual(len(ids), 5 if stage == "Scheduling" else 4)
                        self.assertTrue(set(expected) <= {practice[identifier]["exercise"] for identifier in ids})
            pending = {pack["esrId"] for pack in state["resultsPackagesById"].values()
                       if pack.get("simulatedResponse") == "awaiting-response"}
            self.assertEqual(pending, {key for key, case in practice.items() if case["exercise"] == "Awaiting response"})
            self.assertEqual(get_state(db, backfill_requests=True, load_practice=True)["revision"], state["revision"])
            work = next(item for item in state["workItemsById"].values() if item["stage"] == "Scheduling"
                        and item["entityType"] == "examination-requirement")
            payload = {"state": state, "action": "$stage-view", "args": [work["id"]]}
            self.assertTrue(run_engine(payload)["detail"]["candidates"])
            state["esrsById"][work["esrId"]].pop("practiceScenario")
            self.assertFalse(run_engine(payload)["detail"]["candidates"])
