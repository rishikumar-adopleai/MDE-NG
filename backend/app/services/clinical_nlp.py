"""Medical NLP, medical code crosswalk, negation detection, and transparent evidence search."""

from __future__ import annotations

import hashlib
import re
from datetime import datetime
from typing import Any

# Crosswalk of common VA C&P medical codes -> normalized clinical concepts
ICD10_CROSSWALK: dict[str, dict[str, str]] = {
    "M25.561": {"display": "Pain in right knee", "bodyPart": "knee", "laterality": "right", "examCode": "knee"},
    "M25.562": {"display": "Pain in left knee", "bodyPart": "knee", "laterality": "left", "examCode": "knee"},
    "M25.569": {"display": "Pain in unspecified knee", "bodyPart": "knee", "laterality": "unspecified", "examCode": "knee"},
    "M17.11": {"display": "Unilateral primary osteoarthritis, right knee", "bodyPart": "knee", "laterality": "right", "examCode": "knee"},
    "M17.12": {"display": "Unilateral primary osteoarthritis, left knee", "bodyPart": "knee", "laterality": "left", "examCode": "knee"},
    "S83.511A": {"display": "Sprain of anterior cruciate ligament of right knee", "bodyPart": "knee", "laterality": "right", "examCode": "knee"},
    "S83.512A": {"display": "Sprain of anterior cruciate ligament of left knee", "bodyPart": "knee", "laterality": "left", "examCode": "knee"},
    "M25.511": {"display": "Pain in right shoulder", "bodyPart": "shoulder", "laterality": "right", "examCode": "shoulder"},
    "M25.512": {"display": "Pain in left shoulder", "bodyPart": "shoulder", "laterality": "left", "examCode": "shoulder"},
    "M75.101": {"display": "Rotator cuff tear or rupture, right shoulder", "bodyPart": "shoulder", "laterality": "right", "examCode": "shoulder"},
    "M75.102": {"display": "Rotator cuff tear or rupture, left shoulder", "bodyPart": "shoulder", "laterality": "left", "examCode": "shoulder"},
    "M54.5": {"display": "Low back pain", "bodyPart": "back", "laterality": "unspecified", "examCode": "back"},
    "M54.50": {"display": "Low back pain, unspecified", "bodyPart": "back", "laterality": "unspecified", "examCode": "back"},
    "M54.16": {"display": "Radiculopathy, lumbar region", "bodyPart": "back", "laterality": "unspecified", "examCode": "back"},
    "M54.2": {"display": "Cervicalgia (neck pain)", "bodyPart": "neck", "laterality": "unspecified", "examCode": "neck"},
    "H90.3": {"display": "Sensorineural hearing loss, bilateral", "bodyPart": "ear", "laterality": "bilateral", "examCode": "hearing"},
    "H90.5": {"display": "Unspecified sensorineural hearing loss", "bodyPart": "ear", "laterality": "unspecified", "examCode": "hearing"},
    "H91.90": {"display": "Unspecified hearing loss", "bodyPart": "ear", "laterality": "unspecified", "examCode": "hearing"},
    "H93.13": {"display": "Tinnitus, bilateral", "bodyPart": "ear", "laterality": "bilateral", "examCode": "hearing"},
    "L23.9": {"display": "Allergic contact dermatitis, unspecified cause", "bodyPart": "skin", "laterality": "unspecified", "examCode": "skin"},
    "L24.9": {"display": "Irritant contact dermatitis, unspecified cause", "bodyPart": "skin", "laterality": "unspecified", "examCode": "skin"},
    "L20.9": {"display": "Atopic dermatitis (eczema)", "bodyPart": "skin", "laterality": "unspecified", "examCode": "skin"},
    "L40.0": {"display": "Psoriasis vulgaris", "bodyPart": "skin", "laterality": "unspecified", "examCode": "skin"},
    "F43.10": {"display": "Post-traumatic stress disorder (PTSD), unspecified", "bodyPart": "mental", "laterality": "unspecified", "examCode": "mental"},
    "F43.12": {"display": "Post-traumatic stress disorder (PTSD), chronic", "bodyPart": "mental", "laterality": "unspecified", "examCode": "mental"},
    "F32.9": {"display": "Major depressive disorder, unspecified", "bodyPart": "mental", "laterality": "unspecified", "examCode": "mental"},
    "F41.1": {"display": "Generalized anxiety disorder", "bodyPart": "mental", "laterality": "unspecified", "examCode": "mental"},
    "G43.909": {"display": "Migraine, unspecified, not intractable", "bodyPart": "head", "laterality": "unspecified", "examCode": "migraine"},
    "G44.1": {"display": "Vascular headache / chronic headaches", "bodyPart": "head", "laterality": "unspecified", "examCode": "migraine"},
    "G62.9": {"display": "Polyneuropathy / peripheral neuropathy", "bodyPart": "nerve", "laterality": "bilateral", "examCode": "nerve"},
    "G57.00": {"display": "Lesion of sciatic nerve", "bodyPart": "nerve", "laterality": "unspecified", "examCode": "nerve"},
    "E11.9": {"display": "Type 2 diabetes mellitus without complications", "bodyPart": "endocrine", "laterality": "unspecified", "examCode": "diabetes"},
    "E11.42": {"display": "Type 2 diabetes mellitus with diabetic polyneuropathy", "bodyPart": "endocrine", "laterality": "unspecified", "examCode": "diabetes"},
    "J45.909": {"display": "Unspecified asthma, uncomplicated", "bodyPart": "respiratory", "laterality": "unspecified", "examCode": "respiratory"},
    "I10": {"display": "Essential (primary) hypertension", "bodyPart": "cardiovascular", "laterality": "unspecified", "examCode": "cardiovascular"},
}

