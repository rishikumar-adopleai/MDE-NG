"""Versioned VA Examination & Eligibility Rules Engine (RFP 36C10X27R0001 Section 5.1)."""

from __future__ import annotations

import re
from datetime import UTC, datetime
from typing import Any

from app.services.clinical_nlp import detect_clinical_contradictions
from app.services.va_dbq_catalog import requested_form_label

RULES_VERSION = "MDE-NG-RFP-36C10X27R0001-2026.7"
RULES_EFFECTIVE_DATE = "2026-10-01"

STANDARD_MODALITIES = ["in-person", "tele-c-and-p"]
RECORDS_REVIEW_MODALITIES = [*STANDARD_MODALITIES, "ace-records-review"]

# Centralized, configurable, versioned VA Rules Registry.
# Separates specialtyCategory from qualificationRequirements, tracks verification status
# against RFP 36C10X27R0001 vs MDEO Examinations Handbook, and removes contradictory lists.
RULES_REGISTRY: dict[str, dict[str, Any]] = {
    "knee": {
        "title": "Knee and Lower Leg",
        "specialtyCategory": "Orthopedics / Musculoskeletal",
        "qualificationRequirements": ["MD / DO", "Authorized musculoskeletal examiner"],
        "qualificationVerification": "unverified-mdeo-handbook-required",
        "primaryDbq": "Knee and Lower Leg Conditions DBQ",
        "pathwayStates": {"in-person": "permitted", "tele-c-and-p": "conditional", "ace-records-review": "prohibited"},
        "modalities": STANDARD_MODALITIES,
        "diagnostics": ["Imaging report review"],
        "ruleVerificationStatus": "verified",
        "sourceReference": "RFP 5.1.1.1 · Musculoskeletal ROM / Physical Assessment",
    },
    "hearing": {
        "title": "Hearing Loss and Tinnitus",
        "specialtyCategory": "Audiology",
        "qualificationRequirements": ["AuD", "State-licensed audiologist"],
        "qualificationVerification": "verified-rfp",
        "primaryDbq": "Hearing Loss and Tinnitus DBQ",
        "pathwayStates": {"in-person": "required", "tele-c-and-p": "prohibited", "ace-records-review": "prohibited"},
        "modalities": ["in-person"],
        "diagnostics": ["Audiology test"],
        "ruleVerificationStatus": "verified",
        "sourceReference": "RFP 5.1.1.1 / 5.2.2 · Audiometric Testing Standards",
    },
    "back": {
        "title": "Back (Thoracolumbar Spine)",
        "specialtyCategory": "Orthopedics / Musculoskeletal",
        "qualificationRequirements": ["MD / DO", "Authorized musculoskeletal examiner"],
        "qualificationVerification": "unverified-mdeo-handbook-required",
        "primaryDbq": "Back Conditions DBQ",
        "pathwayStates": {"in-person": "permitted", "tele-c-and-p": "conditional", "ace-records-review": "prohibited"},
        "modalities": STANDARD_MODALITIES,
        "diagnostics": ["Imaging report review"],
        "ruleVerificationStatus": "verified",
        "sourceReference": "RFP 5.1.1.1 · Musculoskeletal ROM / Physical Assessment",
    },
    "mental": {
        "title": "Mental Disorders",
        "specialtyCategory": "Psychiatry / Psychology",
        "qualificationRequirements": ["MD / DO Psychiatrist or PhD / PsyD Licensed psychologist (supervision per MDEO Handbook)"],
        "qualificationVerification": "unverified-mdeo-handbook-required",
        "primaryDbq": "Mental Disorders DBQ",
        "pathwayStates": {"in-person": "permitted", "tele-c-and-p": "permitted", "ace-records-review": "conditional"},
        "modalities": RECORDS_REVIEW_MODALITIES,
        "diagnostics": [],
        "ruleVerificationStatus": "verified",
        "sourceReference": "RFP 5.1.1.7 · Mental Health Tele-C&P / Conditional ACE",
    },
    "migraine": {
        "title": "Headaches",
        "specialtyCategory": "Neurology / General Medical",
        "qualificationRequirements": ["MD / DO / NP / PA per MDEO credentialing"],
        "qualificationVerification": "unverified-mdeo-handbook-required",
        "primaryDbq": "Headaches DBQ",
        "pathwayStates": {"in-person": "permitted", "tele-c-and-p": "permitted", "ace-records-review": "conditional"},
        "modalities": RECORDS_REVIEW_MODALITIES,
        "diagnostics": ["Diagnostic study review"],
        "ruleVerificationStatus": "verified",
        "sourceReference": "RFP 5.1.1.7 · Non-ROM Neurological / Conditional ACE",
    },
    "skin": {
        "title": "Skin Diseases",
        "specialtyCategory": "Dermatology / General Medical",
        "qualificationRequirements": ["MD / DO / NP / PA per MDEO credentialing"],
        "qualificationVerification": "unverified-mdeo-handbook-required",
        "primaryDbq": "Skin Diseases DBQ",
        "pathwayStates": {"in-person": "permitted", "tele-c-and-p": "conditional", "ace-records-review": "prohibited"},
        "modalities": STANDARD_MODALITIES,
        "diagnostics": [],
        "ruleVerificationStatus": "verified",
        "sourceReference": "RFP 5.1 · Dermatological BSA / Visual Assessment",
    },
    "nerve": {
        "title": "Peripheral Nerves",
        "specialtyCategory": "Neurology",
        "qualificationRequirements": ["MD / DO / NP / PA per MDEO credentialing"],
        "qualificationVerification": "unverified-mdeo-handbook-required",
        "primaryDbq": "Peripheral Nerves DBQ",
        "pathwayStates": {"in-person": "permitted", "tele-c-and-p": "conditional", "ace-records-review": "conditional"},
        "modalities": RECORDS_REVIEW_MODALITIES,
        "diagnostics": ["Diagnostic study review"],
        "ruleVerificationStatus": "verified",
        "sourceReference": "RFP 5.1.1.7 · Peripheral Nerve / Conditional ACE",
    },
    "shoulder": {
        "title": "Shoulder and Arm",
        "specialtyCategory": "Orthopedics / Musculoskeletal",
        "qualificationRequirements": ["MD / DO", "Authorized musculoskeletal examiner"],
        "qualificationVerification": "unverified-mdeo-handbook-required",
        "primaryDbq": "Shoulder and Arm Conditions DBQ",
        "pathwayStates": {"in-person": "permitted", "tele-c-and-p": "conditional", "ace-records-review": "prohibited"},
        "modalities": STANDARD_MODALITIES,
        "diagnostics": ["Imaging report review"],
        "ruleVerificationStatus": "verified",
        "sourceReference": "RFP 5.1.1.1 · Musculoskeletal ROM / Physical Assessment",
    },
    "neck": {
        "title": "Neck (Cervical Spine)",
        "specialtyCategory": "Orthopedics / Musculoskeletal",
        "qualificationRequirements": ["MD / DO", "Authorized musculoskeletal examiner"],
        "qualificationVerification": "unverified-mdeo-handbook-required",
        "primaryDbq": "Neck (Cervical Spine) Conditions DBQ",
        "pathwayStates": {"in-person": "permitted", "tele-c-and-p": "conditional", "ace-records-review": "prohibited"},
        "modalities": STANDARD_MODALITIES,
        "diagnostics": ["Imaging report review"],
        "ruleVerificationStatus": "verified",
        "sourceReference": "RFP 5.1.1.1 · Musculoskeletal ROM / Physical Assessment",
    },
    "diabetes": {
        "title": "Diabetes Mellitus",
        "specialtyCategory": "Internal Medicine / Endocrinology",
        "qualificationRequirements": ["MD / DO / NP / PA (verify against MDEO Handbook)"],
        "qualificationVerification": "unverified-mdeo-handbook-required",
        "primaryDbq": "Diabetes Mellitus DBQ",
        "pathwayStates": {"in-person": "permitted", "tele-c-and-p": "permitted", "ace-records-review": "unverified"},
        "modalities": RECORDS_REVIEW_MODALITIES,
        "diagnostics": ["Laboratory panel review"],
        "ruleVerificationStatus": "unverified-requires-review",
        "sourceReference": "Unverified — MDEO Examinations Handbook required",
    },
    "respiratory": {
        "title": "Respiratory Conditions",
        "specialtyCategory": "Pulmonology / Internal Medicine",
        "qualificationRequirements": ["MD / DO / NP / PA (verify against MDEO Handbook)"],
        "qualificationVerification": "unverified-mdeo-handbook-required",
        "primaryDbq": "Respiratory Conditions DBQ",
        "pathwayStates": {"in-person": "permitted", "tele-c-and-p": "conditional", "ace-records-review": "unverified"},
        "modalities": RECORDS_REVIEW_MODALITIES,
        "diagnostics": ["Pulmonary function test"],
        "ruleVerificationStatus": "unverified-requires-review",
        "sourceReference": "Unverified — MDEO Examinations Handbook required",
    },
    "cardiovascular": {
        "title": "Hypertension & Heart Conditions",
        "specialtyCategory": "Cardiology / Internal Medicine",
        "qualificationRequirements": ["MD / DO / NP / PA (verify against MDEO Handbook)"],
        "qualificationVerification": "unverified-mdeo-handbook-required",
        "primaryDbq": "Hypertension DBQ",
        "pathwayStates": {"in-person": "permitted", "tele-c-and-p": "conditional", "ace-records-review": "unverified"},
        "modalities": RECORDS_REVIEW_MODALITIES,
        "diagnostics": ["Electrocardiogram / blood pressure log"],
        "ruleVerificationStatus": "unverified-requires-review",
        "sourceReference": "Unverified — MDEO Examinations Handbook required",
    },
    "tbi": {
        "title": "Traumatic Brain Injury",
        "specialtyCategory": "Neurology / Physiatry / Psychiatry / Neurosurgery",
        "qualificationRequirements": ["MD / DO TBI-certified (Physiatrist, Neurologist, Psychiatrist, or Neurosurgeon)"],
        "qualificationVerification": "verified-rfp",
        "primaryDbq": "Traumatic Brain Injury (TBI) DBQ",
        "pathwayStates": {"in-person": "required", "tele-c-and-p": "prohibited", "ace-records-review": "prohibited"},
        "modalities": ["in-person"],
        "diagnostics": ["Neuroimaging review"],
        "ruleVerificationStatus": "verified",
        "sourceReference": "RFP 5.1 · TBI Specialist Credentialing",
    },
    "sha": {
        "title": "Separation Health Assessment",
        "specialtyCategory": "General Medical",
        "qualificationRequirements": ["MD / DO / NP / PA (BDD/IDES SHA qualified)"],
        "qualificationVerification": "verified-rfp",
        "primaryDbq": "Separation Health Assessment (SHA) DBQ",
        "pathwayStates": {"in-person": "required", "tele-c-and-p": "prohibited", "ace-records-review": "prohibited"},
        "modalities": ["in-person"],
        "diagnostics": ["Pre-discharge records review"],
        "ruleVerificationStatus": "verified",
        "sourceReference": "RFP 5.1.2 · BDD / IDES Separation Health Assessment",
    },
    "general": {
        "title": "General Medical Evaluation",
        "specialtyCategory": "General Medical",
        "qualificationRequirements": ["MD / DO / NP / PA (verify against MDEO Handbook)"],
        "qualificationVerification": "unverified-mdeo-handbook-required",
        "primaryDbq": "General Medical DBQ",
        "pathwayStates": {"in-person": "permitted", "tele-c-and-p": "conditional", "ace-records-review": "prohibited"},
        "modalities": STANDARD_MODALITIES,
        "diagnostics": [],
        "ruleVerificationStatus": "unverified-requires-review",
        "sourceReference": "Unverified — MDEO Examinations Handbook required",
    },
}

