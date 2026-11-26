"""Regression & concurrency tests for Stage 2 AI summarization, schema repair, batching, router circuit breaker, and DB tables."""

from __future__ import annotations

import json
import os
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

BACKEND_ROOT = Path(__file__).resolve().parents[1]
if str(BACKEND_ROOT) not in sys.path:
    sys.path.insert(0, str(BACKEND_ROOT))

os.environ.setdefault("APP_ENV", "dev")

from sqlalchemy import create_engine, select
from sqlalchemy.orm import sessionmaker

from app.core.database import Base
from app.models import (
    OutboxJob,
    Stage2ClinicalFact,
    Stage2ContentionSummary,
    Stage2EvidenceVersion,
    Stage2ProcessingAudit,
    Stage2TriageDossier,
    Stage2TriageJob,
    WorkspaceSnapshot,
)
from app.services.intake_processing import AgenticModelRouter
from app.services.triage_pipeline import (
    CaseMedicalSummary,
    run_guarded_triage_llm,
    run_triage_pipeline_for_state,
)
from app.services.triage_store import (
    compute_current_esr_digest,
    merge_stage2_result_into_state,
    persist_stage2_normalized,
    register_or_reuse_triage_job,
)


def _sample_state() -> dict:
    doc_pages = [
        {
            "page": i,
            "text": (
                f"2023-01-{i:02d} Outpatient Orthopedic & Dermatology Visit. "
                f"Right knee flexion 105 degrees with painful motion (ICD-10 M25.561). "
                f"Chronic eczema plaque on forearm 4% BSA treated with triamcinolone."
            ),
        }
        for i in range(1, 10)
    ]
    return {
        "workspaceId": "mde-ng-demo-workspace",
        "revision": 1,
        "veteransById": {
            "vet-1": {"id": "vet-1", "displayName": "Morgan Q Demo", "serviceGroup": "Army"},
        },
        "esrsById": {
            "esr-1": {
                "id": "esr-1",
                "veteranId": "vet-1",
                "intakeStatus": "ready-for-triage",
                "priority": "routine",
                "evidenceDocumentIds": ["doc-1", "doc-unmapped"],
                "contentionIds": ["c-knee", "c-skin"],
            }
        },
        "evidenceDocumentsById": {
            "doc-1": {
                "id": "doc-1",
                "esrId": "esr-1",
                "title": "VA Medical Records",
                "sourceReference": "VA_Records.pdf",
                "category": "va-medical-records",
                "classificationStatus": "indexed",
                "sourceHash": "hash-1",
                "version": 1,
                "pageCount": len(doc_pages),
                "classifiedPageCount": len(doc_pages),
                "processing": {"allPages": doc_pages},
            },
            "doc-unmapped": {
                "id": "doc-unmapped",
                "esrId": "esr-1",
                "title": "Audiology Baseline",
                "sourceReference": "Audiology.pdf",
                "category": "va-medical-records",
                "classificationStatus": "indexed",
                "sourceHash": "hash-2",
                "version": 1,
                "pageCount": 1,
                "classifiedPageCount": 1,
                "processing": {"allPages": [{"page": 1, "text": "Normal baseline pure tone hearing thresholds bilaterally."}]},
            },
        },
        "contentionsById": {
            "c-knee": {
                "id": "c-knee",
                "esrId": "esr-1",
                "title": "Right knee condition",
                "requestedDbqNames": ["Knee and Lower Leg Conditions DBQ"],
                "status": "awaiting-review",
            },
            "c-skin": {
                "id": "c-skin",
                "esrId": "esr-1",
                "title": "Eczema",
                "requestedDbqNames": ["Skin Diseases DBQ"],
                "status": "awaiting-review",
            },
        },
        "clinicalFactsById": {},
        "triageDossiersByEsrId": {},
        "activitiesById": {},
    }


