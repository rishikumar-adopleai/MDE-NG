"""
Stage 1 Intake Agent Package
Provides:
  - parser: Pure VA Examination Request XML parsing and ESR initialization
  - processor: OCR, 21-category clinical classification, document integrity, claimant matching
  - store: Case attachment, duplicate checksum checking, outbox job dispatch
  - view: AI Document Organizer aggregation and triage handoff validation
"""

from app.agents.intake.parser import (
    ContentionRequest,
    ExamRequest,
    MAX_XML_BYTES,
    import_exam_request,
    parse_exam_request,
)
from app.services.intake_processing import (
    process_document,
    detect_sections,
    classify_page_text,
    check_claimant_match,
)
from app.services.intake_store import (
    queue_document,
    apply_intake_command,
    refresh_intake_work,
)
from app.services.intake_view import (
    with_intake_views,
    has_readable_evidence,
)

__all__ = [
    "ContentionRequest",
    "ExamRequest",
    "MAX_XML_BYTES",
    "import_exam_request",
    "parse_exam_request",
    "process_document",
    "detect_sections",
    "classify_page_text",
    "check_claimant_match",
    "queue_document",
    "apply_intake_command",
    "refresh_intake_work",
    "with_intake_views",
    "has_readable_evidence",
]