CPT_CROSSWALK: dict[str, dict[str, str]] = {
    "92557": {"display": "Comprehensive audiometry threshold evaluation & speech recognition", "examCode": "hearing", "kind": "audiogram"},
    "92550": {"display": "Tympanometry and reflex threshold measurements", "examCode": "hearing", "kind": "audiogram"},
    "73560": {"display": "Radiologic examination (X-ray), knee; 1 or 2 views", "examCode": "knee", "kind": "imaging"},
    "73562": {"display": "Radiologic examination (X-ray), knee; 3 views", "examCode": "knee", "kind": "imaging"},
    "73721": {"display": "MRI any joint of lower extremity (without contrast)", "examCode": "knee", "kind": "imaging"},
    "73030": {"display": "Radiologic examination (X-ray), shoulder", "examCode": "shoulder", "kind": "imaging"},
    "72100": {"display": "Radiologic examination (X-ray), spine, lumbosacral", "examCode": "back", "kind": "imaging"},
    "72148": {"display": "MRI spinal canal, lumbar", "examCode": "back", "kind": "imaging"},
    "95886": {"display": "Needle electromyography (EMG), complete extremity", "examCode": "nerve", "kind": "neuro"},
    "95910": {"display": "Nerve conduction studies (7-8 studies)", "examCode": "nerve", "kind": "neuro"},
    "94010": {"display": "Spirometry / Pulmonary Function Test (PFT)", "examCode": "respiratory", "kind": "pft"},
}

VASRD_CROSSWALK: dict[str, dict[str, str]] = {
    "5260": {"display": "Leg, limitation of flexion of", "bodyPart": "knee", "examCode": "knee"},
    "5261": {"display": "Leg, limitation of extension of", "bodyPart": "knee", "examCode": "knee"},
    "5257": {"display": "Knee, other impairment of (recurrent subluxation/instability)", "bodyPart": "knee", "examCode": "knee"},
    "5201": {"display": "Arm, limitation of motion of", "bodyPart": "shoulder", "examCode": "shoulder"},
    "5237": {"display": "Lumbosacral or cervical strain", "bodyPart": "back", "examCode": "back"},
    "6100": {"display": "Impairment of auditory acuity (hearing loss)", "bodyPart": "ear", "examCode": "hearing"},
    "6260": {"display": "Tinnitus, recurrent", "bodyPart": "ear", "examCode": "hearing"},
    "7806": {"display": "Dermatitis or eczema", "bodyPart": "skin", "examCode": "skin"},
    "8100": {"display": "Migraine", "bodyPart": "head", "examCode": "migraine"},
    "8520": {"display": "Sciatic nerve paralysis / neuropathy", "bodyPart": "nerve", "examCode": "nerve"},
    "9411": {"display": "Posttraumatic stress disorder (PTSD)", "bodyPart": "mental", "examCode": "mental"},
    "7913": {"display": "Diabetes mellitus", "bodyPart": "endocrine", "examCode": "diabetes"},
    "6602": {"display": "Asthma, bronchial", "bodyPart": "respiratory", "examCode": "respiratory"},
    "7101": {"display": "Hypertensive vascular disease (hypertension)", "bodyPart": "cardiovascular", "examCode": "cardiovascular"},
}

BODY_PART_SYNONYMS: dict[str, tuple[str, ...]] = {
    "knee": ("knee", "patella", "patellofemoral", "meniscus", "cruciate", "acl", "mcl", "tibiofemoral", "genu", "flexion", "extension", "lower leg", "gait", "stairs"),
    "shoulder": ("shoulder", "rotator cuff", "acromioclavicular", "deltoid", "glenohumeral", "clavicle", "abduction", "arm", "overhead"),
    "back": ("back", "lumbar", "lumbosacral", "thoracolumbar", "spine", "spinal", "disc", "herniated", "sciatica", "vertebra"),
    "neck": ("neck", "cervical", "cervicalgia", "c-spine"),
    "ear": ("hearing", "auditory", "audiology", "audiogram", "audiometric", "tinnitus", "ringing", "ear", "cochlear", "acoustic", "decibel", "db", "speech discrimination", "noise exposure", "machinery"),
    "skin": ("skin", "dermatitis", "eczema", "rash", "lesion", "pruritus", "itching", "erythema", "plaque", "psoriasis", "scar", "topical", "hydrocortisone", "triamcinolone", "bsa"),
    "mental": ("ptsd", "depression", "depressive", "anxiety", "panic", "insomnia", "nightmare", "trauma", "stressor", "mental", "psychiatric", "mood", "counseling", "behavioral"),
    "head": ("headache", "migraine", "prostrating", "photophobia", "phonophobia", "aura", "head pain", "sumatriptan"),
    "nerve": ("nerve", "neuropathy", "radiculopathy", "paresthesia", "numbness", "tingling", "sciatic", "carpal tunnel", "emg", "weakness"),
    "endocrine": ("diabetes", "a1c", "hba1c", "insulin", "metformin", "glucose", "hyperglycemia", "hypoglycemia", "endocrine"),
    "respiratory": ("asthma", "bronchitis", "copd", "sleep apnea", "cpap", "shortness of breath", "dyspnea", "wheezing", "inhaler", "albuterol", "spirometry", "fev1"),
    "cardiovascular": ("hypertension", "blood pressure", "bp", "cardiac", "heart", "arrhythmia", "chest pain", "coronary"),
}

