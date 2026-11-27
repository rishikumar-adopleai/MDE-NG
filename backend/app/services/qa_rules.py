"""Stage 5 Internal QA Deterministic VA Contract Rules, Evidence Digest & Separation-of-Duties Guards.

Aligned with VA MDE-NG Solicitation 36C10X27R0001 PWS Sections 5.1, 5.8, 7.2, and P.L. 116-315 § 2002.
Where operational details depend on external MDEO Handbooks unavailable in this workspace, checks report
'unable-to-verify' or provisional contract citations rather than fabricating authoritative rules.
"""

from __future__ import annotations

import hashlib
import re
from datetime import UTC, datetime
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models import Stage3Examiner, Stage3Facility
from app.services.scheduling import is_license_valid_for_exam_date
from app.services.va_dbq_catalog import requested_form_label
from app.services.va_rules import RULES_VERSION

QA_RULES_VERSION = f"MDE-NG-QA-36C10X27R0001-2026.8|{RULES_VERSION}"

# Ordered deterministic rule registry (preserving original 10 keys + identity/scope guard)
ALL_QA_RULE_KEYS: tuple[str, ...] = (
    "required-fields",
    "clinician-signoff",
    "evidence-linkage",
    "form-version",
    "diagnostics",
    "doctor-credentials",
    "forbidden-words",
    "privacy-raw-psych",
    "labs-safety-24h",
    "ace-remarks",
    "veteran-esr-identity",
    "dbq-scope-bva",
)

RULE_METADATA: dict[str, dict[str, str]] = {
    "required-fields": {
        "ruleId": "VA-QA-01",
        "label": "Required & conditional DBQ fields",
        "category": "missing-required-field",
        "contractReference": "PWS 5.1.1.4 DBQ Completion and Accuracy",
        "section": "DBQ structured fields & Remarks",
    },
    "clinician-signoff": {
        "ruleId": "VA-QA-02",
        "label": "Clinician sign-off, attestation & NPI",
        "category": "signature-readiness",
        "contractReference": "PWS 5.1.1.4 Signature Block & NPI Standard",
        "section": "Clinician signature & attestation",
    },
    "evidence-linkage": {
        "ruleId": "VA-QA-03",
        "label": "Contention evidence linkage & readability",
        "category": "evidence-consistency",
        "contractReference": "PWS 5.1.1.4 / 9.4.1 eFolder Evidence Linkage",
        "section": "Linked eFolder records",
    },
    "form-version": {
        "ruleId": "VA-QA-04",
        "label": "DBQ form version & catalog alignment",
        "category": "form-version",
        "contractReference": "PWS 5.1.1.1 / 9.4.2 DBQ IEPD Version",
        "section": "DBQ form metadata",
    },
    "diagnostics": {
        "ruleId": "VA-QA-05",
        "label": "Required diagnostic review & exceptions",
        "category": "attachment",
        "contractReference": "PWS 5.1.1.4 Diagnostic Testing Exceptions",
        "section": "Diagnostic orders",
    },
    "doctor-credentials": {
        "ruleId": "VA-QA-06",
        "label": "Examiner license, portability & VA training on exam date",
        "category": "signature-readiness",
        "contractReference": "PWS 5.8.3 / P.L. 116-315 § 2002 / MDEO Personnel Handbook (pending)",
        "section": "Examiner credentialing & LMS",
    },
    "forbidden-words": {
        "ruleId": "VA-QA-07",
        "label": "VA prohibited adjudicative & prescribing commentary",
        "category": "incomplete-rationale",
        "contractReference": "PWS 5.1.1.1 General DBQ Requirements",
        "section": "DBQ narrative & Remarks",
    },
    "privacy-raw-psych": {
        "ruleId": "VA-QA-08",
        "label": "Restricted raw psychological material exclusion",
        "category": "attachment",
        "contractReference": "PWS 5.1.1.5 / 5.1.1.6 Raw Psychological Test Exclusion & 3-Yr Retention",
        "section": "Attachments & diagnostic records",
    },
    "labs-safety-24h": {
        "ruleId": "VA-QA-09",
        "label": "Abnormal/critical findings & safety SLA (24h / 48h / in-exam)",
        "category": "evidence-consistency",
        "contractReference": "PWS 5.8.1.1 Vulnerable Claimants & 5.8.1.2 Abnormal Findings",
        "section": "Diagnostic flags & safety escalations",
    },
    "ace-remarks": {
        "ruleId": "VA-QA-10",
        "label": "ACE records-review justification & Remarks",
        "category": "incomplete-rationale",
        "contractReference": "PWS 5.1.1.7 ACE Exceptions & MDEO Examinations Handbook (pending)",
        "section": "DBQ Remarks (ACE)",
    },
    "veteran-esr-identity": {
        "ruleId": "VA-QA-11",
        "label": "Veteran identity & ESR scope binding",
        "category": "evidence-consistency",
        "contractReference": "PWS 5.3.2 VA Identifier Mismatch Validation",
        "section": "Claimant & ESR metadata",
    },
    "dbq-scope-bva": {
        "ruleId": "VA-QA-12",
        "label": "DBQ type alignment & BVA Remand scope preservation",
        "category": "form-version",
        "contractReference": "PWS 5.1.1.2 DBQ Selection, Substitution & BVA Remands",
        "section": "Examination requirement & DBQ form type",
    },
}

CHECK_TO_CATEGORY: dict[str, str] = {k: v["category"] for k, v in RULE_METADATA.items()}

# Professions eligible for P.L. 116-315 § 2002 interstate license portability ("no other profession is eligible")
PORTABILITY_ELIGIBLE_CREDENTIAL_TOKENS = frozenset({"md", "do", "np", "pa", "aud", "psyd", "phd"})
INELIGIBLE_PROFESSION_HINTS = ("dds", "dmd", "dc", "chiropractor", "lcsw", "lpc", "lmft", "rn", "lpn", "ot", "pt")

# Unambiguous adjudicative / prescribing phrases ONLY — legitimate clinical statements (% BSA, ROM deg, treatment history, nexus opinions) are never flagged
UNAMBIGUOUS_FORBIDDEN_PATTERNS: tuple[tuple[str, re.Pattern[str]], ...] = (
    (
        "Unambiguous disability rating / percentage prediction",
        re.compile(
            r"\b(?:"
            r"(?:should\s+receive|deserves?|warrants?|assign|recommend(?:s|ed)?|rate(?:d)?(?:\s+at)?|entitled\s+to)\s+(?:a\s+)?\d{1,3}\s*%(?:\s+(?:disability|vasrd|rating|schedular|combined))?"
            r"|\d{1,3}\s*%\s*(?:disability\s+rating|vasrd\s+rating|schedular\s+rating|combined\s+rating|service[- ]connected\s+rating)"
            r")\b",
            re.IGNORECASE,
        ),
    ),
    (
        "Unambiguous claim adjudication or benefit outcome prediction",
        re.compile(
            r"\b(?:"
            r"(?:the\s+)?(?:claim|veteran|claimant)\s+(?:will|should)\s+(?:win|prevail|succeed|be\s+(?:approved|granted|denied)|receive\s+(?:disability\s+)?(?:benefits|compensation))"
            r"|(?:va|rater|board)\s+should\s+(?:grant|approve|deny)\s+(?:this\s+claim|the\s+claim|benefits|service\s+connection)"
            r"|(?:recommend|suggest|urge)\s+(?:granting|approving|denying)\s+(?:the\s+claim|benefits|service\s+connection)"
            r")\b",
            re.IGNORECASE,
        ),
    ),
    (
        "Unambiguous medical treatment directive to patient",
        re.compile(
            r"\b(?:"
            r"(?:i\s+(?:prescribe|advise|recommend|instruct))\s+(?:that\s+)?(?:the\s+)?(?:claimant|veteran|patient)?\s*(?:to\s+)?(?:start|begin|initiate|take|increase|decrease|discontinue|stop)\s+(?:taking\s+)?(?:\w+\s+){0,3}(?:mg|daily|twice\s+daily|tid|bid|medication|regimen|surgery)"
            r"|(?:claimant|veteran|patient)\s+should\s+(?:start|take|begin|increase|decrease|discontinue|stop|undergo)\s+(?:taking\s+)?(?:\w+\s+){0,3}(?:mg|daily|medication|surgery|therapy\s+regimen)"
            r")\b",
            re.IGNORECASE,
        ),
    ),
)

# Context cues that prove a matched percentage or clinical opinion is legitimate clinical text
LEGITIMATE_CLINICAL_CONTEXT_RE = re.compile(
    r"\b(?:bsa|body\s+surface\s+area|ejection\s+fraction|stenosis|burn|scarring|flexion|extension|abduction|degree|deg|speech\s+discrimination|fev1|fvc|at\s+least\s+as\s+likely\s+as\s+not|less\s+likely\s+than\s+not)\b",
    re.IGNORECASE,
)