EXAM_CATALOG: dict[str, tuple[str, str, list[str], str, list[str], list[str]]] = {
    code: (
        entry["title"],
        entry["specialtyCategory"],
        entry["qualificationRequirements"],
        entry["primaryDbq"],
        entry["modalities"],
        entry["diagnostics"],
    )
    for code, entry in RULES_REGISTRY.items()
}

EXAM_ALIASES: dict[str, tuple[str, ...]] = {
    "knee": ("knee", "patella", "meniscus", "acl", "lower leg"),
    "hearing": ("hearing", "tinnitus", "auditory", "ear", "deafness"),
    "back": ("back", "thoracolumbar", "lumbar", "lumbosacral", "low back"),
    "neck": ("neck", "cervical", "cervicalgia"),
    "mental": ("mental", "depression", "ptsd", "anxiety", "trauma", "psychiatric"),
    "migraine": ("migraine", "headache", "cephalalgia"),
    "skin": ("skin", "rash", "dermatitis", "eczema", "psoriasis", "scar"),
    "nerve": ("nerve", "neuropathy", "radiculopathy", "sciatica", "carpal tunnel"),
    "shoulder": ("shoulder", "rotator cuff", "clavicle", "deltoid", "arm"),
    "diabetes": ("diabetes", "endocrine", "glucose", "a1c"),
    "respiratory": ("asthma", "copd", "bronchitis", "sleep apnea", "respiratory", "sinusitis"),
    "cardiovascular": ("hypertension", "blood pressure", "heart", "cardiac", "coronary"),
    "tbi": ("tbi", "traumatic brain", "concussion"),
    "sha": ("separation health", "sha", "bdd comprehensive"),
}