CONTENTION_TO_BODY_PARTS: dict[str, tuple[str, ...]] = {
    "knee": ("knee",),
    "shoulder": ("shoulder",),
    "back": ("back",),
    "neck": ("neck", "back"),
    "hearing": ("ear",),
    "tinnitus": ("ear",),
    "skin": ("skin",),
    "dermatitis": ("skin",),
    "eczema": ("skin",),
    "rash": ("skin",),
    "mental": ("mental",),
    "ptsd": ("mental",),
    "depression": ("mental",),
    "anxiety": ("mental",),
    "migraine": ("head",),
    "headache": ("head",),
    "nerve": ("nerve",),
    "neuropathy": ("nerve",),
    "radiculopathy": ("nerve",),
    "diabetes": ("endocrine",),
    "asthma": ("respiratory",),
    "sleep apnea": ("respiratory",),
    "hypertension": ("cardiovascular",),
}

NON_CLINICAL_CATEGORIES = {
    "va-request",
    "examination-requests",
    "claims-and-applications",
    "authorization-consent",
    "representation",
    "administrative-correspondence",
    "insurance-financial",
    "military-personnel",
}

BOILERPLATE_LINE_RE = re.compile(
    r"^(?:section\s+[ivx0-9]+|va\s+form|omb\s+control|privacy\s+act|synthetic\s+test\s+data|"
    r"mde-ng\s+ocr|note:\s+this\s+cover|does\s+the\s+veteran|did\s+the\s+veteran|is\s+there|indicate\s+|check\s+(?:all|one)|"
    r"if\s+checked|if\s+yes|explain\s+in|respondent\s+burden|page\s+\d+\s+of\s+\d+|"
    r"please\s+(?:identify|list|provide|specify|describe|explain|complete)|"
    r"(?:name\s+of\s+veteran|veteran'?s\s+name|date\s+of\s+exam|claim\s+number|social\s+security)|"
    r"symptoms,\s*diagnosis,\s*treatment|treating\s+organization|sign\s+each\s+entry|"
    r"fictional\s+service\s+treatment|e\.g\.\s*,?\s*service\s+treatment)",
    re.IGNORECASE,
)


def is_clinical_document(doc: dict) -> bool:
    if doc.get("classificationStatus") == "excluded" or doc.get("isExcluded") or doc.get("administrativeOnly"):
        return False
    if doc.get("category") in NON_CLINICAL_CATEGORIES:
        return False
    title_l = f"{doc.get('title', '')} {doc.get('sourceReference', '')}".casefold()
    if any(tok in title_l for tok in ("21-526", "21-4142", "21-22", "21-10210", "eob", "explanation of benefits", "esr_intake_cover")):
        return False
    return True

ABBREVIATION_EXPANSIONS: dict[str, tuple[str, ...]] = {
    "sob": ("shortness of breath", "dyspnea"),
    "htn": ("hypertension", "high blood pressure"),
    "gerd": ("gastroesophageal reflux", "reflux", "heartburn", "esophagitis"),
    "dm": ("diabetes mellitus", "diabetes", "a1c", "glucose"),
    "dm2": ("type 2 diabetes", "diabetes mellitus", "a1c", "glucose"),
    "osa": ("obstructive sleep apnea", "sleep apnea", "cpap"),
    "rom": ("range of motion", "flexion", "extension", "goniometer"),
    "oa": ("osteoarthritis", "degenerative joint disease", "arthritis"),
    "djd": ("degenerative joint disease", "osteoarthritis", "arthritis"),
    "ckd": ("chronic kidney disease", "renal", "egfr", "creatinine"),
    "cad": ("coronary artery disease", "ischemic heart", "angina"),
    "bph": ("benign prostatic hyperplasia", "prostate", "urinary"),
    "ra": ("rheumatoid arthritis", "inflammatory arthritis"),
    "sle": ("lupus", "systemic lupus erythematosus"),
    "ibs": ("irritable bowel syndrome", "abdominal pain", "bowel"),
    "ptsd": ("post-traumatic stress disorder", "trauma", "nightmare"),
    "tbi": ("traumatic brain injury", "concussion", "head injury"),
    "pft": ("pulmonary function test", "spirometry", "fev1"),
    "emg": ("electromyography", "nerve conduction"),
    "lbp": ("low back pain", "lumbar"),
    "copd": ("chronic obstructive pulmonary disease", "emphysema", "bronchitis"),
}

CONTENTION_STOPWORDS = {
    "condition", "conditions", "disability", "status", "post", "and", "the", "due",
    "secondary", "with", "without", "for", "bilateral", "left", "right", "unspecified",
    "claimed", "chronic", "acute", "evaluation", "exam", "examination", "dbq", "of", "to",
}