class Stage2ResilienceTests(unittest.TestCase):
    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        db_path = Path(self._tmp.name) / "test_stage2.sqlite3"
        self.engine = create_engine(f"sqlite:///{db_path}")
        Base.metadata.create_all(self.engine)
        self.Session = sessionmaker(bind=self.engine, autoflush=False, expire_on_commit=False)

    def tearDown(self) -> None:
        self.engine.dispose()
        self._tmp.cleanup()

    def test_long_narratives_and_many_citations_do_not_trigger_validation_error(self) -> None:
        long_overview = "Chronological medical summary. " * 60  # ~1800 chars (> old 900 cap)
        long_summary = "Detailed clinical findings for right knee. " * 40  # ~1720 chars (> old 700 cap)
        many_cites = [f"VA Medical Records · p. {i}" for i in range(1, 13)]
        payload = {
            "caseOverview": long_overview,
            "citationLabels": many_cites,
            "contentions": [
                {
                    "contentionId": "c-knee",
                    "clinicalSummary": long_summary,
                    "citationLabels": many_cites,
                }
            ],
        }
        parsed = CaseMedicalSummary.model_validate(payload)
        self.assertGreater(len(parsed.caseOverview), 900)
        self.assertGreater(len(parsed.contentions[0].clinicalSummary), 700)
        self.assertEqual(len(parsed.citationLabels), 12)

    def test_partial_recovery_preserves_valid_contention_and_runs_bounded_repair(self) -> None:
        state = _sample_state()
        dossier = run_triage_pipeline_for_state(state, "esr-1", allow_llm=False)
        self.assertIsNotNone(dossier)
        packets = {
            "c-knee": {"retrieval": {"matchedPages": [{"documentId": "doc-1", "documentTitle": "VA Medical Records", "page": 1, "excerpt": "Knee flexion 105 degrees."}], "matchedFacts": []}},
            "c-skin": {"retrieval": {"matchedPages": [{"documentId": "doc-1", "documentTitle": "VA Medical Records", "page": 2, "excerpt": "Eczema 4% BSA."}], "matchedFacts": []}},
        }
        calls: list[str] = []

        def fake_llm(schema_name, schema, instructions, sources, max_tokens=1200, timeout_seconds=None):
            calls.append(instructions)
            if len(calls) == 1:
                # Extra unknown key + missing c-skin -> triggers ValidationError recovery + 1 bounded repair
                return json.dumps({
                    "caseOverview": "Veteran has documented knee limitation (VA Medical Records · p. 1).",
                    "citationLabels": ["VA Medical Records · p. 1", "Hallucinated Doc · p. 99"],
                    "unexpectedField": "ignored-after-repair",
                    "contentions": [
                        {
                            "contentionId": "c-knee",
                            "clinicalSummary": "Right knee flexion 105 degrees (VA Medical Records · p. 1).",
                            "citationLabels": ["VA Medical Records · p. 1"],
                        }
                    ],
                })
            return json.dumps({
                "caseOverview": "Veteran has documented knee limitation (VA Medical Records · p. 1).",
                "citationLabels": ["VA Medical Records · p. 1"],
                "contentions": [
                    {
                        "contentionId": "c-knee",
                        "clinicalSummary": "Right knee flexion 105 degrees (VA Medical Records · p. 1).",
                        "citationLabels": ["VA Medical Records · p. 1"],
                    },
                    {
                        "contentionId": "c-skin",
                        "clinicalSummary": "Chronic eczema plaque 4% BSA (VA Medical Records · p. 2).",
                        "citationLabels": ["VA Medical Records · p. 2"],
                    },
                ],
            })

        with patch("app.services.triage_pipeline.settings.intake_provider", "azure-openai"), \
             patch("app.services.triage_pipeline.azure_review_completion", side_effect=fake_llm), \
             patch("app.services.triage_pipeline.get_last_completion_provider", return_value="claude-sonnet-4-6"):
            summary, provider, unavailable, audit = run_guarded_triage_llm(
                "Morgan Q Demo",
                [state["contentionsById"]["c-knee"], state["contentionsById"]["c-skin"]],
                packets,
            )

        self.assertFalse(unavailable)
        self.assertEqual(provider, "claude-sonnet-4-6")
        self.assertTrue(audit["repairAttempted"])
        self.assertEqual(len(calls), 2)
        self.assertNotIn("Hallucinated Doc · p. 99", summary.citationLabels)
        self.assertEqual(len(summary.contentions), 2)

    def test_stringified_contentions_and_dict_coercion_prevent_list_type_error(self) -> None:
        state = _sample_state()
        packets = {
            "c-knee": {"retrieval": {"matchedPages": [{"documentId": "doc-1", "documentTitle": "VA Medical Records", "page": 1, "excerpt": "Knee flexion 105 degrees."}], "matchedFacts": []}},
        }
        calls = 0

        def stringified_llm(schema_name, schema, instructions, sources, max_tokens=1200, timeout_seconds=None):
            nonlocal calls
            calls += 1
            return json.dumps({
                "caseOverview": "Veteran knee limitation (VA Medical Records · p. 1).",
                "citationLabels": json.dumps(["VA Medical Records · p. 1"]),
                "contentions": json.dumps([
                    {
                        "contentionId": "c-knee",
                        "clinicalSummary": "Flexion 105 degrees (VA Medical Records · p. 1).",
                        "citationLabels": ["VA Medical Records · p. 1"],
                    }
                ]),
            })

        with patch("app.services.triage_pipeline.settings.intake_provider", "azure-openai"), \
             patch("app.services.triage_pipeline.azure_review_completion", side_effect=stringified_llm), \
             patch("app.services.triage_pipeline.get_last_completion_provider", return_value="claude-sonnet-4-6"):
            summary, provider, unavailable, audit = run_guarded_triage_llm(
                "Morgan Q Demo",
                [state["contentionsById"]["c-knee"]],
                packets,
            )

        self.assertFalse(unavailable)
        self.assertEqual(calls, 1)  # Normalized in 1 pass without ValidationError or repair
        self.assertEqual(audit["validationDiagnostics"], [])
        self.assertEqual(len(summary.contentions), 1)
        self.assertEqual(summary.contentions[0].contentionId, "c-knee")

    def test_large_package_preserves_all_pages_via_batching_and_tracks_coverage(self) -> None:
        state = _sample_state()
        # Make 9 pages total > MAX_BATCH_INPUT_CHARS (60k) to trigger lossless batching
        for p in state["evidenceDocumentsById"]["doc-1"]["processing"]["allPages"]:
            p["text"] = f"{p['text']} " + ("Detailed clinical exam note for right knee flexion 105 degrees. " * 130)

        calls = 0

        def batch_llm(schema_name, schema, instructions, sources, max_tokens=1200, timeout_seconds=None):
            nonlocal calls
            calls += 1
            return json.dumps({
                "caseOverview": f"Batch {calls} overview.",
                "citationLabels": ["VA Medical Records · p. 1"],
                "contentions": [
                    {
                        "contentionId": s["contentionId"],
                        "clinicalSummary": f"Batch {calls} summary for {s['contentionId']}.",
                        "citationLabels": ["VA Medical Records · p. 1"],
                    }
                    for s in sources
                ],
            })

        with patch("app.services.triage_pipeline.settings.intake_provider", "azure-openai"), \
             patch("app.services.triage_pipeline.azure_review_completion", side_effect=batch_llm), \
             patch("app.services.triage_pipeline.get_last_completion_provider", return_value="azure-openai"):
            dossier = run_triage_pipeline_for_state(state, "esr-1", allow_llm=True, force=True)

        self.assertGreaterEqual(calls, 2)
        self.assertEqual(dossier["aiProvider"], "azure-openai")
        self.assertGreater(len(dossier["contentionsById"]["c-knee"]["matchedEvidence"]), 6)
        self.assertIn("evidenceCoverage", dossier)

    def test_circuit_breaker_recovers_after_cooldown_and_secondary_uses_native_tools(self) -> None:
        router = AgenticModelRouter()
        with patch.object(router, "_has_azure", return_value=True), \
             patch.object(router, "_has_secondary", return_value=True), \
             patch.object(router, "_call_azure", side_effect=RuntimeError("azure timeout")), \
             patch.object(router, "_call_secondary", return_value=('{"caseOverview":"ok","citationLabels":[],"contentions":[]}', "native-tool-schema")):
            out = router.execute("triage_case_summary", {}, "inst", [], max_tokens=1200)
        self.assertIn("caseOverview", out)
        self.assertEqual(router.get_circuit_state(), "open")
        self.assertEqual(router.get_last_audit()["structuredMode"], "native-tool-schema")

        # Expire cooldown -> circuit transitions to half-open and recovers Azure
        router.azure_circuit_open_until = 0.0
        with patch.object(router, "_has_azure", return_value=True), \
             patch.object(router, "_call_azure", return_value='{"caseOverview":"azure-ok","citationLabels":[],"contentions":[]}'):
            out2 = router.execute("triage_case_summary", {}, "inst", [], max_tokens=1200)
        self.assertIn("azure-ok", out2)
        self.assertEqual(router.get_circuit_state(), "closed")

    def test_normalized_tables_and_single_active_job_coordination(self) -> None:
        state = _sample_state()
        dossier = run_triage_pipeline_for_state(state, "esr-1", allow_llm=False)
        digest = compute_current_esr_digest(state, "esr-1")

        with self.Session() as db:
            db.add(WorkspaceSnapshot(id="mde-ng-demo-workspace", revision=1, state=state))
            j1, created1 = register_or_reuse_triage_job(db, "esr-1", digest)
            j2, created2 = register_or_reuse_triage_job(db, "esr-1", digest)
            self.assertTrue(created1)
            self.assertFalse(created2)
            self.assertEqual(j1.job_id, j2.job_id)

            facts = list((state.get("clinicalFactsById") or {}).values())
            persist_stage2_normalized(db, "esr-1", dossier, facts, document_count=2, is_stale=False)
            db.commit()

            self.assertEqual(db.get(Stage2TriageDossier, "esr-1").source_digest, digest)
            self.assertEqual(len(db.execute(select(Stage2ClinicalFact)).scalars().all()), len(facts))
            self.assertEqual(len(db.execute(select(Stage2ContentionSummary)).scalars().all()), 2)
            self.assertEqual(db.get(Stage2EvidenceVersion, "esr-1").fact_count, len(facts))
            self.assertEqual(len(db.execute(select(Stage2ProcessingAudit)).scalars().all()), 1)
            self.assertEqual(len(db.execute(select(Stage2TriageJob)).scalars().all()), 1)

    def test_merge_latest_state_preserves_other_esrs_and_marks_stale_on_digest_change(self) -> None:
        state_at_start = _sample_state()
        dossier = run_triage_pipeline_for_state(state_at_start, "esr-1", allow_llm=False)

        # Simulate concurrent revision bump on unrelated ESR + updated evidence on esr-1
        latest_state = _sample_state()
        latest_state["revision"] = 7
        latest_state["esrsById"]["esr-other"] = {"id": "esr-other", "veteranId": "vet-1"}
        merge_stage2_result_into_state(
            latest_state,
            "esr-1",
            state_at_start,
            dossier,
            "2026-11-26T22:00:00Z",
            mark_stale=True,
        )
        self.assertIn("esr-other", latest_state["esrsById"])
        self.assertEqual(latest_state["esrsById"]["esr-1"]["triageProcessing"]["status"], "stale")
        self.assertTrue(latest_state["esrsById"]["esr-1"]["triageProcessing"]["isStale"])


if __name__ == "__main__":
    unittest.main()