ADDITIONAL_DBQS = ["Medical Opinion DBQ", "General Medical DBQ"]
INTERNATIONAL_MARKERS = ("apo", "fpo", "dpo", "germany", "japan", "korea", "overseas", "international", "uk", "italy", "spain", "guam")


def _parse_iso_date(raw: str | None) -> datetime | None:
    if not raw or not isinstance(raw, str):
        return None
    try:
        dt = datetime.fromisoformat(raw.strip().replace("Z", "+00:00"))
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=UTC)
        return dt
    except ValueError:
        return None


def detect_claim_programs(esr: dict, veteran: dict, contentions: list[dict]) -> dict[str, Any]:
    """Evaluate RFP special program rules: BDD/IDES SHA (5.1.2), BVA Remands (5.1.1.2), TERA (5.1.2.1), International (5.1.3)."""
    instructions = (esr.get("specialInstructions") or "").casefold()
    program = ((esr.get("examManagement") or {}).get("program") or esr.get("schedulingProgram") or "").casefold()
    explicit_programs = [str(p).casefold() for p in (esr.get("specialPrograms") or [])]
    combined_contentions = " ".join(
        f"{c.get('title', '')} {c.get('classification', '')} {' '.join(c.get('requestedDbqNames', []))}"
        for c in contentions
    ).casefold()
    esr_blob = f"{instructions} {program} {' '.join(explicit_programs)}"

    is_bdd_ides = bool(
        esr.get("schedulingProgram") in {"BDD", "IDES"}
        or any(p in {"bdd", "ides"} for p in explicit_programs)
        or re.search(r"\b(bdd|ides|integrated\s+disability\s+evaluation|benefits\s+delivery\s+at\s+discharge|separation\s+health)\b", f"{esr_blob} {combined_contentions}")
    )
    is_bva_remand = bool(
        esr.get("bvaRemand")
        or any("bva" in p or "remand" in p for p in explicit_programs)
        or re.search(r"\b(bva|remand|board\s+of\s+veterans)\b", f"{esr_blob} {combined_contentions}")
    )
    remand_directives = {
        "requiresInPerson": bool(re.search(r"\b(in[-\s]?person|physical\s+exam)\b", instructions)) if is_bva_remand else False,
        "prohibitsAce": is_bva_remand and not bool(re.search(r"\bace\s+(?:only|eligible|authorized)\b", instructions)),
        "requiresMedicalOpinion": bool(re.search(r"\bmedical\s+opinion\b", instructions)) if is_bva_remand else False,
    }

    # TERA: distinguish authoritative ESR confirmation from unconfirmed exposure keywords
    is_tera_confirmed = bool(
        esr.get("teraConfirmed")
        or esr.get("teraIndicator")
        or any(p in {"tera", "pact", "pact-act"} for p in explicit_programs)
        or re.search(r"\b(tera|toxic\s+exposure\s+risk\s+activity|pact\s+act)\b", esr_blob)
    )
    tera_keyword_only = bool(
        not is_tera_confirmed
        and re.search(r"\b(tera|toxic\s+exposure|burn\s+pit|pact\s+act|agent\s+orange|herbicide)\b", f"{esr_blob} {combined_contentions}")
    )

    location = f"{veteran.get('city', '')} {veteran.get('stateCode', '')}".casefold()
    is_international = bool(re.search(r"\b(" + "|".join(INTERNATIONAL_MARKERS) + r")\b", f"{location} {instructions}"))

    # BDD/IDES Release from Active Duty (RAD) date evaluation for SHA vs General Medical DBQ
    rad_raw = esr.get("releaseFromActiveDutyDate") or esr.get("radDate") or veteran.get("releaseFromActiveDutyDate") or ""
    rad_dt = _parse_iso_date(rad_raw)
    rad_status = "not-applicable"
    bdd_assessment = ""
    if is_bdd_ides:
        if not rad_raw:
            rad_status = "missing"
        elif rad_dt is None:
            rad_status = "invalid"
        else:
            days_until_rad = (rad_dt - datetime.now(UTC)).days
            if days_until_rad >= 0:
                rad_status = "pre-discharge"
                bdd_assessment = "Separation Health Assessment (SHA) DBQ"
            else:
                rad_status = "post-discharge"
                bdd_assessment = "General Medical DBQ"

    notices: list[dict[str, str]] = []
    program_conflicts: list[str] = []
    if is_bva_remand:
        notices.append({
            "rule": "RFP 5.1.1.2 · BVA Remand Guard",
            "severity": "warning",
            "text": "BVA Remand indicator present: preserve VA-requested DBQs and remand instructions; structured authorization is required for scope changes.",
        })
    if is_bdd_ides:
        if rad_status in {"missing", "invalid"}:
            notices.append({
                "rule": "RFP 5.1.2 · BDD / IDES RAD Date Review",
                "severity": "warning",
                "text": "BDD/IDES indicator present without a valid Release from Active Duty (RAD) date: authorized review required to determine SHA vs General Medical DBQ.",
            })
        else:
            notices.append({
                "rule": "RFP 5.1.2 · BDD / IDES Assessment",
                "severity": "info",
                "text": f"BDD/IDES ({rad_status}, RAD {rad_raw[:10]}): {bdd_assessment} rules apply per RFP 5.1.2.",
            })
    if is_tera_confirmed:
        if is_bdd_ides:
            program_conflicts.append("BDD/IDES pre-discharge TERA exception may override standard TERA Medical Opinion requirement.")
        notices.append({
            "rule": "RFP 5.1.2.1 · Confirmed TERA Indicator",
            "severity": "info",
            "text": (
                "Pre-discharge TERA medical-opinion exception may apply under BDD/IDES guidelines."
                if is_bdd_ides else
                "Authoritative TERA indicator present: verify whether a Medical Opinion DBQ is ordered by VA."
            ),
        })
    elif tera_keyword_only:
        notices.append({
            "rule": "RFP 5.1.2.1 · Unconfirmed Exposure Mention",
            "severity": "info",
            "text": "Exposure-related terminology found in records/contention without authoritative TERA program confirmation; do not auto-add Medical Opinion DBQ without clinician verification.",
        })
    if is_international:
        notices.append({
            "rule": "RFP 5.1.3 · International Exam",
            "severity": "warning",
            "text": "Overseas/APO/FPO indicator detected: Stage 3 scheduling may require international examination arrangements.",
        })
    if is_bva_remand and is_bdd_ides:
        program_conflicts.append("Both BVA Remand and BDD/IDES indicators detected: escalate for VA clarification.")

    return {
        "ruleVersion": RULES_VERSION,
        "effectiveDate": RULES_EFFECTIVE_DATE,
        "isBddIdes": is_bdd_ides,
        "radDate": rad_raw,
        "radStatus": rad_status,
        "bddRecommendedAssessment": bdd_assessment,
        "isBvaRemand": is_bva_remand,
        "remandDirectives": remand_directives,
        "isTera": is_tera_confirmed,
        "teraConfirmed": is_tera_confirmed,
        "teraMentionedUnconfirmed": tera_keyword_only,
        "isInternational": is_international,
        "programConflicts": program_conflicts,
        "programNotices": notices,
    }


