import os
import unittest
from pathlib import Path

from fastapi import HTTPException

from app.core.config import BACKEND_ROOT
from app.services.intake_processing import (
    COMMON_LAB_PATTERNS,
    detect_sections,
    extract_pages_with_ocr,
    extract_structured_lab_data,
    process_document,
)
from app.services.intake_store import (
    apply_intake_command,
    timestamp,
)
from app.services.intake_view import with_intake_views
from app.services.triage import with_triage_view
from app.services.workspace_store import empty_workspace


class IntakeEndToEndTests(unittest.TestCase):
    def setUp(self):
        self.state = empty_workspace()
        self.test_pdf = BACKEND_ROOT / "test_package" / "03_Combined_PDF" / "Mixed_Medical_Efolder_16_pages.pdf"
        self.assertTrue(self.test_pdf.exists(), f"Synthetic PDF not found at {self.test_pdf}")

    def test_full_intake_to_stage2_workflow(self):
        # 1. New ESR Received from EMS & 2. Load Patient Details & Contentions
        esr_id = "DEMO-ESR-2026-9001"
        claimant_name = "Jordan Sample"
        claimant_ssn = "SYN-CLAIM-0042"
        now = timestamp()

        self.state["esrsById"][esr_id] = {
            "id": esr_id,
            "claimantName": claimant_name,
            "claimantSsn": claimant_ssn,
            "intakeStatus": "in-progress",
            "priority": "routine",
            "receiptStatus": "received",
            "receivedAt": now,
            "aepRecordedAt": now,
            "contentionIds": ["contention-shoulder", "contention-skin"],
            "evidenceDocumentIds": [],
        }
        self.state["contentionsById"]["contention-shoulder"] = {
            "id": "contention-shoulder",
            "esrId": esr_id,
            "title": "Right Shoulder Strain",
            "status": "awaiting-review",
            "evidenceDocumentIds": [],
        }
        self.state["contentionsById"]["contention-skin"] = {
            "id": "contention-skin",
            "esrId": esr_id,
            "title": "Contact Dermatitis",
            "status": "awaiting-review",
            "evidenceDocumentIds": [],
        }
        self.state["workItemsById"]["Intake"] = {
            "id": "Intake",
            "esrId": esr_id,
            "stage": "Intake",
            "status": "in-progress",
            "entityId": esr_id,
            "priority": "routine",
        }

        # 3. Identify File Types and Store Source References
        doc_id = "doc-combined-16p"
        self.state["evidenceDocumentsById"][doc_id] = {
            "id": doc_id,
            "esrId": esr_id,
            "title": "Mixed_Medical_Efolder_16_pages.pdf",
            "sourceType": "pdf",
            "filePath": str(self.test_pdf),
            "fileSize": self.test_pdf.stat().st_size,
            "pageCount": 16,
            "classificationStatus": "queued",
            "category": "other",
            "sections": [],
        }
        self.state["esrsById"][esr_id]["evidenceDocumentIds"].append(doc_id)

        # 4 & 5. OCR & Text Extraction
        pdf_bytes = self.test_pdf.read_bytes()
        pages, ocr_applied, ocr_conf = extract_pages_with_ocr("Mixed_Medical_Efolder_16_pages.pdf", pdf_bytes)
        self.assertEqual(len(pages), 16)
        self.assertTrue(ocr_applied, "OCR should be detected for scanned pages")
        self.assertEqual(pages[16], "", "Page 16 should have empty text due to unreadable blurred scan")

        # 6, 7, 8, 9, 10, 11: LangGraph Workflow (Split, Classify, Extract Labs, Quality Checks)
        doc_result = process_document("Mixed_Medical_Efolder_16_pages.pdf", pdf_bytes, expected_claimant=claimant_name)
        self.assertGreaterEqual(doc_result["unreadablePages"], 1, "Unreadable blurred page should be detected")
        sections = doc_result["sections"]
        self.assertEqual(len(sections), 16, "Should segment into 16 logical documents")

        # Check Page 6 CBC lab extraction
        sec_cbc = next(s for s in sections if s.get("startPage") == 6)
        self.assertIn("labPanels", sec_cbc.get("extractedData", {}))
        cbc_panels = sec_cbc["extractedData"]["labPanels"]
        self.assertTrue(any("CBC" in p.get("panelName", "") or "Complete Blood Count" in p.get("panelName", "") for p in cbc_panels))

        # Check Page 7 Metabolic lab extraction
        sec_bmp = next(s for s in sections if s.get("startPage") == 7)
        self.assertIn("labPanels", sec_bmp.get("extractedData", {}))
        bmp_panels = sec_bmp["extractedData"]["labPanels"]
        self.assertTrue(any("Metabolic" in p.get("panelName", "") for p in bmp_panels))

        # Check Quality Flags:
        # Page 13: Duplicate of Page 6
        sec_13 = next(s for s in sections if s.get("startPage") == 13)
        self.assertTrue(sec_13.get("duplicateOf"), "Page 13 should be flagged as duplicate")

        # Page 14: Claimant mismatch
        sec_14 = next(s for s in sections if s.get("startPage") == 14)
        self.assertTrue(sec_14.get("claimantMismatch"), "Page 14 should have claimant mismatch")

        # Page 16: Unreadable
        sec_16 = next(s for s in sections if s.get("startPage") == 16)
        self.assertTrue(sec_16.get("unreadable"), "Page 16 should be unreadable")

        # Update document in state with processed sections
        self.state["evidenceDocumentsById"][doc_id]["sections"] = sections
        self.state["evidenceDocumentsById"][doc_id]["classificationStatus"] = "indexed"
        self.state["evidenceDocumentsById"][doc_id]["classifiedPageCount"] = sum(
            s.get("pageCount", 1) for s in sections if not s.get("unreadable")
        )
        self.state["evidenceDocumentsById"][doc_id]["unreadablePageCount"] = sum(
            s.get("pageCount", 1) for s in sections if s.get("unreadable")
        )

        # Associate document with contentions
        self.state["contentionsById"]["contention-shoulder"]["evidenceDocumentIds"].append(doc_id)
        self.state["contentionsById"]["contention-skin"]["evidenceDocumentIds"].append(doc_id)

        # Generate exceptions in intakeExceptionsById
        for s in sections:
            sec_id = s.get("sectionId")
            if s.get("claimantMismatch"):
                ex_id = f"issue-{doc_id}-{sec_id}-mismatch"
                self.state["intakeExceptionsById"][ex_id] = {
                    "id": ex_id,
                    "title": "Claimant Mismatch",
                    "esrId": esr_id,
                    "documentId": doc_id,
                    "sectionId": sec_id,
                    "exceptionType": "claimant_mismatch",
                    "severity": "critical",
                    "status": "open",
                    "createdAt": now,
                    "detail": f"Claimant mismatch on page {s.get('startPage')}: {s.get('detectedClaimant')}",
                }
            if s.get("unreadable"):
                ex_id = f"issue-{doc_id}-{sec_id}-unreadable"
                self.state["intakeExceptionsById"][ex_id] = {
                    "id": ex_id,
                    "title": "Unreadable Blurred Scan",
                    "esrId": esr_id,
                    "documentId": doc_id,
                    "sectionId": sec_id,
                    "exceptionType": "unreadable_page",
                    "severity": "critical",
                    "status": "open",
                    "createdAt": now,
                    "detail": f"Page {s.get('startPage')} is unreadable (blurred scan)",
                }
            if s.get("duplicateOf"):
                ex_id = f"issue-{doc_id}-{sec_id}-duplicate"
                self.state["intakeExceptionsById"][ex_id] = {
                    "id": ex_id,
                    "title": "Duplicate Document",
                    "esrId": esr_id,
                    "documentId": doc_id,
                    "sectionId": sec_id,
                    "exceptionType": "duplicate_document",
                    "severity": "warning",
                    "status": "open",
                    "createdAt": now,
                    "detail": f"Page {s.get('startPage')} is an exact duplicate of page {s.get('duplicateOf')}",
                }

        # 12. Show Organized Results in Intake View
        view_state = with_intake_views(self.state)
        esr_view = view_state["intake"]["viewsByEsrId"][esr_id]
        organizer = esr_view["aiOrganizer"]

        self.assertEqual(organizer["totalPhysicalFiles"], 1)
        self.assertEqual(organizer["totalLogicalDocs"], 15, "15 medical logical documents")
        self.assertEqual(len(organizer["logicalMetadataDocuments"]), 1, "1 ESR intake cover metadata document")
        self.assertEqual(organizer["totalLogicalDocs"] + len(organizer["logicalMetadataDocuments"]), 16, "16 total logical documents")
        self.assertGreater(organizer["actionRequiredCount"], 0)
        self.assertFalse(esr_view["canValidate"], "Intake must NOT be validatable while critical exceptions exist")
        self.assertIn("Resolve or escalate all open exceptions", esr_view["blocker"])

        # Attempt to validate prematurely -> must raise 422
        with self.assertRaises(HTTPException) as cm:
            apply_intake_command(self.state, "validateIntake", [esr_id], "demo-user")
        self.assertEqual(cm.exception.status_code, 422)

        # 13. Human Reviews and Resolves Exceptions
        # Staff updates category for Page 14; verify that updateDocumentCategory automatically resolves its mismatch exception
        apply_intake_command(
            self.state,
            "updateDocumentCategory",
            [f"{doc_id}::{sec_14.get('sectionId')}", "other"],
            "demo-user",
        )
        mismatch_issue = self.state["intakeExceptionsById"][f"issue-{doc_id}-{sec_14.get('sectionId')}-mismatch"]
        self.assertEqual(mismatch_issue["status"], "resolved", "Category correction must auto-resolve open section exceptions")

        # Staff reviews and disposition remaining exceptions
        apply_intake_command(
            self.state,
            "reviewIntakeException",
            [f"issue-{doc_id}-{sec_16.get('sectionId')}-unreadable", "resolved", "Staff requested clean re-scan; unreadable page acknowledged."],
            "demo-user",
        )
        apply_intake_command(
            self.state,
            "reviewIntakeException",
            [f"issue-{doc_id}-{sec_13.get('sectionId')}-duplicate", "resolved", "Duplicate acknowledged."],
            "demo-user",
        )

        # Re-check validation readiness
        view_state_after = with_intake_views(self.state)
        esr_view_after = view_state_after["intake"]["viewsByEsrId"][esr_id]
        self.assertTrue(esr_view_after["canValidate"], f"Intake should now be validatable. Blocker: {esr_view_after.get('blocker')}")

        # Staff clicks 'Validate and send to Triage'
        outcome = apply_intake_command(self.state, "validateIntake", [esr_id], "demo-user")
        self.assertEqual(outcome["state"]["esrsById"][esr_id]["intakeStatus"], "ready-for-triage")

        # 14. Stage 2 Medical Triage Handoff
        triage_state = with_triage_view(self.state)
        triage_rows = triage_state["triage"]["rows"]
        self.assertEqual(len(triage_rows), 2, "Both contentions should now be queued for Medical Triage")
        for row in triage_rows:
            self.assertTrue(row["readableEvidence"])
            self.assertEqual(row["esrId"], esr_id)

    def test_multiple_files_arrival_and_batch_intake(self):
        esr_id = "DEMO-ESR-BATCH-001"
        claimant_name = "Jordan Sample"
        now = timestamp()

        self.state["esrsById"][esr_id] = {
            "id": esr_id,
            "claimantName": claimant_name,
            "claimantSsn": "SYN-CLAIM-0042",
            "intakeStatus": "in-progress",
            "priority": "routine",
            "receiptStatus": "received",
            "receivedAt": now,
            "aepRecordedAt": now,
            "contentionIds": ["contention-knee"],
            "evidenceDocumentIds": [],
        }
        self.state["contentionsById"]["contention-knee"] = {
            "id": "contention-knee",
            "esrId": esr_id,
            "title": "Knee Osteoarthritis",
            "status": "awaiting-review",
            "evidenceDocumentIds": [],
        }
        self.state["workItemsById"]["Intake"] = {
            "id": "Intake",
            "esrId": esr_id,
            "stage": "Intake",
            "status": "in-progress",
            "entityId": esr_id,
            "priority": "routine",
        }

        test_files = [
            ("cbc.pdf", BACKEND_ROOT / "test_package" / "02_Source_Medical_Documents" / "05_laboratory_report__cbc.pdf"),
            ("imaging.pdf", BACKEND_ROOT / "test_package" / "02_Source_Medical_Documents" / "07_diagnostic_imaging_report.pdf"),
            ("scan_note.pdf", BACKEND_ROOT / "test_package" / "04_OCR_Scanned" / "Scanned_Clinic_Note_IMAGE_ONLY.pdf"),
            ("unreadable.pdf", BACKEND_ROOT / "test_package" / "05_Exception_Tests" / "Unreadable_Blurred_Scan.pdf"),
            ("dup_cbc.pdf", BACKEND_ROOT / "test_package" / "05_Exception_Tests" / "Exact_Duplicate_CBC.pdf"),
            ("wrong_patient.pdf", BACKEND_ROOT / "test_package" / "05_Exception_Tests" / "Wrong_Patient_Record.pdf"),
        ]

        for fname, fpath in test_files:
            self.assertTrue(fpath.exists(), f"Test file {fname} not found at {fpath}")
            doc_id = f"doc-{fname.replace('.', '-')}"
            content = fpath.read_bytes()
            res = process_document(fname, content, expected_claimant=claimant_name)
            
            self.state["evidenceDocumentsById"][doc_id] = {
                "id": doc_id,
                "esrId": esr_id,
                "title": fname,
                "filePath": str(fpath),
                "fileSize": len(content),
                "pageCount": res["pageCount"],
                "classificationStatus": "indexed",
                "category": res["category"],
                "sections": res["sections"],
                "classifiedPageCount": res["readablePages"],
                "unreadablePageCount": res["unreadablePages"],
            }
            self.state["esrsById"][esr_id]["evidenceDocumentIds"].append(doc_id)
            self.state["contentionsById"]["contention-knee"]["evidenceDocumentIds"].append(doc_id)

        view_state = with_intake_views(self.state)
        esr_view = view_state["intake"]["viewsByEsrId"][esr_id]
        organizer = esr_view["aiOrganizer"]

        self.assertEqual(organizer["totalPhysicalFiles"], 6)
        self.assertGreaterEqual(organizer["totalLogicalDocs"], 6)
        # Check category organization
        cats = {c["key"]: c["docCount"] for c in organizer["categories"]}
        self.assertGreaterEqual(cats.get("laboratory", 0), 1, "Should have lab document")
        self.assertGreaterEqual(cats.get("imaging", 0), 1, "Should have imaging document")


if __name__ == "__main__":
    unittest.main()
