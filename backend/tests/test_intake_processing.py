import io
import json
import unittest
from unittest.mock import patch

import httpx
from pydantic import SecretStr
from pypdf import PdfWriter
from pypdf.generic import DecodedStreamObject, DictionaryObject, NameObject

from app.core.config import Settings
from app.services import intake_processing
from app.services.intake_store import add_case
from app.services.intake_view import public_document, public_processing_message, with_intake_views
from app.services.workspace_store import empty_workspace


class IntakeProcessingTests(unittest.TestCase):
    def test_intake_prioritizes_named_cases_and_assigns_names_to_examples(self):
        state = empty_workspace()
        named = add_case(state, "Watson, Riley", ["Knee condition"], "reviewer", "2026-01-01T10:00:00Z")
        legacy = add_case(state, "Synthetic Intake Claimant", ["Knee condition"], "reviewer", "2026-01-01T11:00:00Z")
        example = add_case(state, None, ["Knee condition"], "reviewer", "2026-01-01T12:00:00Z")
        legacy["evidenceDocumentIds"].append("legacy-note")
        state["evidenceDocumentsById"]["legacy-note"] = {
            "id": "legacy-note", "sourceReference": "synthetic-service-note.txt", "classificationStatus": "queued",
            "esrId": legacy["id"], "pageCount": 0, "classifiedPageCount": 0, "unreadablePageCount": 0,
        }
        for esr in (named, legacy, example):
            state["workItemsById"][esr["id"]] = {"esrId": esr["id"], "stage": "Intake", "status": "queued"}

        result = with_intake_views(state)
        self.assertEqual(result["intake"]["queueIds"], [named["id"], example["id"], legacy["id"]])
        names = result["veteransById"]
        self.assertEqual(names[named["veteranId"]]["displayName"], "Watson, Riley")
        for esr in (legacy, example):
            self.assertRegex(names[esr["veteranId"]]["displayName"], r"^\w+, \w+ [A-Z]\.$")
        self.assertEqual(state["veteransById"][legacy["veteranId"]]["displayName"], "Synthetic Intake Claimant")
        self.assertEqual(names, with_intake_views(state)["veteransById"])

    def test_unavailable_ai_uses_local_document_checks_and_allows_retry(self):
        with patch.object(intake_processing.settings, "intake_provider", "azure-openai"), patch.object(
            intake_processing, "azure_suggest", side_effect=intake_processing.DocumentProcessingError("Unavailable")
        ):
            result = intake_processing.process_document("synthetic.txt", b"Synthetic radiology imaging report.")
        self.assertEqual(result["category"], "imaging")
        self.assertEqual(result["provider"], "local")
        self.assertEqual(result["excerpts"][0]["page"], 1)
        self.assertTrue(result["aiUnavailable"])
        self.assertTrue(result["retryable"])
        self.assertNotIn("error", result)

    def test_display_state_hides_legacy_provider_details(self):
        old = {"provider": "azure-openai", "deployment": "internal-deployment",
               "error": "Azure OpenAI could not find the configured deployment.", "status": "needs-review"}
        display = public_document({"id": "doc-1", "processing": old})["processing"]
        self.assertEqual(display["provider"], "ai")
        self.assertNotIn("deployment", display)
        self.assertEqual(display["error"], "AI review is temporarily unavailable. Try again later.")
        self.assertEqual(old["deployment"], "internal-deployment")
        self.assertEqual(public_processing_message("Page has no readable text layer."), "Page has no readable text layer.")

    def test_extracts_text_from_a_pdf_and_finds_blank_page(self):
        pdf = PdfWriter()
        page = pdf.add_blank_page(width=600, height=800)
        font = DictionaryObject({
            NameObject("/Type"): NameObject("/Font"),
            NameObject("/Subtype"): NameObject("/Type1"),
            NameObject("/BaseFont"): NameObject("/Helvetica"),
        })
        page[NameObject("/Resources")] = DictionaryObject({
            NameObject("/Font"): DictionaryObject({NameObject("/F1"): pdf._add_object(font)}),
        })
        stream = DecodedStreamObject()
        stream.set_data(b"BT /F1 12 Tf 70 720 Td (Synthetic radiology imaging report.) Tj ET")
        page[NameObject("/Contents")] = pdf._add_object(stream)
        pdf.add_blank_page(width=600, height=800)
        output = io.BytesIO()
        pdf.write(output)
        with patch.object(intake_processing.settings, "intake_provider", "local"):
            result = intake_processing.process_document("synthetic-radiology.pdf", output.getvalue())
        self.assertEqual((result["pageCount"], result["readablePages"], result["unreadablePages"]), (2, 1, 1))
        self.assertEqual(result["category"], "imaging")
        self.assertEqual(result["excerpts"][0]["page"], 1)

    def test_azure_uses_server_deployment_and_checks_citations(self):
        settings = Settings(
            _env_file=None, app_env="dev", intake_provider="azure-openai",
            azure_openai_endpoint="https://synthetic-example.openai.azure.com",
            azure_openai_deployment="intake-demo", azure_openai_api_key=SecretStr("test-key"),
        )
        original_client = httpx.Client
        sample = [{"page": 2, "text": "Synthetic laboratory record."}]

        def azure_response(request):
            self.assertEqual(str(request.url), "https://synthetic-example.openai.azure.com/openai/v1/chat/completions")
            self.assertEqual(request.headers["api-key"], "test-key")
            body = json.loads(request.content)
            self.assertEqual(body["model"], "intake-demo")
            self.assertEqual(body["response_format"]["json_schema"]["strict"], True)
            self.assertEqual(json.loads(body["messages"][1]["content"]), sample)
            return httpx.Response(200, json={"choices": [{"finish_reason": "stop", "message": {
                "content": json.dumps({"category": "laboratory", "summary": "Laboratory record.",
                                       "citation_pages": [cited_page]})
            }}]})

        transport = httpx.MockTransport(azure_response)
        with patch.object(intake_processing, "settings", settings), patch.object(
            intake_processing.httpx, "Client", lambda **kwargs: original_client(transport=transport, **kwargs)
        ):
            cited_page = 2
            self.assertEqual(intake_processing.azure_suggest(sample).citation_pages, [2])
            cited_page = 99
            with self.assertRaises(intake_processing.DocumentProcessingError):
                intake_processing.azure_suggest(sample)

    def test_document_manifest_verification_distinguishes_verified_unverified_and_discrepancy(self):
        state = empty_workspace()
        esr = add_case(state, "Test Veteran", ["Knee condition"], "reviewer", "2026-01-01T10:00:00Z")
        esr_id = esr["id"]
        state["workItemsById"][esr_id] = {"esrId": esr_id, "stage": "Intake", "status": "queued"}
        # Case 1: Without manifest -> Unverified
        esr.pop("documentManifest", None)
        view = with_intake_views(state)["intake"]["viewsByEsrId"][esr_id]
        self.assertEqual(view["manifestStatus"], "unverified")

        # Case 2: With manifest but missing expected documents -> Discrepancy
        esr["documentManifest"] = {
            "manifestId": "MNF-TEST-1",
            "source": "Prime EMS Manifest",
            "expectedDocuments": [
                {"id": "doc-1", "title": "Service Treatment Records", "expectedCategory": "service-treatment"},
                {"id": "doc-2", "title": "VAMC Clinical Notes", "expectedCategory": "va-medical-records"},
            ],
        }
        view = with_intake_views(state)["intake"]["viewsByEsrId"][esr_id]
        self.assertEqual(view["manifestStatus"], "discrepancy")
        self.assertEqual(len(view["missingDocuments"]), 2)

        # Case 3: When all expected documents are received -> Verified
        doc1_id = "doc-str"
        doc2_id = "doc-vamc"
        state["evidenceDocumentsById"][doc1_id] = {
            "id": doc1_id, "esrId": esr_id, "title": "Service Treatment Records",
            "sourceReference": "str.pdf", "category": "service-treatment",
            "pageCount": 10, "classifiedPageCount": 10, "unreadablePageCount": 0,
            "classificationStatus": "indexed",
        }
        state["evidenceDocumentsById"][doc2_id] = {
            "id": doc2_id, "esrId": esr_id, "title": "VAMC Clinical Notes",
            "sourceReference": "vamc.pdf", "category": "va-medical-records",
            "pageCount": 5, "classifiedPageCount": 5, "unreadablePageCount": 0,
            "classificationStatus": "indexed",
        }
        esr["evidenceDocumentIds"] = [doc1_id, doc2_id]
        view = with_intake_views(state)["intake"]["viewsByEsrId"][esr_id]
        self.assertEqual(view["manifestStatus"], "verified")
        self.assertEqual(len(view["missingDocuments"]), 0)

    def test_detect_sections_splits_combined_reports_in_document(self):
        pages = {
            1: "Active duty service treatment military medical examination.",
            2: "Active duty service treatment continuation record.",
            3: "Clinical laboratory specimen blood test CBC metabolic panel results.",
            4: "Radiology imaging report x-ray examination of right knee.",
        }
        sections = intake_processing.detect_sections(pages)
        self.assertGreaterEqual(len(sections), 3)
        categories = [s["category"] for s in sections]
        self.assertIn("service-treatment", categories)
        self.assertIn("laboratory", categories)
        self.assertIn("imaging", categories)

    def test_document_quality_checks_and_sha256(self):
        content = b"Military medical service treatment note for active duty claimant."
        result = intake_processing.process_document("str-report.txt", content)
        self.assertEqual(result["category"], "service-treatment")
        self.assertIn("quality", result)
        self.assertEqual(result["quality"]["fileIntegrity"], "passed")
        self.assertEqual(result["quality"]["readabilityScore"], 100)
        self.assertEqual(len(result["quality"]["sha256"]), 64)