def resolve_exam_suggestion(contention: dict, matched_facts: list[dict]) -> dict[str, Any]:
    """Resolve VA-requested DBQs and dynamically derive examination requirements even for unseen conditions."""
    title = contention.get("title", "")
    title_l = title.casefold()
    raw_requests = contention.get("requestedDbqNames") or []
    canonical_requests = [label for name in raw_requests if (label := requested_form_label(name))]

    candidates = [code for code, mapping in EXAM_CATALOG.items() if mapping[3] in canonical_requests]
    code = candidates[0] if len(candidates) == 1 else ""
    match_source = "va-requested" if code else ""

    if not code:
        code = next((c for c, terms in EXAM_ALIASES.items() if any(term in title_l for term in terms)), "")
        if code:
            match_source = "contention-title"

    if not code and matched_facts:
        for fact in matched_facts:
            for c_info in fact.get("codes", []):
                if ec := c_info.get("examCode"):
                    if ec in RULES_REGISTRY:
                        code = ec
                        match_source = f"medical-code ({c_info.get('system', 'Code')} {c_info.get('code', '')})"
                        break
            if code:
                break
        if not code:
            bp_to_code = {
                "knee": "knee", "shoulder": "shoulder", "back": "back", "neck": "neck",
                "ear": "hearing", "skin": "skin", "mental": "mental", "head": "migraine",
                "nerve": "nerve", "endocrine": "diabetes", "respiratory": "respiratory",
                "cardiovascular": "cardiovascular",
            }
            for fact in matched_facts:
                if mapped_code := bp_to_code.get(fact.get("bodyPart", "")):
                    code = mapped_code
                    match_source = "clinical-nlp"
                    break

    reg = RULES_REGISTRY.get(code)
    suggested_dbqs: list[str] = []
    if reg:
        suggested_dbqs = list(dict.fromkeys([
            reg["primaryDbq"],
            *(label for label in canonical_requests if label in ADDITIONAL_DBQS),
            *raw_requests,
        ]))
    elif raw_requests:
        suggested_dbqs = list(dict.fromkeys(raw_requests))
    elif title:
        suggested_dbqs = [f"{title.strip()} DBQ"]

    # Check if secondary DBQs should be surfaced based on actual clinical evidence + exam scope (not condition alone)
    secondary_recommendations: list[str] = []
    secondary_dbqs: list[str] = []
    if code in {"knee", "back", "neck", "shoulder"} and "Peripheral Nerves DBQ" not in canonical_requests:
        nerve_findings = [
            f for f in matched_facts
            if f.get("bodyPart") == "nerve"
            and not f.get("isNegated")
            and not f.get("isResolved")
            and not f.get("isFamilyHistory")
            and any(
                kw in (f.get("rawExcerpt") or "").casefold()
                for kw in ("radiculopathy", "neuropathy", "sciatic", "paresthesia", "emg", "nerve conduction")
            )
        ]
        if nerve_findings:
            secondary_recommendations.append("Peripheral Nerves DBQ (active radiculopathy / neuropathy findings in records)")
            secondary_dbqs.append("Peripheral Nerves DBQ")

    rule_status = reg["ruleVerificationStatus"] if reg else "unverified-requires-review"
    qual_status = reg["qualificationVerification"] if reg else "unverified-mdeo-handbook-required"
    unverified_reasons: list[str] = []
    if not reg:
        unverified_reasons.append(f"No verified VA rule mapping exists for '{title or 'this contention'}'; authorized clinical review or VA clarification required.")
    elif rule_status != "verified":
        unverified_reasons.append(reg["sourceReference"])
    elif qual_status != "verified-rfp":
        unverified_reasons.append("Examiner qualification credentials require verification against the MDEO Examinations Handbook.")

    return {
        "examCode": code or "dynamic",
        "isDynamicUnmapped": not bool(reg),
        "vaRequestedDbqs": list(dict.fromkeys(raw_requests or canonical_requests)),
        "canonicalVaDbqs": canonical_requests,
        "suggestedDbqs": suggested_dbqs,
        "dbqMatchSource": match_source or "dynamic-esr-derived",
        "secondaryRecommendations": secondary_recommendations,
        "secondaryDbqs": secondary_dbqs,
        "specialty": reg["specialtyCategory"] if reg else "Unverified — Dynamic Examination Requirement",
        "credentials": reg["qualificationRequirements"] if reg else ["Authorized clinical verification required (MDEO Handbook)"],
        "qualificationVerification": qual_status,
        "ruleVerificationStatus": rule_status,
        "ruleSourceReference": reg["sourceReference"] if reg else "Unverified — MDEO Examinations Handbook unavailable",
        "unverifiedReasons": unverified_reasons,
    }