# Ambiguous borderline phrasing routed to human QA review rather than blocking unconditionally
AMBIGUOUS_COMMENTARY_RE = re.compile(
    r"\b(?:"
    r"likely\s+to\s+(?:be\s+granted|receive\s+compensation|win)"
    r"|(?:warrant|deserve)s?\s+(?:a\s+)?(?:higher\s+)?rating"
    r"|entitled\s+to\s+(?:benefits|compensation)"
    r"|service[- ]connection\s+(?:applies|should\s+be\s+(?:granted|approved|denied)|is\s+(?:warranted|established))"
    r"|i\s+recommend\s+(?:starting|changing)\s+treatment"
    r")\b",
    re.IGNORECASE,
)

RAW_PSYCH_MATERIAL_RE = re.compile(
    r"\b(?:"
    r"raw\s+(?:psych(?:ological)?|neuropsych(?:ological)?|test(?:ing)?)\s+(?:data|sheet(?:s)?|protocol(?:s)?|notes|responses|scoring)"
    r"|psychotherapy\s+notes?"
    r"|mmpi(?:-?[23]|-rf)?\s*(?:raw|answer\s+sheet|profile|protocol|item\s+responses)"
    r"|wais(?:-?(?:iv|iii|v))?\s*(?:raw|record\s+form|protocol)"
    r"|wisc(?:-?(?:iv|v))?\s*(?:raw|record\s+form|protocol)"
    r"|rorschach\s*(?:protocol|scoring|sheet)"
    r"|pai\s*(?:raw|profile|protocol)"
    r"|assessment\s+instrument\s+protocol"
    r")\b",
    re.IGNORECASE,
)

AUTHORIZED_PSYCH_SUMMARY_RE = re.compile(
    r"\b(?:psychological\s+(?:testing\s+)?summary|neuropsychological\s+(?:testing\s+)?summary|integrated\s+testing\s+summary|clinical\s+interpretation\s+summary)\b",
    re.IGNORECASE,
)

UNCERTAIN_PSYCH_MATERIAL_RE = re.compile(
    r"\b(?:psych(?:ological|ometric)|neuropsych(?:ological)?)\b.*\b(?:attachment|worksheet|appendix|unclassified|unsure|raw|protocol|packet)\b",
    re.IGNORECASE,
)

ACE_DEFERRED_RE = re.compile(
    r"\b(?:not\s+(?:examined|performed|applicable|assessed|obtainable)|deferred|skipped|omitted|unable\s+to\s+(?:examine|measure|assess)|n/?a)\b",
    re.IGNORECASE,
)

NO_DIAGNOSIS_UNEXPLAINED_RE = re.compile(
    r"\b(?:tests?\s+(?:were\s+)?not\s+(?:performed|completed|done|conducted)|testing\s+was\s+not\s+performed)\b",
    re.IGNORECASE,
)


def _now_iso() -> str:
    return datetime.now(UTC).isoformat().replace("+00:00", "Z")


def _parse_iso(ts: str | None) -> datetime | None:
    if not ts or not isinstance(ts, str):
        return None
    try:
        dt = datetime.fromisoformat(ts.replace("Z", "+00:00"))
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=UTC)
        return dt
    except ValueError:
        return None


def _diagnostic_ready(order: dict | None) -> tuple[bool, str]:
    if not order:
        return True, "No diagnostic order record."
    if not order.get("required"):
        return True, "Optional diagnostic order."
    if order.get("status") == "reviewed" and order.get("resultIds"):
        return True, "Reviewed."
    exc = order.get("exception")
    if isinstance(exc, dict) and exc.get("reason") in {"claimant-declined", "medically-inadvisable"} and str(exc.get("note") or "").strip():
        return True, f"Valid exception ({exc.get('reason')})."
    if exc:
        return False, f"Diagnostic order '{order.get('orderType')}' exception lacks valid reason or explanation."
    return False, f"Required diagnostic order '{order.get('orderType')}' has no reviewed result or valid exception."


def resolve_actual_exam_date(exam: dict, appt: dict | None, attest: dict) -> str:
    """Resolve actual examination date (never blindly defaulting to today when recorded exam timestamps exist)."""
    if appt:
        if appt.get("completedAt"):
            return str(appt["completedAt"])
        for h in reversed(appt.get("history") or []):
            if h.get("status") == "completed" and h.get("at"):
                return str(h["at"])
        if appt.get("startsAt"):
            return str(appt["startsAt"])
    if exam.get("completedAt"):
        return str(exam["completedAt"])
    if attest.get("at"):
        return str(attest["at"])
    return _now_iso()


def _build_qa_evidence_digest(
    state: dict,
    review_id: str,
    *,
    include_req_version: bool = False,
    req_version_delta: int = 0,
) -> str:
    """Compute a DBQ-scoped SHA-256 digest binding QA to the signed revision, supporting evidence, diagnostics, and rules."""
    review = (state.get("qaReviewsById") or {}).get(review_id)
    if not review:
        return ""
    dbq_id = review.get("dbqId", "")
    dbq = (state.get("dbqsById") or {}).get(dbq_id) or {}
    version = (state.get("dbqVersionsById") or {}).get(review.get("versionId", "")) or {}
    esr_id = str(review.get("esrId") or dbq.get("esrId") or "")
    esr = (state.get("esrsById") or {}).get(esr_id) or {}
    exam = (state.get("examinationsById") or {}).get(dbq.get("examinationId") or "") or {}

    reqs = [
        (state.get("examinationRequirementsById") or {}).get(rid) or {}
        for rid in (exam.get("requirementIds") or [])
    ]
    if include_req_version:
        req_parts = sorted(
            f"{r.get('id', '')}:{max(1, int(r.get('version', 1) or 1) + req_version_delta)}:{r.get('selectedModality', '')}:{r.get('approvedEvidenceDigest', '')}"
            for r in reqs
        )
    else:
        req_parts = sorted(
            f"{r.get('id', '')}:{r.get('selectedModality', '')}:{r.get('approvedEvidenceDigest', '')}:"
            f"{'inactive' if r.get('status') in {'superseded-by-evidence-revision', 'cancelled'} else 'active'}"
            for r in reqs
        )

    cids = sorted(dbq.get("contentionIds") or [])
    contentions = state.get("contentionsById") or {}
    docs_by_id = state.get("evidenceDocumentsById") or {}
    allowed_esr_ids = {esr_id, str(esr.get("linkedOriginalEsrId") or "")} - {""}
    linked_doc_ids = sorted({
        doc_id
        for cid in cids
        for doc_id in (contentions.get(cid, {}).get("evidenceDocumentIds") or [])
        if docs_by_id.get(doc_id, {}).get("esrId") in allowed_esr_ids
    })
    doc_parts = []
    for did in linked_doc_ids:
        d = docs_by_id.get(did) or {}
        pages_sig = "|".join(
            str(p.get("text") or p.get("content") or "") if isinstance(p, dict) else str(p or "")
            for p in (d.get("extractedPages") or [])
        )
        text_fp = hashlib.sha1(f"{d.get('extractedText', '')}|{pages_sig}".encode("utf-8", errors="replace")).hexdigest()[:12]
        doc_parts.append(
            f"{did}:{d.get('sourceHash', '')}:{d.get('version', 1)}:{d.get('classificationStatus', '')}:"
            f"{d.get('classifiedPageCount', 0)}/{d.get('unreadablePageCount', 0)}:{text_fp}"
        )

    orders_by_id = state.get("diagnosticOrdersById") or {}
    results_by_id = state.get("diagnosticResultsById") or {}
    diag_parts = []
    for oid in sorted(exam.get("diagnosticOrderIds") or []):
        o = orders_by_id.get(oid) or {}
        r_parts = []
        for rid in sorted(o.get("resultIds") or []):
            r = results_by_id.get(rid) or {}
            r_parts.append(
                f"{rid}:{r.get('version', 1)}:{r.get('reportedFlag', '')}:{r.get('reviewedAt', '')}:"
                f"{r.get('containsRestrictedRawMaterial', False)}:{r.get('classificationUncertain', False)}"
            )
        diag_parts.append(f"{oid}:{o.get('status', '')}:{(o.get('exception') or {}).get('reason', '')}:[{','.join(r_parts)}]")

    dossier = (state.get("triageDossiersByEsrId") or {}).get(esr_id) or {}
    distress_sig = ",".join(
        f"{a.get('id', '')}:{a.get('severity', '')}:{a.get('escalationStatus', '')}"
        for a in (dossier.get("distressAlerts") or [])
    )
    vals_sig = hashlib.sha256(
        "|".join(f"{k}={v}" for k, v in sorted((version.get("values") or {}).items())).encode()
    ).hexdigest()[:16]

    raw = "|".join([
        f"esr:{esr_id}",
        f"vet:{esr.get('veteranId', '')}",
        f"dbq:{dbq_id}:{dbq.get('formType', '')}:{dbq.get('formVersion', '')}:{dbq.get('signedVersionId', '')}",
        f"ver:{version.get('id', '')}:{version.get('versionNumber', 1)}:{vals_sig}",
        f"exm:{exam.get('examinerId', '')}:{(dbq.get('localAttestation') or {}).get('examinerId', '')}",
        f"reqs:[{';'.join(req_parts)}]",
        f"cids:[{','.join(cids)}]",
        f"docs:[{';'.join(doc_parts)}]",
        f"diags:[{';'.join(diag_parts)}]",
        f"triage:{dossier.get('sourceDigest', '')}:{distress_sig}",
        f"rules:{QA_RULES_VERSION}",
    ])
    return hashlib.sha256(raw.encode()).hexdigest()


