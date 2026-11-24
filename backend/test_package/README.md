# MDE-NG Stage 1 Synthetic Test Package

**All records and identifiers are invented. No real patient data or actual VA records are included.**

## Recommended test sequence

1. Load `01_EMS_Request/synthetic_ems_esr.json` in a DEMO-only EMS import workflow, or create the synthetic ESR with ID `DEMO-ESR-2026-1001` in the app.
2. Upload `03_Combined_PDF/Mixed_Medical_Efolder_16_pages.pdf` into that ESR. The PDF contains 16 pages. All pages should be discovered as logical report segments, not categorized as a single document.
3. Verify that Stage 1 groups service records, VA-style outpatient record, private record, lab reports, imaging, prior DBQ-like worksheet, hospital note and other segments while retaining original page numbers.
4. Check the intentionally duplicated CBC segment, the wrong-patient page, the scanned clinic page, the blurred/unreadable scanned page and the uncertain note. Do not approve handoff while blocking issues remain.
5. Test `04_OCR_Scanned/Scanned_Clinic_Note_IMAGE_ONLY.pdf` separately. It contains image data, not selectable PDF text.
6. Test `05_Exception_Tests/` individually for duplicate, mismatched claimant, corrupt PDF, unsupported type and unreadable scan.
7. Cross-check extraction against the `06_Ground_Truth/` JSON and CSV files.

## What is inside

- `01_EMS_Request`: synthetic patient, ESR, XML and demo event JSON. XML is administrative intake data, not medical evidence.
- `02_Source_Medical_Documents`: 11 individual text-based PDFs organized as medical record examples.
- `03_Combined_PDF`: one multi-document PDF to test document boundary detection (16 pages; under 5 MB).
- `04_OCR_Scanned`: image-only PDF and PNG requiring OCR.
- `05_Exception_Tests`: exact duplicate CBC PDF, wrong-patient document, blurred image-only PDF/PNG, corrupted PDF and unsupported binary.
- `06_Ground_Truth`: expected categories, combined PDF page boundaries, extracted lab values, and expected exceptions.

## Notes for evaluation

- This is a synthetic functional test, not clinically validated training data or an official VA DBQ.
- Original source files must remain unchanged. Use logical segments and citation page ranges.
- A corrupted/unsupported upload may be rejected by the application; that is an acceptable, expected outcome if safely reported.
- The presence of these files does not prove every conceivable medical record was received; test only received-file integrity and available inventory.
- Stage 2 handoff must remain a manual authorized decision.
- Classification names are suggested ground truth; adapt mapping to approved taxonomy and review uncertain documents.
- Do **not** upload both the combined PDF and all source PDFs into the same ESR during the standard successful case test, as that intentionally creates many more duplicates.