PRE_NEGATION_RE = re.compile(
    r"\b(denies|denied|no\s+(?:evidence|history|signs?|report|complaints?|indication)\s+of|"
    r"negative\s+for|without\s+(?:evidence\s+of)?|rules?\s+out|ruled\s+out|no\s+acute|"
    r"no\s+current|unremarkable\s+for|free\s+of|not\s+present)\b",
    re.IGNORECASE,
)
POST_NEGATION_RE = re.compile(r"\b(denied|absent|ruled\s+out|negative|not\s+found)\b", re.IGNORECASE)
RESOLVED_RE = re.compile(r"\b(resolved|remission|healed|subsided|no\s+longer\s+present)\b", re.IGNORECASE)
SUSPECTED_RE = re.compile(r"\b(suspected|possible|probable|rule\s+out|likely|presumed|differential|questionable)\b", re.IGNORECASE)
FAMILY_HISTORY_RE = re.compile(r"\b(family\s+history|mother|father|sibling|brother|sister|parent)\b", re.IGNORECASE)
HISTORICAL_RE = re.compile(r"\b(history\s+of|prior|previous|historical|past\s+medical|in\s+20\d{2}|in\s+19\d{2}|years?\s+ago)\b", re.IGNORECASE)
TREATMENT_RE = re.compile(
    r"\b(sumatriptan|albuterol|insulin|metformin|cpap|physical\s+therapy|pt\b|surgery|arthroscopy|"
    r"brace|nsaid|ibuprofen|naproxen|meloxicam|steroid|prednisone|hydrocortisone|triamcinolone|"
    r"omeprazole|pantoprazole|lisinopril|amlodipine|sertraline|counseling|psychotherapy|inhaler)\b",
    re.IGNORECASE,
)
FUNCTIONAL_RE = re.compile(
    r"\b(unable\s+to|difficulty\s+(?:walking|standing|lifting|bending|sleeping|working|climbing)|"
    r"missed\s+work|prostrating|incapacitating|limited\s+motion|painful\s+motion|instability|weakness|flare[-\s]?up)\b",
    re.IGNORECASE,
)
CLINICAL_STATEMENT_RE = re.compile(
    r"\b(dx|diagnosis|diagnosed|assessment|impression|chronic|pain|disorder|disease|syndrome|"
    r"injury|fracture|tear|strain|sprain|lesion|ulcer|reflux|infection|hypertrophy|carcinoma|"
    r"therapy|treatment|medication|prescribed|symptom|complaints?|abnormal|elevated)\b",
    re.IGNORECASE,
)
DISTRESS_RE = re.compile(
    r"\b(suicidal\s+ideation|thoughts?\s+of\s+self[-\s]?harm|kill\s+myself|want\s+to\s+die|"
    r"active\s+suicide|self[-\s]?harm\s+plan|imminent\s+danger\s+to\s+self)\b",
    re.IGNORECASE,
)

DATE_ISO_RE = re.compile(r"\b(20\d{2}|19\d{2})-(0[1-9]|1[0-2])-(0[1-9]|[12]\d|3[01])\b")
DATE_US_RE = re.compile(r"\b(0?[1-9]|1[0-2])/(0?[1-9]|[12]\d|3[01])/(19\d{2}|20\d{2})\b")
DATE_TEXT_RE = re.compile(
    r"\b(Jan(?:uary)?|Feb(?:ruary)?|Mar(?:ch)?|Apr(?:il)?|May|Jun(?:e)?|Jul(?:y)?|"
    r"Aug(?:ust)?|Sep(?:t(?:ember)?)?|Oct(?:ober)?|Nov(?:ember)?|Dec(?:ember)?)"
    r"\.?\s+(\d{1,2}),?\s+(19\d{2}|20\d{2})\b",
    re.IGNORECASE,
)
MONTH_MAP = {
    "jan": 1, "feb": 2, "mar": 3, "apr": 4, "may": 5, "jun": 6,
    "jul": 7, "aug": 8, "sep": 9, "oct": 10, "nov": 11, "dec": 12,
}

FLEXION_RE = re.compile(r"\bflexion(?:\s+(?:to|at|of|limited\s+to|measured\s+at|is|:|=))?\s*(\d{1,3})\s*(?:°|deg(?:rees?)?)?", re.IGNORECASE)
EXTENSION_RE = re.compile(r"\bextension(?:\s+(?:to|at|of|limited\s+to|measured\s+at|is|:|=))?\s*(-?\d{1,3})\s*(?:°|deg(?:rees?)?)?", re.IGNORECASE)
PAINFUL_MOTION_RE = re.compile(r"\bpain(?:ful\s+motion)?(?:\s+(?:begins?|onset|at))?\s*(\d{1,3})\s*(?:°|deg(?:rees?)?)", re.IGNORECASE)
ABDUCTION_RE = re.compile(r"\babduction(?:\s+(?:to|at|of|is|:|=))?\s*(\d{1,3})\s*(?:°|deg(?:rees?)?)?", re.IGNORECASE)
BSA_RE = re.compile(r"\b(\d{1,3})\s*%\s*(?:BSA|body\s+surface\s+area|skin\s+surface)\b", re.IGNORECASE)
AUDIOLOGY_DB_RE = re.compile(r"\b(\d{2,3})\s*dB(?:\s*(?:HL|at\s+\d+\s*Hz))?\b", re.IGNORECASE)
SPEECH_DISCRIM_RE = re.compile(r"\bspeech\s+(?:discrimination|recognition)[^.\d]{0,20}(\d{1,3})\s*%", re.IGNORECASE)
ICD10_RE = re.compile(r"\b([A-TV-Z]\d{2}(?:\.[0-9A-TV-Z]{1,4})?)\b")
CPT_RE = re.compile(r"\bCPT[:\s#]*([0-9]{5})\b|\b(92557|92550|73560|73562|73721|73030|72100|72148|95886|95910|94010)\b", re.IGNORECASE)
VASRD_RE = re.compile(r"\b(?:DC|Diagnostic\s+Code|VASRD)[:\s#]*(\d{4})\b", re.IGNORECASE)