def compute_qa_evidence_digest(state: dict, review_id: str) -> str:
    """Compute a DBQ-scoped SHA-256 digest binding QA to the signed revision, supporting evidence, diagnostics, and rules."""
    return _build_qa_evidence_digest(state, review_id, include_req_version=False)


def _matches_recorded_qa_digest(state: dict, review_id: str, recorded_digest: str, live_digest: str) -> bool:
    """Match current digest, or legacy pre-fix digest when only requirement status->completed incremented req.version."""
    if not recorded_digest or not live_digest:
        return True
    if recorded_digest == live_digest:
        return True
    return any(
        recorded_digest == _build_qa_evidence_digest(state, review_id, include_req_version=True, req_version_delta=delta)
        for delta in (0, -1)
    )


def invalidate_stale_qa_reviews(
    state: dict,
    esr_id: str,
    now: str,
    reason: str,
    *,
    affected_doc_ids: set[str] | None = None,
    affected_dbq_ids: set[str] | None = None,
    affected_contention_ids: set[str] | None = None,
) -> list[str]:
    """Mark affected QAReview records stale (and block unsubmitted packages) only when their linked evidence changed."""
    qa_reviews = state.get("qaReviewsById") or {}
    dbqs = state.get("dbqsById") or {}
    contentions = state.get("contentionsById") or {}
    invalidated: list[str] = []

    for rev_id, review in qa_reviews.items():
        if review.get("esrId") != esr_id:
            continue
        dbq = dbqs.get(review.get("dbqId") or "")
        if not dbq:
            continue
        if affected_dbq_ids is not None and dbq.get("id") not in affected_dbq_ids:
            continue
        if affected_contention_ids is not None and not (set(dbq.get("contentionIds") or []) & affected_contention_ids):
            continue
        if affected_doc_ids is not None:
            linked_ids = {
                did
                for cid in (dbq.get("contentionIds") or [])
                for did in (contentions.get(cid, {}).get("evidenceDocumentIds") or [])
            }
            if not (linked_ids & affected_doc_ids):
                # Do not invalidate unaffected DBQs when unrelated documents change!
                continue

        current_digest = compute_qa_evidence_digest(state, rev_id)
        recorded_digest = review.get("evidenceDigest")
        if recorded_digest and current_digest and recorded_digest != current_digest:
            review["isStale"] = True
            review["staleReason"] = reason
            review["staleAt"] = now
            if review.get("status") == "approved":
                review["status"] = "queued"
                dbq["status"] = "ready-for-qa"
            review["updatedAt"] = now
            invalidated.append(rev_id)

    if invalidated:
        for pack in (state.get("resultsPackagesById") or {}).values():
            if pack.get("esrId") == esr_id and pack.get("preparationStatus") != "closed" and pack.get("simulatedResponse") not in {"awaiting-response", "accepted"}:
                pack["validationStatus"] = "blocked"
                pack["preparationStatus"] = "blocked"
                pack["blockingReasons"] = list(dict.fromkeys([*(pack.get("blockingReasons") or []), reason]))
                pack["updatedAt"] = now
    return invalidated


def _validate_required_fields(dbq: dict, values: dict[str, Any]) -> tuple[str, list[str], str]:
    """Validate mandatory and conditional DBQ fields based on DBQ type and form version."""
    form_type = str(dbq.get("formType") or "").lower()
    is_opinion = "opinion" in form_type
    form_version = str(dbq.get("formVersion") or "synthetic-v2")

    req: list[tuple[str, str]] = (
        [("opinionQuestion", "Medical opinion question"), ("rationale", "Clinical rationale")]
        if is_opinion
        else [("history", "Medical history"), ("findings", "Examination findings"), ("rationale", "Clinical rationale")]
    )
    if form_version == "synthetic-v2":
        req.append(("recordsReviewed", "Records reviewed and source references"))
        if not is_opinion:
            req.extend([("diagnosisOutcome", "Diagnosis outcome"), ("diagnosisDetails", "Diagnosis details")])

    missing = [f"{label} is missing." for key, label in req if not str(values.get(key) or "").strip()]
    remarks = str(values.get("remarks") or "").strip()

    # Check unacceptable "could not be established because tests were not performed" (PWS 5.1.1.4)
    outcome = str(values.get("diagnosisOutcome") or "").strip()
    diag_details = str(values.get("diagnosisDetails") or "").strip()
    rationale = str(values.get("rationale") or "").strip()
    combined_diag = f"{diag_details} {rationale} {remarks}".lower()

    if outcome == "Could not be established":
        if not diag_details or len(diag_details) < 10:
            missing.append("Diagnosis outcome is 'Could not be established' without supporting explanatory rationale in Diagnosis details.")
        elif NO_DIAGNOSIS_UNEXPLAINED_RE.search(combined_diag) and not any(
            exc_kw in combined_diag for exc_kw in ("declined", "refused", "medically inadvisable", "contraindicated", "inadvisable")
        ):
            missing.append("DBQ states tests were not performed without an authorized exception (Claimant declined or medically inadvisable) per PWS 5.1.1.4.")

    if form_version not in {"synthetic-v2", "legacy"} and not missing:
        return "unable-to-verify", [], f"Form version '{form_version}' required-field schema is not verified against an MDEO Handbook."
    if missing:
        return "failed", missing, " ".join(missing)
    return "passed", [], "All applicable DBQ fields and conditional explanations are complete."


def _find_examiner_record(
    db: Session | None,
    state: dict,
    examiner_id: str,
    facility_id: str,
) -> tuple[dict | None, str]:
    """Resolve Stage3Examiner (SQLite) -> state['examinersById'] plus required facility state."""
    fac_state = ""
    fac_obj = (state.get("facilitiesById") or {}).get(facility_id) or {}
    if fac_obj:
        fac_state = str(fac_obj.get("stateCode") or "").upper()
    if db is not None and facility_id and not fac_state:
        fac_row = db.execute(
            select(Stage3Facility).where(Stage3Facility.facility_id == facility_id)
        ).scalar_one_or_none()
        if fac_row and fac_row.state_code:
            fac_state = fac_row.state_code.upper()

    if db is not None and examiner_id:
        row = db.execute(
            select(Stage3Examiner).where(Stage3Examiner.examiner_id == examiner_id)
        ).scalar_one_or_none()
        if row:
            data = dict(row.data_json or {})
            data.setdefault("examinerId", row.examiner_id)
            data.setdefault("doctorName", row.doctor_name)
            data.setdefault("licenseStatus", row.license_status)
            data.setdefault("licenseExpiryDate", row.license_expiry_date)
            data.setdefault("licenseState", row.license_state)
            data.setdefault("credentials", row.credentials or [])
            data.setdefault("specialties", row.specialties or [])
            data.setdefault("authorizedDbqTypes", row.authorized_dbq_types or [])
            data.setdefault("status", row.status)
            return data, fac_state

    legacy = (state.get("examinersById") or {}).get(examiner_id)
    if legacy:
        return legacy, fac_state
    return None, fac_state


def _extract_cred_tokens(creds: list[str]) -> set[str]:
    tokens: set[str] = set()
    for raw in creds or []:
        for tok in re.findall(r"[a-z]{2,}", str(raw).casefold()):
            tokens.add(tok)
    return tokens