def _years_since(iso_date: str) -> float | None:
    if not iso_date:
        return None
    try:
        dt = datetime.fromisoformat(iso_date.replace("Z", "+00:00"))
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=UTC)
        return (datetime.now(UTC) - dt).days / 365.25
    except ValueError:
        return None


def analyze_evidence_gaps_and_pathway(
    contention: dict,
    exam_info: dict,
    matched_pages: list[dict],
    matched_facts: list[dict],
    documents: list[dict],
    program_flags: dict,
    esr: dict | None = None,
) -> dict[str, Any]:
    """Deterministic Evidence Gap Analyzer & Per-Contention Pathway Suggester (RFP 5.1.1.7)."""
    exam_code = exam_info.get("examCode") or ""
    gaps: list[dict[str, str]] = []
    suggested_diagnostics: list[str] = []

    present_facts = [f for f in matched_facts if not f.get("isNegated") and not f.get("isFamilyHistory")]
    negated_facts = [f for f in matched_facts if f.get("isNegated")]

    has_readable_docs = bool(
        documents
        and any(
            not d.get("administrativeOnly") and d.get("classifiedPageCount", 0) > 0 and d.get("classificationStatus") != "excluded"
            for d in documents
        )
    )
    if not has_readable_docs:
        if not documents:
            gaps.append({
                "id": f"gap-{contention['id']}-no-readable",
                "severity": "critical",
                "gapGroup": "blocking-issue",
                "category": "missing-evidence",
                "message": "No evidence documents linked to this contention.",
                "recommendedAction": "Attach source records or confirm whether VA requested a baseline examination without prior records.",
            })
        else:
            gaps.append({
                "id": f"gap-{contention['id']}-no-historical",
                "severity": "warning",
                "gapGroup": "collect-during-exam",
                "category": "missing-historical-evidence",
                "message": "Readable historical clinical records are unavailable; examination will collect baseline findings.",
                "recommendedAction": "Proceed with In-Person/Telehealth examination (ACE records review is prohibited without readable records).",
            })

    # Context-aware contradiction check (concept + laterality + temporality)
    contradictions = detect_clinical_contradictions(matched_facts)
    if contradictions:
        c0 = contradictions[0]
        gaps.append({
            "id": f"gap-{contention['id']}-contradiction",
            "severity": "info",
            "gapGroup": "clinical-review-requirement",
            "category": "contradiction",
            "message": (
                f"Conflicting findings for {', '.join(c0['concepts'])} ({c0['laterality']}): "
                f"{c0['positiveCitation']} vs {c0['negatedCitation']}."
            ),
            "recommendedAction": "Inspect both cited excerpts directly in the evidence viewer.",
        })

    # Abnormal labs notice
    abnormal_labs = [
        fact for fact in present_facts
        if fact.get("bodyPart") == "laboratory" and (fact.get("measurements") or {}).get("abnormalLabs")
    ]
    if abnormal_labs:
        gaps.append({
            "id": f"gap-{contention['id']}-labs",
            "severity": "info",
            "gapGroup": "clinical-review-requirement",
            "category": "abnormal-lab",
            "message": "Abnormal laboratory findings documented in attached records; include in clinical review.",
            "recommendedAction": "Review extracted lab panels with examiner.",
        })

    # Condition-specific objective requirements
    has_recent_rom = False
    latest_rom_fact = None
    if exam_code in {"knee", "back", "shoulder", "neck"}:
        rom_facts = [
            f for f in present_facts
            if any(k in (f.get("measurements") or {}) for k in ("flexion", "extension", "abduction"))
        ]
        if rom_facts:
            latest_rom_fact = rom_facts[-1]
            age_yrs = _years_since(latest_rom_fact.get("clinicalDate", ""))
            has_recent_rom = age_yrs is not None and age_yrs <= 1.0
        if not rom_facts:
            gaps.append({
                "id": f"gap-{contention['id']}-rom",
                "severity": "warning",
                "gapGroup": "collect-during-exam",
                "category": "missing-measurements",
                "message": "Current physical range-of-motion (goniometer) measurements are not identified in the supplied records.",
                "recommendedAction": "In-person physical measurement and functional assessment may be required.",
            })
        elif not has_recent_rom:
            gaps.append({
                "id": f"gap-{contention['id']}-rom-stale",
                "severity": "warning",
                "gapGroup": "collect-during-exam",
                "category": "stale-measurements",
                "message": f"Historical ROM findings ({latest_rom_fact.get('clinicalDate') or 'undated'}) may be too old for current rating evaluation.",
                "recommendedAction": "Confirm whether updated in-person ROM measurement is required.",
            })

        has_imaging = any(
            d.get("category") == "imaging" or any(c.get("kind") == "imaging" for f in present_facts for c in f.get("codes", []))
            for d in documents if d.get("classificationStatus") != "excluded"
        )
        if not has_imaging and exam_code in {"knee", "back", "shoulder"}:
            suggested_diagnostics.append("Imaging report review")

    elif exam_code == "hearing":
        has_audio = any(
            "audiologyDb" in (f.get("measurements") or {})
            or "speechDiscriminationPercent" in (f.get("measurements") or {})
            or any(c.get("kind") == "audiogram" for c in f.get("codes", []))
            for f in present_facts
        )
        if not has_audio:
            gaps.append({
                "id": f"gap-{contention['id']}-audiogram",
                "severity": "warning",
                "gapGroup": "collect-during-exam",
                "category": "missing-diagnostics",
                "message": "Audiometric results (pure-tone thresholds / speech discrimination) were not identified in the supplied records.",
                "recommendedAction": "Review applicable audiometric testing requirements with a qualified audiologist.",
            })
            suggested_diagnostics.append("Audiology test")

    elif exam_code == "skin":
        has_bsa = any("bsaPercent" in (f.get("measurements") or {}) for f in present_facts)
        if not has_bsa:
            gaps.append({
                "id": f"gap-{contention['id']}-skin",
                "severity": "warning",
                "gapGroup": "collect-during-exam",
                "category": "insufficient-current-findings",
                "message": "Current body surface area (% BSA) affected and active flare-up extent are not documented.",
                "recommendedAction": "Evaluate current skin findings, evidence sufficiency, and applicable Tele-C&P or ACE eligibility.",
            })

    # Secondary recommendation gap (RFP 5.1.1.2) — require clinical/instructional basis, never condition/program flag alone
    secondary_recs = list(exam_info.get("secondaryRecommendations") or [])
    esr_instr_l = ((esr or {}).get("specialInstructions") or "").casefold()
    has_nexus_or_exposure_evidence = any(
        any(kw in (f.get("rawExcerpt") or "").casefold() for kw in ("burn pit", "toxic", "exposure", "agent orange", "nexus", "etiology"))
        for f in present_facts
    )
    if (
        program_flags.get("teraConfirmed")
        and not program_flags.get("isBddIdes")
        and "Medical Opinion DBQ" not in (exam_info.get("suggestedDbqs") or [])
        and (has_nexus_or_exposure_evidence or "medical opinion" in esr_instr_l)
    ):
        secondary_recs.append("Medical Opinion DBQ (Confirmed TERA with documented exposure/opinion context)")
    if secondary_recs:
        gaps.append({
            "id": f"gap-{contention['id']}-secondary-dbq",
            "severity": "info",
            "gapGroup": "clinical-review-requirement",
            "category": "additional-dbq-consideration",
            "message": f"Clinical records suggest considering: {', '.join(secondary_recs)}.",
            "recommendedAction": "Clinician to decide whether to add an additional DBQ (unless BVA remand restricts scope).",
        })

    # Detect if VA explicitly specified an examination pathway in ESR instructions or contention
    esr_instructions = (esr or {}).get("specialInstructions") or ""
    va_text = f"{esr_instructions} {contention.get('specialInstructions', '')} {contention.get('title', '')}".casefold()
    va_specified_pathway = ""
    if re.search(r"\b(in[-\s]?person\s+exam(?:ination)?|physical\s+exam\s+required)\b", va_text):
        va_specified_pathway = "in-person"
    elif re.search(r"\b(tele[-\s]?c&p|telehealth\s+exam(?:ination)?)\b", va_text):
        va_specified_pathway = "tele-c-and-p"
    elif re.search(r"\b(ace\s+(?:only|exam|records\s+review|eligible))\b", va_text):
        va_specified_pathway = "ace-records-review"

    # Centralized registry lookup (supports unmapped/dynamic conditions cleanly)
    reg = RULES_REGISTRY.get(exam_code)
    pathway_states = dict(reg["pathwayStates"]) if reg else {
        "in-person": "unverified",
        "tele-c-and-p": "unverified",
        "ace-records-review": "prohibited",
    }
    has_blocking_gap = any(g["gapGroup"] in {"blocking-issue", "collect-during-exam"} for g in gaps)

    # Evaluate ACE eligibility dynamically (never hardcoded unconditionally)
    ace_eligible = False
    if va_specified_pathway == "ace-records-review":
        ace_eligible = True
        ace_reason = "VA Examination Request instructions explicitly specify ACE records review."
    elif program_flags.get("isBddIdes") and exam_code == "sha":
        ace_reason = "Prohibited: BDD/IDES Separation Health Assessment requires physical verification (RFP 5.1.2)."
    elif program_flags.get("isBvaRemand") and (program_flags.get("remandDirectives") or {}).get("prohibitsAce", True):
        ace_reason = "Restricted: BVA Remand instructions do not authorize ACE records review (RFP 5.1.1.2)."
    elif va_specified_pathway in {"in-person", "tele-c-and-p"}:
        ace_reason = f"Restricted: Authoritative VA instructions specify '{va_specified_pathway}'."
    elif not reg:
        ace_reason = "Unverified: authoritative ACE eligibility rules are unavailable for this dynamic examination; requires authorized review."
    elif pathway_states.get("ace-records-review") == "prohibited":
        ace_reason = f"Prohibited: {reg['title']} requires physical examination or direct testing ({reg['sourceReference']})."
    elif pathway_states.get("ace-records-review") in {"conditional", "unverified"} and not has_blocking_gap and matched_pages:
        ace_eligible = True
        ace_reason = (
            f"Potentially ACE-eligible ({reg['sourceReference']}); requires explicit clinician authorization."
            if pathway_states.get("ace-records-review") == "conditional"
            else "ACE rules unverified in MDEO Handbook; requires authorized clinical review."
        )
    elif pathway_states.get("ace-records-review") in {"conditional", "unverified"}:
        ace_reason = "ACE deferred: attach cohesive readable clinical evidence without missing physical measurements first."
    else:
        ace_reason = "Unverified: authoritative ACE eligibility rules are unavailable."

    # Compute permitted modalities & conflict checks
    if va_specified_pathway:
        permitted_modalities = [va_specified_pathway]
        for m in ("in-person", "tele-c-and-p", "ace-records-review"):
            pathway_states[m] = "required" if m == va_specified_pathway else "prohibited"
    elif program_flags.get("isBddIdes") and exam_code == "sha":
        permitted_modalities = ["in-person"]
        pathway_states = {"in-person": "required", "tele-c-and-p": "prohibited", "ace-records-review": "prohibited"}
    else:
        permitted_modalities = [
            m for m in ("in-person", "tele-c-and-p")
            if pathway_states.get(m) in {"required", "permitted", "conditional", "unverified"}
        ] or ["in-person"]
        if ace_eligible and "ace-records-review" not in permitted_modalities:
            permitted_modalities.append("ace-records-review")

    modality_reasons: dict[str, str] = {}
    for mod in ("in-person", "tele-c-and-p", "ace-records-review"):
        if mod == "in-person":
            modality_reasons[mod] = (
                "Mandated by VA ESR instructions."
                if va_specified_pathway == "in-person"
                else f"Status: {pathway_states.get('in-person', 'unverified')}."
            )
        elif mod == "tele-c-and-p":
            modality_reasons[mod] = (
                f"Status: {pathway_states.get('tele-c-and-p', 'unverified')}."
                if mod in permitted_modalities
                else "Prohibited by authoritative VA/program instructions or physical testing requirements."
            )
        elif mod == "ace-records-review":
            modality_reasons[mod] = ace_reason

    rule_status = exam_info.get("ruleVerificationStatus", "unverified-requires-review")
    if va_specified_pathway == "in-person":
        proposed_pathway = "in-person-candidate"
        proposed_modality = "in-person"
        pathway_summary = "In-person examination — VA specified"
        pathway_reason = "Authoritative ESR instructions specify an in-person examination."
        rule_ref = "ESR Authoritative Instruction"
        requires_confirmation = True
    elif va_specified_pathway == "tele-c-and-p":
        proposed_pathway = "tele-c-and-p-candidate"
        proposed_modality = "tele-c-and-p"
        pathway_summary = "Tele-C&P — VA specified"
        pathway_reason = "Authoritative ESR instructions specify Tele-C&P examination."
        rule_ref = "ESR Authoritative Instruction"
        requires_confirmation = True
    elif va_specified_pathway == "ace-records-review":
        proposed_pathway = "ace-candidate"
        proposed_modality = "ace-records-review"
        pathway_summary = "ACE records review — VA specified"
        pathway_reason = "Authoritative ESR instructions specify ACE records review; confirm clinician authorization."
        rule_ref = "ESR Authoritative Instruction · RFP 5.1.1.7"
        requires_confirmation = True
    elif reg and pathway_states.get("in-person") == "required":
        proposed_pathway = "in-person-candidate"
        proposed_modality = "in-person"
        pathway_summary = f"In-person {reg['title']} — required"
        pathway_reason = f"Physical examination or specialized testing required ({reg['sourceReference']})."
        rule_ref = reg["sourceReference"]
        requires_confirmation = True
    elif program_flags.get("isBddIdes") and program_flags.get("radStatus") in {"missing", "invalid"}:
        rule_status = "unverified-requires-review"
        proposed_pathway = "requires-clinical-review"
        proposed_modality = ""
        pathway_summary = "BDD/IDES RAD Date Review Required"
        pathway_reason = "Missing or invalid Release from Active Duty (RAD) date; authorized clinician must confirm SHA vs General Medical DBQ and pathway."
        rule_ref = "RFP 5.1.2 · Authorized Review Required"
        requires_confirmation = True
    elif program_flags.get("isBvaRemand"):
        proposed_pathway = "in-person-candidate"
        proposed_modality = "in-person"
        pathway_summary = "In-person examination — candidate (BVA Remand)"
        pathway_reason = "BVA Remand scope cannot bypass required examination steps via ACE without explicit authorization (RFP 5.1.1.2)."
        rule_ref = "RFP 5.1.1.2 · BVA Remand"
        requires_confirmation = True
    elif reg and exam_code in {"knee", "back", "shoulder", "neck"}:
        proposed_pathway = "in-person-candidate"
        proposed_modality = "in-person"
        pathway_summary = "In-person examination — candidate"
        pathway_reason = "Current physical measurements (ROM / palpation) and functional assessment may be necessary."
        rule_ref = reg["sourceReference"]
        requires_confirmation = True
    elif reg and ace_eligible and pathway_states.get("ace-records-review") == "conditional" and not has_blocking_gap and matched_pages:
        proposed_pathway = "ace-candidate"
        proposed_modality = "ace-records-review" if contention.get("aceAuthorizationReference") else "tele-c-and-p"
        pathway_summary = "ACE or Tele-C&P — candidate"
        pathway_reason = "Existing evidence appears cohesive without blocking physical gaps. ACE eligibility still requires explicit clinician approval (RFP 5.1.1.7)."
        rule_ref = reg["sourceReference"]
        requires_confirmation = True
    elif reg and (pathway_states.get("tele-c-and-p") == "permitted" or (pathway_states.get("tele-c-and-p") == "conditional" and not has_blocking_gap)) and matched_pages and rule_status == "verified":
        proposed_pathway = "tele-c-and-p-candidate"
        proposed_modality = "tele-c-and-p"
        pathway_summary = "Tele-C&P — candidate"
        pathway_reason = "Evaluate remote Telehealth interview eligibility and check remaining clinical gaps."
        rule_ref = reg["sourceReference"]
        requires_confirmation = True
    elif reg and rule_status == "verified":
        proposed_pathway = "requires-clinical-review"
        proposed_modality = ""
        pathway_summary = f"{reg['title']} — clinical review"
        pathway_reason = "Evaluate current clinical findings, evidence sufficiency, and applicable In-Person or Tele-C&P pathway."
        rule_ref = reg["sourceReference"]
        requires_confirmation = True
    else:
        rule_status = "unverified-requires-review"
        proposed_pathway = "requires-clinical-review"
        proposed_modality = ""
        pathway_summary = "Unverified — requires authorized clinical review"
        pathway_reason = "Authoritative VA pathway/MDEO Handbook rules are unavailable for this condition; authorized clinician review or VA clarification is required."
        rule_ref = "Unverified Rule · MDEO Examinations Handbook Unavailable"
        requires_confirmation = True

    # Consolidate any unverified rule / qualification notice into ONE clear actionable notice
    if rule_status != "verified":
        gaps.append({
            "id": f"gap-{contention['id']}-unverified-rule",
            "severity": "warning",
            "gapGroup": "clinical-review-requirement",
            "category": "unverified-rule",
            "message": "Authoritative VA rule or MDEO Examinations Handbook mapping is unavailable/unverified for this requirement.",
            "recommendedAction": "Authorized clinical reviewer must verify examiner qualifications and pathway or request VA clarification.",
        })

    return {
        "evidenceGaps": gaps,
        "contradictions": contradictions,
        "suggestedDiagnostics": suggested_diagnostics,
        "vaSpecifiedPathway": va_specified_pathway,
        "permittedModalities": permitted_modalities,
        "pathwayStates": pathway_states,
        "modalityEligibilityReasons": modality_reasons,
        "aceEligible": ace_eligible,
        "aceEligibilityReason": ace_reason,
        "ruleVerificationStatus": rule_status,
        "proposedPathway": proposed_pathway,
        "proposedModality": proposed_modality,
        "pathwaySummary": pathway_summary,
        "pathwayReason": pathway_reason,
        "ruleReference": rule_ref,
        "requiresConfirmation": requires_confirmation,
        "aceCandidate": proposed_pathway == "ace-candidate" or ace_eligible,
        "pathwayAssessment": {
            "regulatoryEligibility": {
                "permittedModalities": permitted_modalities,
                "pathwayStates": pathway_states,
                "vaSpecifiedPathway": va_specified_pathway,
                "ruleVerificationStatus": rule_status,
                "sourceReference": exam_info.get("ruleSourceReference", rule_ref),
            },
            "clinicalSuitability": {
                "hasReadableRecords": has_readable_docs,
                "hasPhysicalGap": has_blocking_gap,
                "aceEligible": ace_eligible,
                "aceEligibilityReason": ace_reason,
            },
            "aiRecommendation": {
                "proposedPathway": proposed_pathway,
                "proposedModality": proposed_modality,
                "pathwaySummary": pathway_summary,
                "pathwayReason": pathway_reason,
                "advisoryOnly": True,
                "requiresClinicalConfirmation": True,
            },
        },
    }