def parse_clinical_date(text: str) -> str | None:
    if match := DATE_ISO_RE.search(text):
        return match.group(0)
    if match := DATE_US_RE.search(text):
        m, d, y = int(match.group(1)), int(match.group(2)), int(match.group(3))
        return f"{y:04d}-{m:02d}-{d:02d}"
    if match := DATE_TEXT_RE.search(text):
        mon = MONTH_MAP.get(match.group(1)[:3].lower())
        if mon:
            d, y = int(match.group(2)), int(match.group(3))
            return f"{y:04d}-{mon:02d}-{d:02d}"
    return None


def detect_laterality(text: str) -> str:
    lower = text.casefold()
    has_left = bool(re.search(r"\bleft\b|\blt\b|\b(?:os)\b", lower))
    has_right = bool(re.search(r"\bright\b|\brt\b|\b(?:od)\b", lower))
    has_bi = "bilateral" in lower or (has_left and has_right)
    if has_bi:
        return "bilateral"
    if has_left:
        return "left"
    if has_right:
        return "right"
    return "unspecified"


def extract_codes(text: str) -> list[dict[str, str]]:
    found: list[dict[str, str]] = []
    seen: set[tuple[str, str]] = set()
    for match in ICD10_RE.finditer(text):
        code = match.group(1).upper()
        if ("ICD-10", code) not in seen:
            seen.add(("ICD-10", code))
            if code in ICD10_CROSSWALK:
                info = ICD10_CROSSWALK[code]
                found.append({"system": "ICD-10-CM", "code": code, "display": info["display"],
                              "bodyPart": info["bodyPart"], "laterality": info["laterality"], "examCode": info["examCode"]})
            else:
                found.append({"system": "ICD-10-CM", "code": code, "display": f"ICD-10-CM {code}",
                              "bodyPart": "general", "laterality": "unspecified", "examCode": ""})
    for match in CPT_RE.finditer(text):
        code = (match.group(1) or match.group(2) or "").strip()
        if code in CPT_CROSSWALK and ("CPT", code) not in seen:
            seen.add(("CPT", code))
            info = CPT_CROSSWALK[code]
            found.append({"system": "CPT", "code": code, "display": info["display"],
                          "examCode": info["examCode"], "kind": info["kind"]})
    for match in VASRD_RE.finditer(text):
        code = match.group(1).strip()
        if code in VASRD_CROSSWALK and ("VASRD", code) not in seen:
            seen.add(("VASRD", code))
            info = VASRD_CROSSWALK[code]
            found.append({"system": "VASRD", "code": code, "display": info["display"],
                          "bodyPart": info["bodyPart"], "examCode": info["examCode"]})
    return found


def extract_measurements(text: str) -> dict[str, Any]:
    measurements: dict[str, Any] = {}
    if match := FLEXION_RE.search(text):
        measurements["flexion"] = int(match.group(1))
        measurements["unit"] = "degrees"
    if match := EXTENSION_RE.search(text):
        measurements["extension"] = int(match.group(1))
        measurements["unit"] = "degrees"
    if match := ABDUCTION_RE.search(text):
        measurements["abduction"] = int(match.group(1))
        measurements["unit"] = "degrees"
    if match := PAINFUL_MOTION_RE.search(text):
        measurements["painfulMotion"] = int(match.group(1))
        measurements["unit"] = "degrees"
    if match := BSA_RE.search(text):
        measurements["bsaPercent"] = int(match.group(1))
    if db_vals := [int(m.group(1)) for m in AUDIOLOGY_DB_RE.finditer(text)]:
        measurements["audiologyDb"] = db_vals[:8]
    if match := SPEECH_DISCRIM_RE.search(text):
        measurements["speechDiscriminationPercent"] = int(match.group(1))
    return measurements


def document_pages_for_nlp(doc: dict) -> list[dict[str, Any]]:
    """Collect all readable page texts for a document without dropping any pages."""
    processing = doc.get("processing") or {}
    raw_pages = processing.get("allPages") or []
    seen_pages: dict[int, str] = {}
    for entry in raw_pages:
        p = int(entry.get("page") or 1)
        txt = (entry.get("text") or "").strip()
        if txt:
            seen_pages[p] = txt
    if not seen_pages:
        for entry in processing.get("sources") or []:
            p = int(entry.get("page") or 1)
            txt = (entry.get("text") or "").strip()
            if txt:
                seen_pages[p] = txt
    if not seen_pages:
        for entry in doc.get("syntheticExcerpts") or []:
            p = int(entry.get("pageNumber") or entry.get("page") or 1)
            txt = (entry.get("text") or "").strip()
            if txt:
                seen_pages[p] = txt
    if not seen_pages:
        for sec in doc.get("sections") or []:
            p = int(sec.get("startPage") or 1)
            txt = (sec.get("summary") or "").strip()
            if txt:
                seen_pages[p] = txt
    if not seen_pages and processing.get("summary"):
        seen_pages[1] = str(processing["summary"]).strip()
    return [{"page": p, "text": seen_pages[p]} for p in sorted(seen_pages)]


def split_clinical_sentences(page_text: str) -> list[str]:
    lines = [line.strip() for line in page_text.splitlines() if line.strip()]
    sentences: list[str] = []
    for line in lines:
        if BOILERPLATE_LINE_RE.match(line):
            continue
        if line.endswith("?") or ("[" in line and "]" in line and ("yes" in line.lower() or "no" in line.lower())):
            continue
        if line.isupper() and len(line) < 90:
            continue
        parts = re.split(r"(?<=[.!?;])\s+(?=[A-Z0-9])", line)
        for p in parts:
            cleaned = p.strip()
            if len(cleaned) < 12 or cleaned.endswith("?") or BOILERPLATE_LINE_RE.match(cleaned):
                continue
            if cleaned.isupper() and len(cleaned) < 90:
                continue
            alpha = [c for c in cleaned if c.isalpha()]
            if alpha and (sum(1 for c in alpha if c.isupper()) / len(alpha)) >= 0.72 and len(alpha) >= 15:
                continue
            if len(cleaned.split()) <= 3 and not re.search(r"\d", cleaned):
                continue
            sentences.append(cleaned)
    return sentences