def _evaluate_doctor_credentials(
    db: Session | None,
    state: dict,
    dbq: dict,
    exam: dict,
    appt: dict | None,
    req: dict | None,
    values: dict[str, Any] | None = None,
) -> tuple[str, str]:
    """Evaluate examiner eligibility, license validity, P.L. 116-315 § 2002 portability, training, and NPI on exam date."""
    attest = dbq.get("localAttestation") or {}
    assigned_id = str(exam.get("examinerId") or (appt or {}).get("examinerId") or "")
    signing_id = str(attest.get("examinerId") or assigned_id or "")
    examiner_id = signing_id or assigned_id
    exam_date = resolve_actual_exam_date(exam, appt, attest)
    exam_day = exam_date[:10]
    modality = str((appt or {}).get("modality") or (req or {}).get("selectedModality") or "in-person")
    facility_id = str((appt or {}).get("facilityId") or "")

    if not examiner_id:
        return "failed", "No assigned or signing examiner could be verified on this examination."

    if assigned_id and signing_id and assigned_id != signing_id:
        reconcile_note = str(
            attest.get("reconciliationNote")
            or dbq.get("examinerReconciliationNote")
            or (values or {}).get("examinerReconciliationNote")
            or ""
        ).strip()
        if not reconcile_note:
            return (
                "failed",
                f"Signing clinician ({signing_id}) differs from scheduled examiner ({assigned_id}) without documented reconciliation.",
            )

    examiner_rec, fac_state = _find_examiner_record(db, state, examiner_id, facility_id)
    if not examiner_rec:
        if attest.get("examinerName") and attest.get("credentials"):
            return (
                "unable-to-verify",
                f"Attested examiner {attest['examinerName']} ({', '.join(attest['credentials'])}); full credentialing directory record unavailable.",
            )
        return "failed", f"Examiner '{examiner_id}' could not be verified in the credentialing directory."

    doc_name = examiner_rec.get("doctorName") or examiner_rec.get("displayName") or attest.get("examinerName") or examiner_id

    # 1. Prohibited / suspended / excluded check (PWS 5.8.3.2)
    exm_status = str(examiner_rec.get("status") or "active").lower()
    if (
        exm_status in {"excluded", "suspended", "revoked", "prohibited", "inactive"}
        or examiner_rec.get("medicareMedicaidExcluded")
        or examiner_rec.get("licenseSurrenderedUnderInvestigation")
    ):
        return "failed", f"Examiner {doc_name} is ineligible/prohibited under PWS 5.8.3.2 (status: {exm_status})."

    # 2. License validity & jurisdiction / P.L. 116-315 § 2002 interstate portability on actual exam date
    creds = [str(c) for c in (examiner_rec.get("credentials") or attest.get("credentials") or [])]
    cred_tokens = _extract_cred_tokens(creds)
    raw_licenses = list(examiner_rec.get("licenses") or [])
    lic_status = str(examiner_rec.get("licenseStatus") or "active")
    lic_expiry = str(examiner_rec.get("licenseExpiryDate") or examiner_rec.get("licenseExpiresAt") or "")
    lic_state = str(examiner_rec.get("licenseState") or "").upper()
    licenses = [
        {
            "licenseState": str(l.get("licenseState") or l.get("state") or "").upper(),
            "licenseStatus": str(l.get("licenseStatus") or l.get("status") or "active").lower(),
            "licenseExpiryDate": str(l.get("licenseExpiryDate") or l.get("expiresAt") or ""),
        }
        for l in raw_licenses
    ]
    if not licenses and (lic_expiry or lic_status != "active"):
        licenses = [{
            "licenseStatus": lic_status.lower(),
            "licenseExpiryDate": lic_expiry,
            "licenseState": lic_state,
        }]

    portability_note = ""
    if licenses:
        if modality == "in-person" and fac_state:
            in_state_matches = [
                l for l in licenses
                if str(l.get("licenseState") or "").upper() == fac_state
            ]
            active_in_state = [
                l for l in in_state_matches
                if str(l.get("licenseStatus") or "").lower() == "active"
                and (not str(l.get("licenseExpiryDate") or "").strip() or str(l.get("licenseExpiryDate") or "")[:10] > exam_day)
            ]
            if not active_in_state:
                active_out_state = [
                    l for l in licenses
                    if str(l.get("licenseStatus") or "").lower() == "active"
                    and (not str(l.get("licenseExpiryDate") or "").strip() or str(l.get("licenseExpiryDate") or "")[:10] > exam_day)
                    and len(str(l.get("licenseState") or "").strip()) == 2
                ]
                has_ineligible_prof = any(t in cred_tokens for t in INELIGIBLE_PROFESSION_HINTS) and not (cred_tokens & PORTABILITY_ELIGIBLE_CREDENTIAL_TOKENS)
                portability_flag = examiner_rec.get("interstatePortabilityAuthorized") is not False
                if active_out_state and not has_ineligible_prof and portability_flag and (cred_tokens & PORTABILITY_ELIGIBLE_CREDENTIAL_TOKENS):
                    out_st = str(active_out_state[0].get("licenseState") or "").upper()
                    portability_note = f" (interstate portability under P.L. 116-315 § 2002 via active {out_st} license for exam in {fac_state})"
                elif active_out_state:
                    out_st = str(active_out_state[0].get("licenseState") or "").upper()
                    return (
                        "failed",
                        f"Doctor check failed for {doc_name}: holds only {out_st} license for {fac_state} exam without valid P.L. 116-315 § 2002 interstate portability authorization.",
                    )
                else:
                    return (
                        "failed",
                        f"Doctor check failed for {doc_name}: no active unrestricted license valid past actual exam date {exam_day}.",
                    )
        else:
            ok_lic, lic_reason = is_license_valid_for_exam_date(
                lic_status, lic_expiry, exam_date, licenses=licenses, required_state="",
            )
            if not ok_lic:
                return "failed", f"Doctor check failed for {doc_name}: {lic_reason} (exam date {exam_day})."

    # 3. NPI verification where required under HIPAA (PWS 5.1.1.4)
    npi = str(examiner_rec.get("npi") or attest.get("npi") or "").strip()
    hipaa_req = examiner_rec.get("hipaaNpiRequired")
    if npi:
        if not re.fullmatch(r"\d{10}", npi):
            return "failed", f"Examiner {doc_name} has invalid 10-digit NPI '{npi}'."
    elif hipaa_req is True:
        return "failed", f"Examiner {doc_name} is missing mandatory National Provider Identifier (NPI) per PWS 5.1.1.4."

    # 4. Mandatory VA C&P / LMS training & 10-hour annual retraining (SSSCE exempt per PWS 5.1.3.2)
    is_sss_exempt = bool(
        examiner_rec.get("trainingExempt")
        or examiner_rec.get("lmsExempt")
        or examiner_rec.get("isSingleSourceSubcontracted")
        or (req and req.get("internationalProcess") == "SSSCE")
    )
    if not is_sss_exempt:
        certs = examiner_rec.get("certifications") or []
        legacy_training = examiner_rec.get("training") or []
        expired_items: list[str] = []
        has_cp_training = False

        for cert in certs:
            cname = str(cert.get("certificationName") or "")
            status = str(cert.get("status") or "").lower()
            expires = str(cert.get("expiresAt") or "")
            if any(kw in cname.casefold() for kw in ("c&p", "lms", "training", "retraining")):
                has_cp_training = True
            if status in {"expired", "revoked", "suspended", "pending"} or (expires and len(expires) >= 10 and expires[:10] < exam_day):
                expired_items.append(cname or "VA LMS Certification")

        for tr in legacy_training:
            course = str(tr.get("course") or "")
            status = str(tr.get("status") or "").lower()
            expires = str(tr.get("expiresAt") or "")
            has_cp_training = True
            if status in {"expired", "pending"} or (expires and len(expires) >= 10 and expires[:10] < exam_day):
                expired_items.append(course or "VA LMS Training")

        if any("training" in c.casefold() or "lms" in c.casefold() for c in creds):
            has_cp_training = True

        if expired_items:
            return (
                "failed",
                f"Mandatory VA LMS training/certification expired or incomplete on exam date {exam_day}: {', '.join(expired_items)}.",
            )
        if not has_cp_training:
            return "failed", f"Mandatory VA C&P / LMS initial or annual training record missing for {doc_name}."

    exempt_msg = " (training exempt per PWS 5.1.3.2 SSSCE)" if is_sss_exempt else ""
    return (
        "passed",
        f"Verified active unrestricted license and mandatory VA training for {doc_name} on exam date {exam_day}{portability_note}{exempt_msg}.",
    )


def _evaluate_forbidden_words(dbq: dict, values: dict[str, Any]) -> tuple[str, list[str], str]:
    """Distinguish unambiguous adjudicative/prescribing violations from legitimate clinical % / treatment / opinion statements."""
    is_opinion_dbq = "opinion" in str(dbq.get("formType") or "").lower() or bool(str(values.get("opinionQuestion") or "").strip())
    blocking_hits: list[str] = []
    review_hits: list[str] = []

    for field_key, raw in values.items():
        if not raw:
            continue
        text_val = str(raw)

        for label, pattern in UNAMBIGUOUS_FORBIDDEN_PATTERNS:
            for m in pattern.finditer(text_val):
                snippet = m.group(0).strip()
                window = text_val[max(0, m.start() - 40): min(len(text_val), m.end() + 40)]
                if "percentage" in label.lower() and LEGITIMATE_CLINICAL_CONTEXT_RE.search(window):
                    continue
                if "adjudication" in label.lower() and is_opinion_dbq and re.search(r"\b(?:at\s+least\s+as\s+likely|less\s+likely|etiology|nexus)\b", window, re.I):
                    continue
                blocking_hits.append(f"{label} in '{field_key}' (\"{snippet}\")")

        for m in AMBIGUOUS_COMMENTARY_RE.finditer(text_val):
            snippet = m.group(0).strip()
            if any(snippet in b for b in blocking_hits):
                continue
            review_hits.append(f"Potentially prohibited commentary in '{field_key}' (\"{snippet}\") — QA reviewer context verification required")

    if blocking_hits:
        return "failed", blocking_hits, f"Unambiguous prohibited content detected: {'; '.join(blocking_hits)}."
    if review_hits:
        return "human-review-required", review_hits, f"Borderline wording requires human QA context verification: {'; '.join(review_hits)}."
    return "passed", [], "No VA-prohibited rating predictions, claim adjudication statements, or unauthorized treatment directives detected."


