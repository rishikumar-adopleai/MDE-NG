import os
import unittest
from pathlib import Path

from fastapi import HTTPException
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from app.core.config import BACKEND_ROOT
from app.core.database import Base
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
        self.engine = create_engine("sqlite:///:memory:")
        Base.metadata.create_all(self.engine)
        self.Session = sessionmaker(autocommit=False, autoflush=False, bind=self.engine)
        self.db = self.Session()
        self.addCleanup(self.db.close)
        self.addCleanup(self.engine.dispose)

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

    def test_va_instructions_and_duplicate_detection(self):
        esr_id = "DEMO-ESR-VA-001"
        claimant_name = "Marcus Vance"
        now = timestamp()

        self.state["veteransById"]["vet-marcus"] = {
            "id": "vet-marcus",
            "displayName": claimant_name,
            "claimIdentifier": "CLAIM-VA-9901",
            "serviceGroup": "Air Force",
        }
        self.state["esrsById"][esr_id] = {
            "id": esr_id,
            "veteranId": "vet-marcus",
            "claimantName": claimant_name,
            "intakeStatus": "in-progress",
            "priority": "routine",
            "receiptStatus": "received",
            "receivedAt": now,
            "aepRecordedAt": now,
            "contentionIds": ["contention-ptsd"],
            "evidenceDocumentIds": [],
            "specialInstructions": "Evaluate for current level of severity of service connected PTSD. Review all military treatment records.",
        }
        self.state["contentionsById"]["contention-ptsd"] = {
            "id": "contention-ptsd",
            "esrId": esr_id,
            "title": "Post Traumatic Stress Disorder",
            "status": "awaiting-review",
            "requestedDbqNames": ["PSYCH PTSD (Review)"],
            "evidenceDocumentIds": [],
        }
        self.state["workItemsById"]["work-intake-va-001"] = {
            "id": "work-intake-va-001",
            "esrId": esr_id,
            "stage": "Intake",
            "status": "in-progress",
            "entityId": esr_id,
            "priority": "routine",
        }

        # Check that with_intake_views generates rich vaInstructions
        view_state = with_intake_views(self.state)
        esr_view = view_state["intake"]["viewsByEsrId"][esr_id]
        self.assertIn("vaInstructions", esr_view)
        va_inst = esr_view["vaInstructions"]
        self.assertEqual(va_inst["claimantName"], claimant_name)
        self.assertEqual(va_inst["serviceBranch"], "Air Force")
        self.assertIn("PSYCH PTSD (Review)", va_inst["requestedDbqs"])
        self.assertIn("Evaluate for current level of severity", va_inst["specialInstructions"])
        self.assertIn("isSimulated", va_inst)

        # Verify that triage view automatically receives these instructions and simulation flag
        from app.services.triage import with_triage_view
        triage_state = with_triage_view(view_state)
        triage_rows = triage_state["triage"]["rows"]
        matching_row = next((r for r in triage_rows if r["esrId"] == esr_id), None)
        if matching_row:
            self.assertEqual(matching_row["specialInstructions"], va_inst["specialInstructions"])
            self.assertEqual(matching_row["serviceBranch"], "Air Force")
            self.assertIn("isSimulated", matching_row)

        # Verify no hardcoded default instructions when none provided
        self.state["esrsById"][esr_id]["specialInstructions"] = ""
        view_state_empty = with_intake_views(self.state)
        va_inst_empty = view_state_empty["intake"]["viewsByEsrId"][esr_id]["vaInstructions"]
        self.assertEqual(va_inst_empty["specialInstructions"], "")

    def test_folder_and_zip_unpacking(self):
        import io
        import zipfile
        from starlette.datastructures import UploadFile
        from app.api.intake import unpack_upload_files

        # Create an in-memory zip representing a medical folder with 3 PDFs and a nested file
        buf = io.BytesIO()
        with zipfile.ZipFile(buf, "w") as zf:
            zf.writestr("01_Orthopedic/xray_report.pdf", b"%PDF-1.4 simulated xray")
            zf.writestr("01_Orthopedic/lab_blood_work.pdf", b"%PDF-1.4 simulated lab")
            zf.writestr("01_Orthopedic/str_service_records.pdf", b"%PDF-1.4 simulated str")
            zf.writestr("01_Orthopedic/.DS_Store", b"junk")
            zf.writestr("01_Orthopedic/__MACOSX/._xray_report.pdf", b"mac-junk")

        buf.seek(0)
        zip_upload = UploadFile(filename="01_Orthopedic.zip", file=buf)
        extracted = unpack_upload_files(zip_upload, None, synthetic=True)

        self.assertEqual(len(extracted), 3, "Should unpack all 3 valid medical files, skipping system metadata")
        extracted_names = [fn for fn, _ in extracted]
        self.assertIn("xray_report.pdf", extracted_names)
        self.assertIn("lab_blood_work.pdf", extracted_names)
        self.assertIn("str_service_records.pdf", extracted_names)

        # Test multi-file list upload (as when folder is selected in browser)
        f1 = UploadFile(filename="01_Orthopedic/exam.pdf", file=io.BytesIO(b"%PDF-1.4 exam"))
        f2 = UploadFile(filename="01_Orthopedic/notes.txt", file=io.BytesIO(b"clinical notes"))
        extracted_multi = unpack_upload_files(None, [f1, f2], synthetic=True)
        self.assertEqual(len(extracted_multi), 2)
        multi_names = [fn for fn, _ in extracted_multi]
        self.assertIn("exam.pdf", multi_names)
        self.assertIn("notes.txt", multi_names)

    def test_xml_and_medical_pdf_bundle_upload(self):
        import io
        from starlette.datastructures import UploadFile
        from app.api.intake import receive_exam_management_upload
        from app.core.security import CurrentUser
        from app.agents.intake.parser import scenario_files, read_bundled_request

        db = self.db
        user = CurrentUser(user_id="test-staff", display_name="Test Staff", role="intake")

        scenarios = scenario_files()
        if scenarios:
            _, path = next(iter(scenarios.values()))
            loaded = read_bundled_request(path)
            self.assertIsNotNone(loaded)
            _, raw_xml = loaded
            import re
            from uuid import uuid4
            unique_ref = str(uuid4())
            xml_content = re.sub(b'examSchedulingRequestUuid="[^"]+"', f'examSchedulingRequestUuid="{unique_ref}"'.encode(), raw_xml)
        else:
            from uuid import uuid4
            unique_ref = f"TEST-{uuid4().hex[:8].upper()}"
            xml_content = f"""<?xml version="1.0" encoding="UTF-8"?>
<ExamSchedulingRequest xmlns="http://va.gov/service/spec/esr/4.7">
    <SchedulingRequestUniqueIdentifier>{unique_ref}</SchedulingRequestUniqueIdentifier>
    <Claim>
        <Veteran>
            <PersonName>
                <LastName>Smith</LastName>
                <FirstName>John</FirstName>
            </PersonName>
        </Veteran>
        <Contention>
            <ContentionName>Right knee condition</ContentionName>
            <DBQ>
                <DBQName>Knee and Lower Leg</DBQName>
            </DBQ>
        </Contention>
    </Claim>
</ExamSchedulingRequest>""".encode("utf-8")

        xml_upload = UploadFile(filename="ESR_Request.xml", file=io.BytesIO(xml_content))
        pdf_upload = UploadFile(filename="service_records.pdf", file=io.BytesIO(b"%PDF-1.4 simulated records"))

        res = receive_exam_management_upload(
            file=xml_upload,
            files=[pdf_upload],
            synthetic=True,
            user=user,
            db=db
        )
        self.assertIn("esrId", res)
        self.assertEqual(len(res.get("documentIds", [])), 1, "The accompanying medical PDF should be queued into the ESR")

    def test_batch_upload_with_duplicate_files(self):
        import io
        from starlette.datastructures import UploadFile
        from app.api.intake import create_esr
        from app.core.security import CurrentUser

        db = self.db
        user = CurrentUser(user_id="test-staff", display_name="Test Staff", role="intake")

        # Two files with identical bytes (simulating exact duplicate in a 26-file folder)
        identical_bytes = b"%PDF-1.4 identical duplicate content"
        f1 = UploadFile(filename="05_laboratory_report__cbc.pdf", file=io.BytesIO(identical_bytes))
        f2 = UploadFile(filename="Exact_Duplicate_CBC.pdf", file=io.BytesIO(identical_bytes))
        f3 = UploadFile(filename="01_service_treatment_record.pdf", file=io.BytesIO(b"%PDF-1.4 unique content"))

        # Should NOT raise 409, but queue all files and record an actionable exception for the duplicate
        res = create_esr(
            file=f1,
            files=[f2, f3],
            name="Morgan Batch Test",
            contentions="Knee Condition",
            synthetic=True,
            user=user,
            db=db,
        )
        self.assertIn("esrId", res)
        self.assertEqual(len(res.get("documentIds", [])), 3, "All 3 files in the folder should be accepted without crashing")

        state = res["state"]
        esr_id = res["esrId"]
        dup_exceptions = [e for e in state.get("intakeExceptionsById", {}).values() if e.get("esrId") == esr_id and e.get("kind") == "duplicate-document"]
        self.assertEqual(len(dup_exceptions), 1, "Should create an open duplicate exception for staff review")

    def test_vbms_attachment_f_xml_parsing(self):
        import io
        from starlette.datastructures import UploadFile
        from app.api.intake import inspect_esr, create_esr
        from app.core.database import SessionLocal
        from app.core.security import CurrentUser

        xml_data = b"""<?xml version="1.0" encoding="UTF-8"?>
<ExamSchedulingRequest synthetic="true" schemaStatus="NotValidatedAgainstVAIEPD">
  <SchedulingRequestUniqueIdentifier>TEST-ESR-015</SchedulingRequestUniqueIdentifier>
  <SpecialInstructions>Review the attached records before scheduling.</SpecialInstructions>
  <Claim>
    <ProgramTypeCode source="simulation">CPL</ProgramTypeCode>
    <ClaimLabel source="simulation">Disability Compensation</ClaimLabel>
    <Veteran>
      <FileNumber>000000015</FileNumber>
      <VeteranName><FirstName>Emerson</FirstName><MiddleName>Q</MiddleName><LastName>Example</LastName></VeteranName>
      <VeteranDateOfBirth>2000-02-25</VeteranDateOfBirth>
      <PeriodOfService><BranchOfService>Coast Guard</BranchOfService></PeriodOfService>
    </Veteran>
  </Claim>
  <Contention>
    <ContentionUniqueIdentifier>TEST-015-C01</ContentionUniqueIdentifier>
    <ContentionName>Right knee condition</ContentionName>
    <DBQ source="simulation"><Name>Knee and Lower Leg</Name></DBQ>
  </Contention>
  <Contention>
    <ContentionUniqueIdentifier>TEST-015-C02</ContentionUniqueIdentifier>
    <ContentionName>Bilateral hearing loss</ContentionName>
    <DBQ source="simulation"><Name>Hearing Loss and Tinnitus</Name></DBQ>
  </Contention>
  <Contention>
    <ContentionUniqueIdentifier>TEST-015-C03</ContentionUniqueIdentifier>
    <ContentionName>irritant contact dermatitis</ContentionName>
    <DBQ source="simulation"><Name>Skin Diseases</Name></DBQ>
  </Contention>
</ExamSchedulingRequest>"""

        db = self.db
        user = CurrentUser(user_id="test-staff", display_name="Test Staff", role="intake")

        xml_file = UploadFile(filename="ESR_Request.xml", file=io.BytesIO(xml_data))
        pdf_file = UploadFile(filename="SF_600.pdf", file=io.BytesIO(b"%PDF-1.4 sample clinical evidence"))

        # 1. Test inspection
        insp = inspect_esr(file=pdf_file, files=[xml_file], db=db)
        self.assertTrue(insp["examManagementRequest"])
        self.assertEqual(insp["claimantName"], "Emerson Q Example")
        self.assertEqual(insp["esrReference"], "TEST-ESR-015")
        self.assertEqual(insp["serviceBranch"], "Coast Guard")
        self.assertEqual(len(insp["contentions"]), 3)
        self.assertEqual(insp["contentions"][0]["title"], "Right knee condition")
        self.assertIn("Knee and Lower Leg", insp["contentions"][0]["forms"])
        self.assertTrue(insp["isSimulated"])

        # 2. Test ESR creation
        xml_file.file.seek(0)
        pdf_file.file.seek(0)
        created = create_esr(file=xml_file, files=[pdf_file], db=db, user=user)
        self.assertIn("esrId", created)

        # Check intake view
        esr_id = created["esrId"]
        state = created["state"]
        view = state["intake"]["viewsByEsrId"][esr_id]
        va_inst = view["vaInstructions"]
        self.assertEqual(va_inst["claimantName"], "Emerson Q Example")
        self.assertEqual(va_inst["serviceBranch"], "Coast Guard")
        self.assertEqual(va_inst["specialInstructions"], "Review the attached records before scheduling.")
        self.assertEqual(len(va_inst["contentions"]), 3)
        self.assertIn("Knee and Lower Leg", va_inst["contentions"][0]["dbqs"])
        self.assertTrue(va_inst["isSimulated"])


if __name__ == "__main__":
    unittest.main()