def _extract_concept_tokens(text: str) -> list[str]:
    tokens = re.findall(r"[a-z0-9]{3,}", text.casefold())
    expanded: list[str] = []
    for tok in tokens:
        if tok in CONTENTION_STOPWORDS or tok.isdigit():
            continue
        expanded.append(tok)
        for syn in ABBREVIATION_EXPANSIONS.get(tok, ()):
            expanded.extend(w for w in re.findall(r"[a-z0-9]{3,}", syn) if w not in CONTENTION_STOPWORDS)
    return list(dict.fromkeys(expanded))


def extract_facts_for_document(esr_id: str, doc: dict) -> dict[str, Any]:
    """Run deterministic Medical NLP + code crosswalk + measurement/safety extraction over every page of a document."""
    pages = document_pages_for_nlp(doc)
    doc_id = doc["id"]
    doc_title = doc.get("title") or doc.get("sourceReference") or doc_id
    facts: list[dict[str, Any]] = []
    distress_alerts: list[dict[str, Any]] = []

    for page_obj in pages:
        page_num = page_obj["page"]
        page_text = page_obj["text"]
        page_date = parse_clinical_date(page_text[:450])

        for sentence in split_clinical_sentences(page_text):
            sent_lower = sentence.casefold()
            is_negated = bool(PRE_NEGATION_RE.search(sentence) or POST_NEGATION_RE.search(sentence))
            is_resolved = bool(RESOLVED_RE.search(sentence))
            is_suspected = bool(SUSPECTED_RE.search(sentence))
            is_family = bool(FAMILY_HISTORY_RE.search(sentence))
            is_historical = bool(HISTORICAL_RE.search(sentence))
            sent_date = parse_clinical_date(sentence) or page_date

            # Sentence-level safety & distress detection (skip negated distress; distinguish historical vs current)
            if DISTRESS_RE.search(sentence) and not is_negated and not is_family:
                temporality = "historical" if (is_historical or is_resolved) else "current"
                distress_alerts.append({
                    "id": f"distress-{doc_id}-{page_num}-{hashlib.sha256(sentence.encode()).hexdigest()[:8]}",
                    "esrId": esr_id,
                    "documentId": doc_id,
                    "documentTitle": doc_title,
                    "pageNumber": page_num,
                    "clinicalDate": sent_date or "",
                    "temporality": temporality,
                    "severity": "historical-review" if temporality == "historical" else "urgent",
                    "escalationStatus": "historical-note" if temporality == "historical" else "pending-escalation",
                    "excerpt": sentence[:320],
                    "reason": (
                        "Historical crisis/self-harm mention detected; verify current clinical safety status."
                        if temporality == "historical"
                        else "Potential active crisis/self-harm indicator detected in clinical documentation. Immediate staff escalation required."
                    ),
                })

            codes = extract_codes(sentence)
            measurements = extract_measurements(sentence)
            treatments = list(dict.fromkeys(m.group(0).lower() for m in TREATMENT_RE.finditer(sentence)))
            functional = list(dict.fromkeys(m.group(0).lower() for m in FUNCTIONAL_RE.finditer(sentence)))
            matched_parts = [
                part for part, terms in BODY_PART_SYNONYMS.items()
                if any(re.search(rf"\b{re.escape(term)}\b", sent_lower) for term in terms)
            ]
            for code_info in codes:
                if bp := code_info.get("bodyPart"):
                    if bp not in matched_parts and bp != "general":
                        matched_parts.append(bp)

            if not matched_parts and not codes and not measurements and not treatments and not functional and not CLINICAL_STATEMENT_RE.search(sentence):
                continue

            laterality = detect_laterality(sentence)
            if laterality == "unspecified":
                for code_info in codes:
                    if code_info.get("laterality") and code_info["laterality"] != "unspecified":
                        laterality = code_info["laterality"]
                        break

            primary_part = matched_parts[0] if matched_parts else "general"
            assertion = (
                "negated" if is_negated else
                "resolved" if is_resolved else
                "family-history" if is_family else
                "suspected" if is_suspected else
                "historical" if is_historical else
                "present"
            )
            concept_tokens = _extract_concept_tokens(sentence)
            digest = hashlib.sha256(f"{doc_id}:{page_num}:{sentence}".encode()).hexdigest()[:12]
            facts.append({
                "id": f"fact-{doc_id}-{page_num}-{digest}",
                "esrId": esr_id,
                "documentId": doc_id,
                "documentTitle": doc_title,
                "documentCategory": doc.get("category", "other"),
                "pageNumber": page_num,
                "clinicalDate": sent_date or "",
                "bodyPart": primary_part,
                "bodyParts": matched_parts,
                "laterality": laterality,
                "assertion": assertion,
                "isNegated": is_negated,
                "isResolved": is_resolved,
                "isSuspected": is_suspected,
                "isFamilyHistory": is_family,
                "isHistorical": is_historical,
                "measurements": measurements,
                "treatments": treatments,
                "functionalLimitations": functional,
                "conceptTokens": concept_tokens,
                "codes": codes,
                "rawExcerpt": sentence[:500],
            })

    # Include structured lab panels already extracted in Stage 1
    lab_panels = (doc.get("extractedData") or {}).get("labPanels") or []
    for idx, panel in enumerate(lab_panels, 1):
        abnormal = [r for r in panel.get("results", []) if r.get("flag") in {"abnormal", "critical"}]
        facts.append({
            "id": f"fact-{doc_id}-lab-{idx}",
            "esrId": esr_id,
            "documentId": doc_id,
            "documentTitle": doc_title,
            "documentCategory": "laboratory",
            "pageNumber": 1,
            "clinicalDate": panel.get("collectionDate") or "",
            "bodyPart": "laboratory",
            "bodyParts": ["laboratory"],
            "laterality": "unspecified",
            "assertion": "present",
            "isNegated": False,
            "isFamilyHistory": False,
            "isHistorical": False,
            "measurements": {"abnormalLabs": abnormal, "panelName": panel.get("panelName", "Lab Panel")},
            "codes": [],
            "rawExcerpt": f"{panel.get('panelName', 'Lab Panel')}: {len(panel.get('results', []))} tests ({len(abnormal)} abnormal/critical).",
        })

    return {"facts": facts, "distressAlerts": distress_alerts, "pages": pages}