def _scan_and_quarantine_privacy(state: dict, dbq: dict, exam: dict) -> tuple[str, str, list[dict[str, Any]]]:
    """Identify raw or uncertain psychological test materials, quarantine locally (3-year retention), and exclude from VA export while allowing testing summaries."""
    quarantined: list[dict[str, Any]] = []
    diag_results = state.setdefault("diagnosticResultsById", {})
    orders_by_id = state.get("diagnosticOrdersById") or {}
    now = _now_iso()
    uncertain_count = 0

    for order_id in exam.get("diagnosticOrderIds") or []:
        order = orders_by_id.get(order_id) or {}
        for res_id in order.get("resultIds") or []:
            res = diag_results.get(res_id)
            if not res:
                continue
            combined = f"{res.get('sourceReference', '')} {res.get('summary', '')}"
            is_authorized_summary = bool(AUTHORIZED_PSYCH_SUMMARY_RE.search(combined)) and not bool(RAW_PSYCH_MATERIAL_RE.search(combined))
            if is_authorized_summary:
                res.setdefault("containsRestrictedRawMaterial", False)
                continue

            matched_raw = bool(res.get("containsRestrictedRawMaterial")) or bool(RAW_PSYCH_MATERIAL_RE.search(combined))
            is_uncertain = bool(res.get("classificationUncertain")) or (
                not matched_raw and bool(UNCERTAIN_PSYCH_MATERIAL_RE.search(combined))
            )
            if matched_raw or is_uncertain:
                res["containsRestrictedRawMaterial"] = True
                res["privacyQuarantined"] = True
                res["classificationUncertain"] = bool(is_uncertain and not matched_raw)
                res["quarantineStorage"] = "local-restricted-3yr"
                res["quarantineRetentionYears"] = 3
                res["quarantinedAt"] = res.get("quarantinedAt") or now
                if is_uncertain and not matched_raw:
                    uncertain_count += 1
                quarantined.append({
                    "id": res_id,
                    "sourceReference": res.get("sourceReference") or res_id,
                    "reason": (
                        "Potentially restricted psychological attachment (classification uncertain) excluded from VA package until authorized review."
                        if (is_uncertain and not matched_raw)
                        else "Raw psychological testing sheet/notes intercepted and quarantined in 3-year restricted local storage; excluded from VA upload package."
                    ),
                    "retentionYears": 3,
                })

    if uncertain_count > 0:
        names = ", ".join(q["sourceReference"] for q in quarantined)
        return (
            "human-review-required",
            f"Privacy guard quarantined {len(quarantined)} record(s) ({names}) including classification-uncertain psychological attachment(s); verify exclusion before Stage 6.",
            quarantined,
        )
    if quarantined:
        names = ", ".join(q["sourceReference"] for q in quarantined)
        return (
            "passed",
            f"Privacy guard active: {len(quarantined)} raw psychological record(s) ({names}) quarantined to 3-year local storage and blocked from VA transmission.",
            quarantined,
        )
    return "passed", "No raw psychological test protocols or psychotherapy notes detected; authorized psychological summaries remain cleared.", []


def _evaluate_labs_and_safety(state: dict, dbq: dict, exam: dict, appt: dict | None, values: dict[str, Any]) -> tuple[str, str]:
    """Differentiate PWS 5.8.1.2 SLAs: Critical (24h incident + immediate/certified patient notice), Non-Critical Abnormal outside exam (48h notice), In-Exam Abnormal (Remarks), Normal (none), Vulnerable/Urgent Safety (24h)."""
    esr_id = str(dbq.get("esrId") or "")
    orders_by_id = state.get("diagnosticOrdersById") or {}
    results_by_id = state.get("diagnosticResultsById") or {}
    remarks_l = str(values.get("remarks") or "").lower()

    issues: list[str] = []
    notes: list[str] = []
    checked_any = False

    for order_id in exam.get("diagnosticOrderIds") or []:
        order = orders_by_id.get(order_id) or {}
        for res_id in order.get("resultIds") or []:
            res = results_by_id.get(res_id)
            if not res:
                continue
            checked_any = True
            flag = str(res.get("reportedFlag") or "").lower()
            ref = str(res.get("sourceReference") or res_id)
            if not flag or flag == "normal":
                continue

            identified_during_exam = bool(res.get("identifiedDuringExam"))
            if flag == "critical":
                if not res.get("reviewedAt"):
                    issues.append(f"Critical/life-threatening result '{ref}' lacks clinician review and 24-hour notification/incident documentation")
                else:
                    created_dt = _parse_iso(res.get("createdAt"))
                    reviewed_dt = _parse_iso(res.get("reviewedAt"))
                    if created_dt and reviewed_dt and (reviewed_dt - created_dt).total_seconds() > 86400:
                        issues.append(f"Critical result '{ref}' notification/review exceeded the 24-hour SLA window (PWS 5.8.1.2)")
                    has_incident_or_letter = (
                        res.get("incidentReportLogged") is not False
                        and (res.get("incidentReportedAt") or res.get("certifiedLetterSentAt") or res.get("patientNotifiedAt") or res.get("reviewedAt"))
                    )
                    if res.get("incidentReportLogged") is False:
                        issues.append(f"Critical result '{ref}' is missing mandatory VA Incident Report (PWS 5.8.1.2)")
                    elif not has_incident_or_letter:
                        issues.append(f"Critical result '{ref}' lacks 24-hour patient contact or certified-letter documentation")
                    else:
                        notes.append(f"Critical result '{ref}' reviewed within 24h")
            elif flag == "abnormal":
                if identified_during_exam:
                    if not any(kw in remarks_l for kw in ("notified", "advised", "follow up", "follow-up", "primary care", "abnormal", "communicated")):
                        issues.append(f"Abnormal result '{ref}' identified during exam is not documented as communicated in DBQ Remarks (PWS 5.8.1.2)")
                else:
                    # Non-critical abnormal outside exam -> 48h patient/portal communication (does NOT require critical 24h incident report)
                    has_48h_comm = bool(res.get("patientNotifiedAt") or res.get("portalNotificationAt") or res.get("reviewedAt"))
                    if not has_48h_comm:
                        issues.append(f"Non-critical abnormal result '{ref}' lacks 48-hour clinician review / Claimant & Portal communication record (PWS 5.8.1.2)")
                    else:
                        created_dt = _parse_iso(res.get("createdAt"))
                        notified_dt = _parse_iso(res.get("patientNotifiedAt") or res.get("portalNotificationAt") or res.get("reviewedAt"))
                        if created_dt and notified_dt and (notified_dt - created_dt).total_seconds() > 172800:
                            issues.append(f"Non-critical abnormal result '{ref}' notification exceeded 48-hour SLA window")
                        else:
                            notes.append(f"Abnormal result '{ref}' communicated within 48h (no critical incident report required)")

    dossier = (state.get("triageDossiersByEsrId") or {}).get(esr_id) or {}
    urgent_alerts = [
        a for a in (dossier.get("distressAlerts") or [])
        if str(a.get("severity") or "").lower() == "urgent"
    ]
    if urgent_alerts:
        checked_any = True
        has_escalation = any(
            str(a.get("escalationStatus") or "").lower() == "escalated"
            for a in urgent_alerts
        ) or any(
            bool((state.get("contentionsById") or {}).get(cid, {}).get("distressEscalatedAt"))
            for cid in (dbq.get("contentionIds") or [])
        )
        if not has_escalation:
            issues.append("Stage 2 urgent safety/distress alert lacks a logged 24-hour clinical escalation/incident record (PWS 5.8.1.1)")
        else:
            for a in urgent_alerts:
                esc_dt = _parse_iso(a.get("escalatedAt"))
                ref_dt = _parse_iso(dossier.get("preparedAt")) or _parse_iso((appt or {}).get("startsAt"))
                if esc_dt and ref_dt and abs((esc_dt - ref_dt).total_seconds()) > 86400:
                    issues.append("Urgent safety alert escalation exceeded 24-hour SLA window")

    if issues:
        return "failed", "; ".join(issues) + "."
    if not checked_any:
        return "not-applicable", "No abnormal, critical, or urgent clinical safety findings apply to this examination."
    return "passed", "; ".join(notes) + "." if notes else "Abnormal/critical diagnostic findings (24h/48h SLAs) and safety escalations verified."


