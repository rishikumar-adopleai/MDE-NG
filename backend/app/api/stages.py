from fastapi import APIRouter, Depends, HTTPException

from app.core.security import CurrentUser, get_current_user

router = APIRouter(tags=["Workflow placeholders"])


def pending_workflow(user: CurrentUser = Depends(get_current_user)) -> None:
    """Visible placeholder until stage rules and persistence operations are implemented."""
    raise HTTPException(status_code=501, detail="Workflow operation is scaffolded but not implemented yet.")


@router.post("/mock-va/esr", status_code=501, dependencies=[Depends(pending_workflow)])
def receive_esr_placeholder() -> None:
    """Future intake route: persist ESR and AEP before enqueueing document processing."""


@router.get("/esrs/{esr_id}/documents", status_code=501, dependencies=[Depends(pending_workflow)])
def intake_documents_placeholder(esr_id: str) -> None:
    pass


@router.get("/esrs/{esr_id}/processing", status_code=501, dependencies=[Depends(pending_workflow)])
def intake_processing_placeholder(esr_id: str) -> None:
    pass


@router.get("/documents/{document_id}/pages/{page_no}", status_code=501, dependencies=[Depends(pending_workflow)])
def document_page_placeholder(document_id: str, page_no: int) -> None:
    pass


@router.post("/esrs/{esr_id}/exceptions/{exception_id}/disposition", status_code=501, dependencies=[Depends(pending_workflow)])
def intake_exception_placeholder(esr_id: str, exception_id: str) -> None:
    pass


@router.get("/esrs/{esr_id}/handoff/triage", status_code=501, dependencies=[Depends(pending_workflow)])
def intake_handoff_placeholder(esr_id: str) -> None:
    pass


@router.post("/esrs/{esr_id}/intake/complete", status_code=501, dependencies=[Depends(pending_workflow)])
def complete_intake_placeholder(esr_id: str) -> None:
    pass


@router.get("/esrs/{esr_id}/evidence/search", status_code=501, dependencies=[Depends(pending_workflow)])
def evidence_search_placeholder(esr_id: str, q: str = "") -> None:
    pass


@router.get("/contentions/{contention_id}/summary", status_code=501, dependencies=[Depends(pending_workflow)])
def contention_summary_placeholder(contention_id: str) -> None:
    pass


@router.get("/contentions/{contention_id}/dbq-suggestions", status_code=501, dependencies=[Depends(pending_workflow)])
def dbq_suggestions_placeholder(contention_id: str) -> None:
    pass


@router.put("/work-items/{work_item_id}/plan", status_code=501, dependencies=[Depends(pending_workflow)])
def save_triage_plan_placeholder(work_item_id: str) -> None:
    pass


@router.post("/work-items/{work_item_id}/plan/approve", status_code=501, dependencies=[Depends(pending_workflow)])
def approve_triage_plan_placeholder(work_item_id: str) -> None:
    pass


@router.get("/work-items/{work_item_id}/examiners", status_code=501, dependencies=[Depends(pending_workflow)])
def list_examiners_placeholder(work_item_id: str) -> None:
    pass


@router.get("/examiners/{examiner_id}/availability", status_code=501, dependencies=[Depends(pending_workflow)])
def examiner_availability_placeholder(examiner_id: str, from_date: str = "", to_date: str = "") -> None:
    pass


@router.post("/work-items/{work_item_id}/outreach", status_code=501, dependencies=[Depends(pending_workflow)])
def record_outreach_placeholder(work_item_id: str) -> None:
    pass


@router.get("/work-items/{work_item_id}/booking-checks", status_code=501, dependencies=[Depends(pending_workflow)])
def booking_checks_placeholder(work_item_id: str) -> None:
    pass


@router.post("/work-items/{work_item_id}/appointment/confirm", status_code=501, dependencies=[Depends(pending_workflow)])
def confirm_appointment_placeholder(work_item_id: str) -> None:
    pass


@router.get("/work-items/{work_item_id}/clinical", status_code=501, dependencies=[Depends(pending_workflow)])
def get_clinical_workspace_placeholder(work_item_id: str) -> None:
    pass


@router.get("/dbq-definitions/{code}", status_code=501, dependencies=[Depends(pending_workflow)])
def dbq_definition_placeholder(code: str) -> None:
    pass


@router.put("/reports/{report_id}/findings", status_code=501, dependencies=[Depends(pending_workflow)])
def save_report_findings_placeholder(report_id: str) -> None:
    pass


@router.post("/reports/{report_id}/validate", status_code=501, dependencies=[Depends(pending_workflow)])
def validate_report_placeholder(report_id: str) -> None:
    pass