def contention_search_terms(contention_title: str, requested_dbqs: list[str] | None = None) -> tuple[list[str], set[str]]:
    """Expand contention title + requested DBQs into transparent clinical synonyms, abbreviations, body parts, and codes."""
    combined = " ".join([contention_title, *(requested_dbqs or [])]).casefold()
    raw_tokens = re.findall(r"[a-z0-9]{2,}", contention_title.casefold())
    terms: list[str] = []
    for w in raw_tokens:
        if w in CONTENTION_STOPWORDS or w.isdigit():
            continue
        if len(w) >= 3 or w in ABBREVIATION_EXPANSIONS:
            terms.append(w)
        for exp in ABBREVIATION_EXPANSIONS.get(w, ()):
            terms.append(exp)
            terms.extend(t for t in re.findall(r"[a-z0-9]{3,}", exp) if t not in CONTENTION_STOPWORDS)

    body_parts: set[str] = set()
    for trigger, mapped_parts in CONTENTION_TO_BODY_PARTS.items():
        if re.search(rf"\b{re.escape(trigger)}\b", combined):
            for part in mapped_parts:
                body_parts.add(part)
                terms.extend(BODY_PART_SYNONYMS.get(part, ()))

    # Add matching medical codes into transparent query expansion
    for code, info in ICD10_CROSSWALK.items():
        if info["bodyPart"] in body_parts or (info["examCode"] and info["examCode"] in combined):
            terms.append(code.casefold())
    for code, info in CPT_CROSSWALK.items():
        if info["examCode"] in body_parts or (info["examCode"] and info["examCode"] in combined):
            terms.append(code.casefold())
    for code, info in VASRD_CROSSWALK.items():
        if info["bodyPart"] in body_parts or (info["examCode"] and info["examCode"] in combined):
            terms.append(code.casefold())

    return list(dict.fromkeys(terms)), body_parts


def _laterality_compatible(target_lat: str, fact_lat: str) -> bool:
    if target_lat in ("", "unspecified", "bilateral") or fact_lat in ("", "unspecified", "bilateral"):
        return True
    return target_lat == fact_lat