def _evaluate_ace_remarks(dbq: dict, exam: dict, appt: dict | None, req: dict | None, values: dict[str, Any]) -> tuple[str, str]:
    modality = str((appt or {}).get("modality") or (req or {}).get("selectedModality") or "")
    if modality != "ace-records-review":
        return "not-applicable", "Standard in-person/Tele-C&P modality; ACE skip-section Remarks check is not applicable."

    remarks = str(values.get("remarks") or "").strip()
    findings = str(values.get("findings") or "").strip()
    skipped_signals = bool(
        ACE_DEFERRED_RE.search(findings)
        or not str(values.get("diagnosisDetails") or "").strip()
        or len(findings) < 25
    )
    if skipped_signals and len(remarks) < 20:
        return (
            "failed",
            "ACE records review defers/omits examination sections without an explicit authorized explanation in Remarks (PWS 5.1.1.7).",
        )
    if not remarks:
        return (
            "failed",
            "ACE records review requires a documented explanation in Remarks confirming record sufficiency and any omitted in-person tests.",
        )
    return "passed", "ACE records review includes required explanation and scope sufficiency basis in Remarks."


def _evaluate_veteran_esr_identity(state: dict, dbq: dict, esr_id: str) -> tuple[str, str]:
    esr = (state.get("esrsById") or {}).get(esr_id) or {}
    if not esr or dbq.get("esrId") != esr_id:
        return "failed", "DBQ ESR reference does not match an active workspace ESR."
    vet = (state.get("veteransById") or {}).get(esr.get("veteranId") or "") or {}
    vet_name = str(vet.get("displayName") or vet.get("fullName") or "").strip()
    detected = str((dbq.get("uploadedDbqDocument") or {}).get("detectedClaimantName") or "").strip()
    if detected and vet_name:
        vet_tokens = {t.lower() for t in re.findall(r"[A-Za-z]{2,}", vet_name)}
        det_tokens = {t.lower() for t in re.findall(r"[A-Za-z]{2,}", detected)}
        if vet_tokens and det_tokens and not (vet_tokens & det_tokens):
            return "failed", f"Uploaded DBQ claimant name '{detected}' does not match ESR Veteran '{vet_name}'."
    if not dbq.get("contentionIds"):
        return "failed", "DBQ is not linked to any active ESR contention."
    return "passed", f"Verified Veteran/ESR association ({vet_name or esr.get('veteranId') or esr_id})."


def _evaluate_dbq_scope_and_bva(state: dict, dbq: dict, req: dict | None, esr_id: str) -> tuple[str, str]:
    dossier = (state.get("triageDossiersByEsrId") or {}).get(esr_id) or {}
    is_bva = bool((dossier.get("programFlags") or {}).get("isBvaRemand"))
    requested_types = list((req or {}).get("requestedDbqTypes") or [])
    form_type = str(dbq.get("formType") or "")

    if is_bva:
        contentions = state.get("contentionsById") or {}
        va_req = [
            requested_form_label(name) or str(name).strip()
            for cid in (dbq.get("contentionIds") or [])
            for name in (contentions.get(cid, {}).get("requestedDbqNames") or [])
            if str(name).strip()
        ]
        triage_draft = next(
            ((contentions.get(cid) or {}).get("triageDraft") or {} for cid in (dbq.get("contentionIds") or [])),
            {},
        )
        if va_req and form_type and form_type not in va_req:
            if not (triage_draft.get("scopeChangeAuthorized") and str(triage_draft.get("remandAuthorizationReference") or "").strip()):
                return "failed", f"BVA Remand prohibits substituting '{form_type}' for VA-requested {va_req} without structured remand authorization (PWS 5.1.1.2)."
        return "passed", "BVA Remand DBQ scope and requested forms verified."

    if requested_types and form_type not in requested_types:
        return "human-review-required", f"Completed DBQ '{form_type}' differs from planned {requested_types}; confirm clinical substitution per PWS 5.1.1.2."
    return "passed", f"DBQ type '{form_type}' aligns with approved examination requirement."