@router.get("/reports/{report_id}/suggestions", status_code=501, dependencies=[Depends(pending_workflow)])
def report_suggestions_placeholder(report_id: str) -> None:
    pass


@router.post("/work-items/{work_item_id}/dbq-change", status_code=501, dependencies=[Depends(pending_workflow)])
def dbq_change_placeholder(work_item_id: str) -> None:
    pass


@router.post("/reports/{report_id}/sign", status_code=501, dependencies=[Depends(pending_workflow)])
def sign_report_placeholder(report_id: str) -> None:
    pass


@router.get("/qa/queue", status_code=501, dependencies=[Depends(pending_workflow)])
def qa_queue_placeholder() -> None:
    pass


@router.get("/reports/{report_id}/versions", status_code=501, dependencies=[Depends(pending_workflow)])
def report_versions_placeholder(report_id: str) -> None:
    pass


@router.get("/reports/{report_id}/compare", status_code=501, dependencies=[Depends(pending_workflow)])
def compare_report_versions_placeholder(report_id: str, a: str = "", b: str = "") -> None:
    pass


@router.get("/reports/{report_id}/qa-checks", status_code=501, dependencies=[Depends(pending_workflow)])
def qa_checks_placeholder(report_id: str) -> None:
    pass


@router.post("/qa/findings/{finding_id}/disposition", status_code=501, dependencies=[Depends(pending_workflow)])
def qa_finding_disposition_placeholder(finding_id: str) -> None:
    pass


@router.post("/reports/{report_id}/return", status_code=501, dependencies=[Depends(pending_workflow)])
def return_report_placeholder(report_id: str) -> None:
    pass


@router.post("/reports/{report_id}/approve", status_code=501, dependencies=[Depends(pending_workflow)])
def approve_report_placeholder(report_id: str) -> None:
    pass


@router.get("/esrs/{esr_id}/package", status_code=501, dependencies=[Depends(pending_workflow)])
def get_package_placeholder(esr_id: str) -> None:
    pass


@router.post("/esrs/{esr_id}/package/validate", status_code=501, dependencies=[Depends(pending_workflow)])
def validate_package_placeholder(esr_id: str) -> None:
    pass


@router.post("/esrs/{esr_id}/package/transmit", status_code=501, dependencies=[Depends(pending_workflow)])
def transmit_package_placeholder(esr_id: str) -> None:
    pass


@router.post("/mock-va/responses", status_code=501, dependencies=[Depends(pending_workflow)])
def mock_va_response_placeholder() -> None:
    pass


@router.get("/esrs/{esr_id}/va-events", status_code=501, dependencies=[Depends(pending_workflow)])
def va_events_placeholder(esr_id: str) -> None:
    pass


@router.post("/esrs/{esr_id}/reconcile", status_code=501, dependencies=[Depends(pending_workflow)])
def reconcile_placeholder(esr_id: str) -> None:
    pass


@router.get("/delivery/exceptions", status_code=501, dependencies=[Depends(pending_workflow)])
def delivery_exceptions_placeholder() -> None:
    pass


@router.post("/ai/suggestions/{suggestion_id}/disposition", status_code=501, dependencies=[Depends(pending_workflow)])
def suggestion_disposition_placeholder(suggestion_id: str) -> None:
    pass


@router.get("/ai/modules", status_code=501, dependencies=[Depends(pending_workflow)])
def ai_modules_placeholder() -> None:
    pass


@router.post("/ai/modules/{name}/toggle", status_code=501, dependencies=[Depends(pending_workflow)])
def toggle_ai_module_placeholder(name: str) -> None:
    pass


@router.get("/audit", status_code=501, dependencies=[Depends(pending_workflow)])
def audit_list_placeholder(esr_id: str | None = None, actor: str | None = None) -> None:
    pass


@router.get("/audit/verify", status_code=501, dependencies=[Depends(pending_workflow)])
def audit_verify_placeholder() -> None:
    pass


@router.get("/metrics/kpis", status_code=501, dependencies=[Depends(pending_workflow)])
def metrics_placeholder() -> None:
    pass


@router.post("/demo/reset", status_code=501, dependencies=[Depends(pending_workflow)])
def demo_reset_placeholder() -> None:
    pass


@router.post("/demo/scenarios/{name}", status_code=501, dependencies=[Depends(pending_workflow)])
def demo_scenario_placeholder(name: str) -> None:
    pass


@router.post("/demo/tamper-audit", status_code=501, dependencies=[Depends(pending_workflow)])
def tamper_audit_placeholder() -> None:
    pass


@router.get("/notifications", status_code=501, dependencies=[Depends(pending_workflow)])
def notifications_placeholder() -> None:
    pass


@router.get("/events/stream", status_code=501, dependencies=[Depends(pending_workflow)])
def events_stream_placeholder() -> None:
    pass