def search_relevant_evidence(
    esr_id: str,
    contention: dict,
    documents: list[dict],
    clinical_facts: list[dict],
) -> dict[str, Any]:
    """Transparent Full-Text + Synonym + Concept + Excerpt-Ranked retrieval across all clinical documents."""
    title = contention.get("title", "")
    target_lat = detect_laterality(title)
    core_terms = _extract_concept_tokens(title)
    terms, body_parts = contention_search_terms(title, contention.get("requestedDbqNames") or [])
    matched_pages: list[dict[str, Any]] = []
    seen_page_keys: set[tuple[str, int]] = set()

    for doc in documents:
        if not is_clinical_document(doc):
            continue
        for page_obj in document_pages_for_nlp(doc):
            page_num = page_obj["page"]
            text = page_obj["text"]
            sentences = split_clinical_sentences(text) or [text]
            relevant_sents: list[str] = []
            page_hits: list[str] = []
            score = 0.0

            for sent in sentences:
                s_lower = sent.casefold()
                core_hits = [t for t in core_terms if re.search(rf"\b{re.escape(t)}\b", s_lower)]
                syn_hits = [t for t in terms if re.search(rf"\b{re.escape(t)}\b", s_lower)]
                if core_hits or syn_hits:
                    sent_lat = detect_laterality(sent)
                    lat_factor = 1.2 if (target_lat in {"left", "right"} and sent_lat == target_lat) else (
                        0.35 if (target_lat in {"left", "right"} and sent_lat in {"left", "right"} and sent_lat != target_lat) else 1.0
                    )
                    score += (len(core_hits) * 3.0 + len(syn_hits) * 1.0) * lat_factor
                    relevant_sents.append(sent)
                    for h in (*core_hits, *syn_hits):
                        if h not in page_hits:
                            page_hits.append(h)

            if page_hits:
                key = (doc["id"], page_num)
                if key not in seen_page_keys:
                    seen_page_keys.add(key)
                    focused_excerpt = " ".join(relevant_sents) if relevant_sents else text
                    matched_pages.append({
                        "documentId": doc["id"],
                        "documentTitle": doc.get("title") or doc.get("sourceReference"),
                        "category": doc.get("category", "other"),
                        "page": page_num,
                        "clinicalDate": parse_clinical_date(text[:450]) or "",
                        "matchedTerms": page_hits[:10],
                        "relevanceScore": round(score, 2),
                        "excerpt": focused_excerpt,
                        "fullPageText": text,
                    })

    # Include structured clinical facts relevant to this contention (never pull unrelated body-part facts from shared pages)
    matched_facts: list[dict[str, Any]] = []
    for fact in clinical_facts:
        if fact.get("esrId") != esr_id:
            continue
        fact_parts = set(fact.get("bodyParts") or [fact.get("bodyPart")])
        fact_tokens = set(fact.get("conceptTokens") or [])
        fact_codes = {c.get("code", "").casefold() for c in fact.get("codes", [])}
        excerpt_lower = (fact.get("rawExcerpt") or "").casefold()
        fact_lat = fact.get("laterality", "unspecified")

        concept_match = bool(
            (fact_parts & body_parts)
            or (fact_tokens & set(core_terms))
            or (fact_codes & set(terms))
            or any(re.search(rf"\b{re.escape(t)}\b", excerpt_lower) for t in core_terms)
        )
        # Preserve secondary nerve facts alongside musculoskeletal claims, and abnormal labs for clinical review
        is_secondary_nerve = bool(body_parts & {"knee", "back", "neck", "shoulder"}) and ("nerve" in fact_parts)
        is_lab = fact.get("bodyPart") == "laboratory"
        if (concept_match and _laterality_compatible(target_lat, fact_lat)) or is_secondary_nerve or is_lab:
            matched_facts.append(fact)

    # Sort by relevance score descending, then chronological clinicalDate
    matched_pages.sort(key=lambda item: (-item.get("relevanceScore", 0.0), item["clinicalDate"] == "", item["clinicalDate"], item["documentTitle"], item["page"]))
    matched_facts.sort(key=lambda item: (not item.get("clinicalDate"), item.get("clinicalDate", ""), item.get("pageNumber", 1)))

    # Fallback: if no keyword matched yet and non-admin documents exist, expose non-excluded clinical docs so clinicians never miss records
    used_fallback = False
    if not matched_pages:
        for doc in documents:
            if not is_clinical_document(doc):
                continue
            for page_obj in document_pages_for_nlp(doc):
                matched_pages.append({
                    "documentId": doc["id"],
                    "documentTitle": doc.get("title") or doc.get("sourceReference"),
                    "category": doc.get("category", "other"),
                    "page": page_obj["page"],
                    "clinicalDate": parse_clinical_date(page_obj["text"][:450]) or "",
                    "matchedTerms": ["full-record fallback"],
                    "relevanceScore": 0.1,
                    "excerpt": page_obj["text"],
                    "fullPageText": page_obj["text"],
                })
                used_fallback = True

    return {
        "searchTerms": terms,
        "bodyParts": sorted(body_parts),
        "targetLaterality": target_lat,
        "matchedPages": matched_pages,
        "matchedFacts": matched_facts,
        "usedFallback": used_fallback,
    }


def detect_clinical_contradictions(matched_facts: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Context-aware contradiction check comparing normalized concept, laterality, and temporality."""
    active = [
        f for f in matched_facts
        if not f.get("isNegated") and not f.get("isFamilyHistory") and not f.get("isResolved") and not f.get("isHistorical")
    ]
    negated = [
        f for f in matched_facts
        if f.get("isNegated") and not f.get("isFamilyHistory") and not f.get("isResolved")
    ]
    generic_ignore = {"pain", "normal", "exam", "note", "patient", "chronic", "acute", "history", "findings"}
    conflicts: list[dict[str, Any]] = []
    seen_pairs: set[tuple[str, str]] = set()

    for pos in active:
        pos_tokens = {t for t in (pos.get("conceptTokens") or []) if t not in generic_ignore}
        pos_codes = {c.get("code") for c in pos.get("codes", []) if c.get("code")}
        pos_lat = pos.get("laterality", "unspecified")
        pos_date = pos.get("clinicalDate", "")

        for neg in negated:
            neg_tokens = {t for t in (neg.get("conceptTokens") or []) if t not in generic_ignore}
            neg_codes = {c.get("code") for c in neg.get("codes", []) if c.get("code")}
            neg_lat = neg.get("laterality", "unspecified")
            neg_date = neg.get("clinicalDate", "")

            if not _laterality_compatible(pos_lat, neg_lat):
                continue
            # Different dated encounters (> 0 days apart when both dates exist) reflect progression, not contradiction
            if pos_date and neg_date and pos_date != neg_date:
                continue

            shared_concepts = sorted((pos_tokens & neg_tokens) | (pos_codes & neg_codes))
            if not shared_concepts:
                continue

            pair_key = tuple(sorted((pos["id"], neg["id"])))
            if pair_key in seen_pairs:
                continue
            seen_pairs.add(pair_key)
            conflicts.append({
                "concepts": shared_concepts[:5],
                "laterality": pos_lat if pos_lat != "unspecified" else neg_lat,
                "positiveCitation": f"{pos.get('documentTitle')} · p. {pos.get('pageNumber')}",
                "positiveExcerpt": pos.get("rawExcerpt", ""),
                "negatedCitation": f"{neg.get('documentTitle')} · p. {neg.get('pageNumber')}",
                "negatedExcerpt": neg.get("rawExcerpt", ""),
            })
    return conflicts