def run_deterministic_qa_rules(
    db: Session | None,
    state: dict,
    review_id: str,
) -> dict[str, Any]:
    """Run all 12 Stage 5 deterministic VA contract rules with 5-state outcomes and sync findings."""
    qa_reviews = state.setdefault("qaReviewsById", {})
    qa_findings = state.setdefault("qaFindingsById", {})
    review = qa_reviews.get(review_id)
    if not review:
        raise KeyError(f"QA review '{review_id}' not found.")
    dbq = (state.get("dbqsById") or {}).get(review["dbqId"])
    version = (state.get("dbqVersionsById") or {}).get(review["versionId"])
    if not dbq or not version or version.get("dbqId") != dbq.get("id"):
        raise ValueError("Signed DBQ version is unavailable for QA rules evaluation.")

    now = _now_iso()
    esr_id = str(review["esrId"])
    exam = (state.get("examinationsById") or {}).get(dbq.get("examinationId") or "") or {}
    req_id = (exam.get("requirementIds") or [None])[-1]
    req = (state.get("examinationRequirementsById") or {}).get(req_id or "") or {}
    appts = state.get("appointmentsById") or {}
    appt = next(
        (
            appts.get(aid)
            for aid in (exam.get("appointmentIds") or [])
            if (appts.get(aid) or {}).get("status") in {"completed", "confirmed-locally", "confirmed"}
        ),
        None,
    )
    values = dict(version.get("values") or {})

    evidence_docs = state.get("evidenceDocumentsById") or {}
    contentions = state.get("contentionsById") or {}
    allowed_esr_ids = {esr_id, str(((state.get("esrsById") or {}).get(esr_id) or {}).get("linkedOriginalEsrId") or "")} - {""}
    raw_doc_ids = list(dict.fromkeys(
        doc_id
        for cid in (dbq.get("contentionIds") or [])
        for doc_id in (contentions.get(cid, {}).get("evidenceDocumentIds") or [])
        if evidence_docs.get(doc_id, {}).get("esrId") in allowed_esr_ids
        and evidence_docs.get(doc_id, {}).get("classificationStatus") != "excluded"
    ))
    evidence_ids = [
        did for did in raw_doc_ids
        if evidence_docs.get(did, {}).get("classifiedPageCount", 0) > 0
    ]
    unreadable_doc_count = sum(1 for did in raw_doc_ids if (evidence_docs.get(did) or {}).get("unreadablePageCount", 0) > 0)
    pending_doc_count = sum(1 for did in raw_doc_ids if (evidence_docs.get(did) or {}).get("classificationStatus") in {"queued", "processing"})

    orders_by_id = state.get("diagnosticOrdersById") or {}
    orders = [orders_by_id.get(oid) for oid in (exam.get("diagnosticOrderIds") or []) if orders_by_id.get(oid)]

    req_status, field_errors, req_detail = _validate_required_fields(dbq, values)
    signoff_ok = (
        dbq.get("signedVersionId") == version["id"]
        and dbq.get("currentVersionId") == version["id"]
        and dbq.get("localReviewState") == "review-recorded"
        and (dbq.get("formVersion") != "synthetic-v2" or (dbq.get("localAttestation") or {}).get("versionId") == version["id"])
    )
    form_ok = bool(version.get("formVersion")) and version.get("formVersion") == dbq.get("formVersion")

    if not orders:
        diag_status, diag_detail = "not-applicable", "No diagnostic orders required for this examination."
    else:
        diag_checks = [_diagnostic_ready(o) for o in orders]
        diag_failures = [msg for ok, msg in diag_checks if not ok]
        if diag_failures:
            diag_status, diag_detail = "failed", "; ".join(diag_failures)
        else:
            diag_status, diag_detail = "passed", "All required diagnostic orders have clinician-reviewed results or valid exceptions."

    if pending_doc_count:
        ev_status, ev_detail = "unable-to-verify", f"{pending_doc_count} linked evidence document(s) still awaiting intake processing."
    elif not evidence_ids:
        ev_status, ev_detail = "failed", "No linked readable source evidence is available for the report contention(s)."
    elif unreadable_doc_count:
        ev_status, ev_detail = "human-review-required", f"{len(evidence_ids)} linked source record(s) available, but {unreadable_doc_count} document(s) contain unreadable pages requiring QA inspection."
    else:
        ev_status, ev_detail = "passed", f"{len(evidence_ids)} linked readable source record(s) verified."

    doc_status, doc_detail = _evaluate_doctor_credentials(db, state, dbq, exam, appt, req, values)
    fw_status, fw_hits, fw_detail = _evaluate_forbidden_words(dbq, values)
    privacy_status, privacy_detail, quarantined = _scan_and_quarantine_privacy(state, dbq, exam)
    labs_status, labs_detail = _evaluate_labs_and_safety(state, dbq, exam, appt, values)
    ace_status, ace_detail = _evaluate_ace_remarks(dbq, exam, appt, req, values)
    id_status, id_detail = _evaluate_veteran_esr_identity(state, dbq, esr_id)
    scope_status, scope_detail = _evaluate_dbq_scope_and_bva(state, dbq, req, esr_id)

    def _make_check(key: str, status: str, detail: str, *, action: str = "") -> dict[str, Any]:
        meta = RULE_METADATA[key]
        sev = "advisory" if status in {"passed", "not-applicable"} else ("review" if status in {"human-review-required", "unable-to-verify"} else "blocking")
        return {
            "ruleId": meta["ruleId"],
            "key": key,
            "label": meta["label"],
            "contractReference": meta["contractReference"],
            "ruleVersion": QA_RULES_VERSION,
            "dbqId": dbq["id"],
            "affectedSection": meta["section"],
            "supportingEvidenceIds": evidence_ids[:10],
            "status": status,
            "severity": sev,
            "detail": detail,
            "recommendedAction": action or ("" if status in {"passed", "not-applicable"} else f"Inspect {meta['section']} under {meta['contractReference']}."),
            "versionId": version["id"],
            "evaluatedAt": now,
        }

    checks: list[dict[str, Any]] = [
        _make_check("required-fields", req_status, req_detail),
        _make_check(
            "clinician-signoff",
            "passed" if signoff_ok else "failed",
            "Exact report revision is signed and attested." if signoff_ok else "This exact report revision lacks a matching clinician sign-off and attestation.",
        ),
        _make_check("evidence-linkage", ev_status, ev_detail),
        _make_check(
            "form-version",
            "passed" if form_ok else "failed",
            f"Report and signed revision both match form version {dbq.get('formVersion')}." if form_ok else "The DBQ and signed revision form versions do not match.",
        ),
        _make_check("diagnostics", diag_status, diag_detail),
        _make_check("doctor-credentials", doc_status, doc_detail),
        _make_check("forbidden-words", fw_status, fw_detail),
        _make_check("privacy-raw-psych", privacy_status, privacy_detail),
        _make_check("labs-safety-24h", labs_status, labs_detail),
        _make_check("ace-remarks", ace_status, ace_detail),
        _make_check("veteran-esr-identity", id_status, id_detail),
        _make_check("dbq-scope-bva", scope_status, scope_detail),
    ]

    finding_ids = list(review.get("findingIds") or [])
    checks_by_key = {c["key"]: c for c in checks}

    # Reconcile prior & current deterministic findings against the active signed revision.
    # Human and AI findings are preserved across resubmissions until a human QA reviewer resolves/dismisses them.
    for fid in finding_ids:
        f = qa_findings.get(fid)
        if not f or f.get("origin") != "deterministic" or f.get("status") not in {"open", "correction-requested"}:
            continue
        ck = f.get("checkKey")
        if ck not in checks_by_key:
            continue
        same_version = f.get("versionId") == version["id"]
        next_status = checks_by_key[ck]["status"]
        if next_status in {"passed", "not-applicable"}:
            f["status"] = "resolved"
            note = (
                "Automatically cleared after re-running passing QA rules check."
                if same_version
                else f"Automatically resolved after resubmitted revision '{version['id']}' passed rule '{ck}'."
            )
            f["comments"] = [*f.get("comments", []), note]
            f["resolutionHistory"] = [
                *f.get("resolutionHistory", []),
                {"at": now, "actorId": "system-rules", "status": "resolved", "comment": note},
            ]
            f["updatedAt"] = now
            f["version"] = f.get("version", 1) + 1
        elif not same_version:
            note = f"Carried forward to resubmitted revision '{version['id']}' because rule '{ck}' remains '{next_status}'."
            f["status"] = "resolved"
            f["comments"] = [*f.get("comments", []), note]
            f["resolutionHistory"] = [
                *f.get("resolutionHistory", []),
                {"at": now, "actorId": "system-rules", "status": "resolved", "comment": note},
            ]
            f["updatedAt"] = now
            f["version"] = f.get("version", 1) + 1

    # Add findings for failed, unable-to-verify, or human-review-required checks
    for c in checks:
        if c["status"] not in {"failed", "human-review-required", "unable-to-verify"}:
            continue
        already_open = any(
            (qa_findings.get(fid) or {}).get("origin") == "deterministic"
            and (qa_findings.get(fid) or {}).get("versionId") == version["id"]
            and (qa_findings.get(fid) or {}).get("checkKey") == c["key"]
            and (qa_findings.get(fid) or {}).get("status") in {"open", "correction-requested"}
            for fid in finding_ids
        )
        if already_open:
            continue
        fid = f"finding-rule-{review_id}-{c['key']}-{int(datetime.now(UTC).timestamp() * 1000)}"
        qa_findings[fid] = {
            "id": fid,
            "version": 1,
            "createdAt": now,
            "updatedAt": now,
            "esrId": esr_id,
            "qaReviewId": review_id,
            "dbqId": dbq["id"],
            "ruleId": c["ruleId"],
            "ruleVersion": QA_RULES_VERSION,
            "contractReference": c["contractReference"],
            "sectionReference": c["label"],
            "issueCategory": CHECK_TO_CATEGORY.get(c["key"], "evidence-consistency"),
            "severity": "blocking" if c["status"] == "failed" else "review",
            "title": c["detail"],
            "supportingEvidenceIds": evidence_ids,
            "reviewerId": "system-rules",
            "status": "open",
            "comments": [f"Flagged by {c['ruleId']} ({c['contractReference']}): {c['recommendedAction']}"],
            "resolutionHistory": [],
            "origin": "deterministic",
            "checkKey": c["key"],
            "versionId": version["id"],
        }
        finding_ids.append(fid)

    evidence_digest = compute_qa_evidence_digest(state, review_id)
    review["checks"] = checks
    review["findingIds"] = finding_ids
    review["quarantinedPrivacyFiles"] = quarantined
    review["ruleVersion"] = QA_RULES_VERSION
    review["evidenceDigest"] = evidence_digest
    review["isStale"] = False
    review.pop("staleReason", None)
    review["updatedAt"] = now
    review["version"] = review.get("version", 1) + 1
    if review.get("status") == "queued":
        review["status"] = "in-review"

    failed_count = sum(1 for c in checks if c["status"] == "failed")
    review_count = sum(1 for c in checks if c["status"] in {"human-review-required", "unable-to-verify"})
    return {
        "checks": checks,
        "failedCount": failed_count,
        "reviewNeededCount": review_count,
        "quarantinedPrivacyFiles": quarantined,
        "evidenceDigest": evidence_digest,
        "ruleVersion": QA_RULES_VERSION,
    }


def check_independent_qa_reviewer(state: dict, review: dict, actor_id: str, actor_role: str) -> None:
    """Enforce separation of duties: neither the signing clinician nor performing examiner can QA/approve their own report (unless admin override)."""
    if not review or not actor_id:
        return
    dbq = (state.get("dbqsById") or {}).get(review.get("dbqId") or "")
    if not dbq:
        return
    exam = (state.get("examinationsById") or {}).get(dbq.get("examinationId") or "") or {}
    attest = dbq.get("localAttestation") or {}
    signer_actor = str(attest.get("staffActorId") or "")
    signer_examiner = str(attest.get("examinerId") or "")
    performing_examiner = str(exam.get("examinerId") or "")
    if actor_role != "admin" and actor_id in {x for x in (signer_actor, signer_examiner, performing_examiner) if x}:
        from fastapi import HTTPException
        raise HTTPException(
            403,
            "Separation of duties: the examiner or clinician who performed or signed this report cannot act as its independent QA reviewer.",
        )


def enforce_backend_qa_approval(
    db: Session | None,
    state: dict,
    review_id: str,
    actor_id: str,
    actor_role: str,
    approval_note: str = "",
    manual_override: Any = False,
) -> dict[str, Any]:
    """Authoritative backend validation before allowing QA approval of a signed DBQ revision."""
    from fastapi import HTTPException

    review = (state.get("qaReviewsById") or {}).get(review_id)
    if not review:
        raise HTTPException(404, "QA review record not found.")
    check_independent_qa_reviewer(state, review, actor_id, actor_role)

    dbq = (state.get("dbqsById") or {}).get(review.get("dbqId") or "")
    version = (state.get("dbqVersionsById") or {}).get(review.get("versionId") or "")
    if not dbq or not version or dbq.get("signedVersionId") != version.get("id") or dbq.get("currentVersionId") != version.get("id"):
        raise HTTPException(422, "Only the exact locked, currently signed DBQ revision can be approved by QA.")

    now = _now_iso()
    if review.get("isStale"):
        raise HTTPException(
            422,
            f"QA review is stale ({review.get('staleReason') or 'underlying evidence changed'}). Re-run Stage 5 checks/audit before approval.",
        )

    current_digest = compute_qa_evidence_digest(state, review_id)
    if not _matches_recorded_qa_digest(state, review_id, str(review.get("evidenceDigest") or ""), current_digest):
        invalidate_stale_qa_reviews(
            state,
            review["esrId"],
            now,
            "Linked evidence or diagnostic results changed prior to QA approval.",
            affected_dbq_ids={dbq["id"]},
        )
        raise HTTPException(422, "Underlying evidence or diagnostics changed since QA evaluation; re-run Stage 5 checks/audit before approval.")

    if not review.get("checks") or review.get("ruleVersion") != QA_RULES_VERSION:
        run_deterministic_qa_rules(db, state, review_id)

    unresolved_checks = [
        c for c in (review.get("checks") or [])
        if c.get("status") in {"failed", "human-review-required", "unable-to-verify"}
    ]
    if unresolved_checks:
        labels = "; ".join(f"{c.get('ruleId') or c.get('key')}: {c.get('status')}" for c in unresolved_checks[:4])
        raise HTTPException(422, f"Cannot approve QA review until deterministic rule checks are resolved ({labels}).")

    findings_by_id = state.get("qaFindingsById") or {}
    open_blockers = [
        findings_by_id[fid]
        for fid in (review.get("findingIds") or [])
        if fid in findings_by_id
        and findings_by_id[fid].get("status") in {"open", "correction-requested"}
        and findings_by_id[fid].get("severity") in {"blocking", "review"}
    ]
    if open_blockers:
        titles = "; ".join(str(f.get("title") or f.get("id")) for f in open_blockers[:3])
        raise HTTPException(422, f"Cannot approve QA review with unresolved blocking or review findings: {titles}.")

    ai_audit = review.get("aiAudit") or {}
    if ai_audit.get("status") in {"failed", "partial"} or ai_audit.get("manualReviewRequired"):
        confirmed = bool(
            manual_override is True
            or (isinstance(manual_override, dict) and manual_override.get("confirmed"))
        )
        if not confirmed or len(str(approval_note or "").strip()) < 10:
            raise HTTPException(
                422,
                "AI clinical audit failed or was partial; check manual review confirmation and enter an approval note (minimum 10 characters) before approving.",
            )
        review["manualAiOverride"] = {
            "confirmed": True,
            "note": str(approval_note or "").strip(),
            "actorId": actor_id,
            "at": now,
        }

    return {
        "evidenceDigest": current_digest,
        "ruleVersion": QA_RULES_VERSION,
        "manualAiOverride": bool(review.get("manualAiOverride")),
    }


def enforce_backend_stage6_delivery(
    db: Session | None,
    state: dict,
    action: str,
    args: list[Any],
    actor_id: str,
    actor_role: str,
) -> dict[str, Any]:
    """Authoritative backend guard for Stage 6 delivery, version/digest integrity, contention checks, and DB idempotency."""
    from fastapi import HTTPException

    if actor_role not in {"delivery", "admin"}:
        raise HTTPException(403, "Only authorized VA Delivery / Operations staff may execute Stage 6 actions.")

    now = _now_iso()
    if action in {"validateResultsPackage", "prepareResultsPackage", "simulatePackageTransmission", "uploadContentionDocuments", "sendContentionRap"}:
        pkg_id = str(args[0]) if args else ""
        pack = (state.get("resultsPackagesById") or {}).get(pkg_id)
        if not pack:
            raise HTTPException(404, "Results package not found.")
        esr_id = str(pack.get("esrId") or "")
        esr = (state.get("esrsById") or {}).get(esr_id) or {}
        if esr.get("workflowClosedAt") or pack.get("preparationStatus") == "closed":
            raise HTTPException(422, "Cannot modify a closed ESR or delivered/closed results package.")

        # Select contention subset when per-contention upload/RAP is invoked
        selected_cids = (
            [str(c) for c in (args[1] or [])]
            if action in {"uploadContentionDocuments", "sendContentionRap"} and len(args) > 1 and isinstance(args[1], list)
            else list(esr.get("contentionIds") or [])
        )
        is_tech_rework = esr.get("esrType") == "rework" and (esr.get("reworkDetails") or {}).get("reworkRoute") == "technical-delivery-issue"
        check_esr_id = str((esr.get("reworkDetails") or {}).get("originalEsrId") or esr_id) if is_tech_rework else esr_id

        # Verify exact approved revisions & live Stage 5 evidence digests for active contentions being delivered
        for cid in selected_cids:
            cont = (state.get("contentionsById") or {}).get(cid)
            if not cont or cont.get("simulatedDisposition") == "cancelled":
                continue
            linked_dbqs = [
                d for d in (state.get("dbqsById") or {}).values()
                if d.get("esrId") == check_esr_id and (is_tech_rework or cid in (d.get("contentionIds") or []))
            ]
            if not linked_dbqs and action in {"uploadContentionDocuments", "sendContentionRap"}:
                raise HTTPException(422, f"Contention '{cid}' lacks an approved DBQ for delivery.")
            for dbq in linked_dbqs:
                if dbq.get("status") != "approved-for-package" or dbq.get("signedVersionId") != dbq.get("currentVersionId"):
                    raise HTTPException(422, f"DBQ '{dbq.get('id')}' is not QA-approved at its current signed revision.")
                review = next(
                    (
                        r for r in (state.get("qaReviewsById") or {}).values()
                        if r.get("dbqId") == dbq.get("id") and r.get("status") == "approved" and r.get("versionId") == dbq.get("currentVersionId")
                    ),
                    None,
                )
                if not review or review.get("isStale"):
                    raise HTTPException(422, f"QA review for DBQ '{dbq.get('id')}' is missing or stale.")
                live_digest = compute_qa_evidence_digest(state, review["id"])
                recorded_digest = review.get("approvedEvidenceDigest") or review.get("evidenceDigest")
                if not _matches_recorded_qa_digest(state, review["id"], str(recorded_digest or ""), live_digest):
                    invalidate_stale_qa_reviews(state, check_esr_id, now, "Evidence changed prior to Stage 6 delivery.", affected_dbq_ids={dbq["id"]})
                    raise HTTPException(422, "Underlying clinical evidence or diagnostics changed prior to delivery; Stage 5 QA revalidation required.")

        # DB-backed duplicate RAP transmission prevention when confirmable
        simulate_fail = bool(args[2]) if action == "sendContentionRap" and len(args) > 2 else False
        if db is not None and action == "sendContentionRap" and not simulate_fail:
            from sqlalchemy import select
            from app.models import Stage6DeliveryOperation
            rev_lock = "|".join(
                f"{cid}:{((state.get('resultsPackagesById') or {}).get(pkg_id, {}).get('contentionDeliveries') or {}).get(cid, {}).get('revisionIds', {})}"
                for cid in sorted(selected_cids)
            )
            op_key = hashlib.sha256(f"{pkg_id}|{rev_lock}".encode("utf-8")).hexdigest()[:64]
            existing_op = db.scalar(
                select(Stage6DeliveryOperation).where(
                    Stage6DeliveryOperation.operation_kind == "rap-send",
                    Stage6DeliveryOperation.idempotency_key == op_key,
                )
            )
            if existing_op:
                raise HTTPException(409, "Duplicate RAP transmission blocked by database idempotency check.")
            return {"dbOpKind": "rap-send", "dbOpKey": op_key, "esrId": esr_id}

    if db is not None and action == "createLinkedReworkESR" and args and isinstance(args[0], dict):
        from sqlalchemy import select
        from app.models import Stage6DeliveryOperation
        payload = args[0]
        orig_id = str(payload.get("originalEsrId") or "")
        cids = sorted(str(x) for x in (payload.get("affectedContentionIds") or []))
        cat = str(payload.get("reworkCategory") or "")
        instr = str(payload.get("vaSpecialInstructions") or "").strip().lower()
        req_key = str(payload.get("reworkRequestKey") or "")
        raw_key = req_key or f"{orig_id}|{','.join(cids)}|{cat}|{instr}"
        op_key = hashlib.sha256(raw_key.encode("utf-8")).hexdigest()[:64]
        existing_op = db.scalar(
            select(Stage6DeliveryOperation).where(
                Stage6DeliveryOperation.operation_kind == "rework-esr",
                Stage6DeliveryOperation.idempotency_key == op_key,
            )
        )
        if existing_op:
            raise HTTPException(409, "Duplicate incoming Rework ESR rejected by database idempotency check.")
        return {"dbOpKind": "rework-esr", "dbOpKey": op_key, "esrId": orig_id}

    return {}
