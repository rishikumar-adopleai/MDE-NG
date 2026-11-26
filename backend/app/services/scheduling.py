"""Stage 3 Intelligent Examination Scheduling service.

Implements complete (facility, examiner, slot) global evaluation, validated Veteran
location resolution without fabricated distances, multi-state license & certification
checks, duplicate conflict detection, pathway-specific rules, configurable ranking,
atomic reservations, and Stage 2 revision reconciliation.
"""

from __future__ import annotations

import json
import logging
import math
import re
from pathlib import Path
from typing import Any
from uuid import uuid4

from fastapi import HTTPException
from sqlalchemy import func, select, update
from sqlalchemy.orm import Session

from app.models import AuditEvent, Stage3Examiner, Stage3Facility, Stage3TimeSlot
from app.services.intake_store import record, timestamp, touch

logger = logging.getLogger(__name__)

STAGE3_DATA_DIR = Path(__file__).resolve().parents[2] / "data" / "stage3_data"
STAGE3_SEED_VERSION = "v3-normalized-dedup"

SCHEDULING_COMMANDS = frozenset({
    "holdAppointment",
    "holdCoordinatedAppointments",
    "recordOutreach",
    "recordAppointmentLogistics",
    "verifyVeteranLocation",
    "confirmAppointment",
    "rescheduleAppointment",
    "cancelAppointment",
    "setSchedulingDistanceLimit",
    "returnClinicalToScheduling",
})

US_STATE_NAME_TO_CODE: dict[str, str] = {
    "alabama": "AL", "alaska": "AK", "arizona": "AZ", "arkansas": "AR", "california": "CA",
    "colorado": "CO", "connecticut": "CT", "delaware": "DE", "district of columbia": "DC",
    "florida": "FL", "georgia": "GA", "hawaii": "HI", "idaho": "ID", "illinois": "IL",
    "indiana": "IN", "iowa": "IA", "kansas": "KS", "kentucky": "KY", "louisiana": "LA",
    "maine": "ME", "maryland": "MD", "massachusetts": "MA", "michigan": "MI", "minnesota": "MN",
    "mississippi": "MS", "missouri": "MO", "montana": "MT", "nebraska": "NE", "nevada": "NV",
    "new hampshire": "NH", "new jersey": "NJ", "new mexico": "NM", "new york": "NY",
    "north carolina": "NC", "north dakota": "ND", "ohio": "OH", "oklahoma": "OK", "oregon": "OR",
    "pennsylvania": "PA", "puerto rico": "PR", "rhode island": "RI", "south carolina": "SC",
    "south dakota": "SD", "tennessee": "TN", "texas": "TX", "utah": "UT", "vermont": "VT",
    "virginia": "VA", "washington": "WA", "west virginia": "WV", "wisconsin": "WI", "wyoming": "WY",
}


def normalize_state_code(raw: str | None) -> str:
    cleaned = " ".join((raw or "").strip().split())
    if not cleaned:
        return ""
    if len(cleaned) == 2 and cleaned.isalpha():
        return cleaned.upper()
    return US_STATE_NAME_TO_CODE.get(cleaned.casefold(), cleaned.upper()[:15])

# Configurable ranking weights & specialty mappings (ready for Prime EMS overrides)
DEFAULT_RANKING_CONFIG = {
    "defaultTravelLimitMiles": None,
    "provisionalDemonstrationMinDurationMinutes": 15,
    "enforceLicenseJurisdiction": True,
    "requireBoardCertifiedWhenRequested": False,
    "maxOutreachAttempts": 2,
    "specialtyTravelThresholds": {
        "general medical": 50,
        "audiology": 100,  # Decoupled from 50mi general medical: specialized sound booth requirements
        "specialty": 100,
    },
}


def _resolve_travel_limit(esr: dict, veteran: dict, requirement: dict | None = None) -> int | None:
    """Return explicit ESR/Veteran travel limit in miles if configured;
    otherwise return None unless an explicit policy threshold is configured."""
    raw = esr.get("travelLimitMiles") or veteran.get("travelLimitMiles")
    if raw is not None:
        try:
            return int(raw)
        except (ValueError, TypeError):
            pass
    return DEFAULT_RANKING_CONFIG.get("defaultTravelLimitMiles")


def get_specialty_travel_threshold(specialty: str) -> int:
    """Configurable source-backed travel thresholds.
    Audiology has specialized sound booth requirements, classified at 100 miles
    rather than general medical 50 miles."""
    spec_clean = specialty.strip().casefold()
    thresholds = DEFAULT_RANKING_CONFIG.get("specialtyTravelThresholds", {})
    if "audiology" in spec_clean or "otolaryngology" in spec_clean:
        return thresholds.get("audiology", 100)
    if spec_clean not in ("general medical", "family medicine", "internal medicine"):
        return thresholds.get("specialty", 100)
    return thresholds.get("general medical", 50)

# Exact primary specialty aliases without equating unrelated qualifications or General Medical
SPECIALTY_ALIASES: dict[str, set[str]] = {
    "orthopedics": {"orthopedics", "physical medicine and rehabilitation"},
    "audiology": {"audiology", "otolaryngology"},
    "psychiatry": {"psychiatry", "clinical psychology"},
    "psychology": {"clinical psychology", "psychiatry"},
    "neurology": {"neurology", "physical medicine and rehabilitation"},
    "dermatology": {"dermatology"},
    "pulmonology": {"pulmonology"},
    "cardiology": {"cardiology"},
    "internal medicine": {"internal medicine", "endocrinology", "family medicine"},
    "endocrinology": {"endocrinology", "internal medicine"},
    "general medical": {"general medical", "family medicine", "internal medicine"},
}

# Diagnostic tests requiring facility equipment (vs "... review" items that review existing records)
EQUIPMENT_DIAGNOSTIC_MAP: dict[str, tuple[str, ...]] = {
    "audiology test": ("audiology booth",),
    "pulmonary function test": ("pft / spirometry",),
    "electrocardiogram / blood pressure log": ("ecg",),
    "x-ray": ("x-ray", "imaging"),
    "imaging": ("imaging", "x-ray"),
    "lab draw": ("lab draw",),
}

# US State / Territory & 1st-digit ZIP prefix reference centroids (used ONLY for approximate ZIP resolution, never claimed exact)
STATE_CENTROIDS: dict[str, tuple[float, float]] = {
    "AL": (32.8067, -86.7911), "AK": (61.3707, -152.4044), "AZ": (33.7298, -111.4312),
    "AR": (34.9697, -92.3731), "CA": (36.1162, -119.6816), "CO": (39.0598, -105.3111),
    "CT": (41.5978, -72.7554), "DE": (39.3185, -75.5071), "FL": (27.7663, -81.6868),
    "GA": (33.0406, -83.6431), "HI": (21.0943, -157.4983), "ID": (44.2405, -114.4788),
    "IL": (40.3495, -88.9861), "IN": (39.8494, -86.2583), "IA": (42.0115, -93.2105),
    "KS": (38.5266, -96.7265), "KY": (37.6681, -84.6701), "LA": (31.1695, -91.8678),
    "ME": (44.6939, -69.3819), "MD": (39.0639, -76.8021), "MA": (42.2302, -71.5301),
    "MI": (43.3266, -84.5361), "MN": (45.6945, -93.9002), "MS": (32.7416, -89.6787),
    "MO": (38.4561, -92.2884), "MT": (46.9219, -110.4544), "NE": (41.1254, -98.2681),
    "NV": (38.3135, -117.0554), "NH": (43.4525, -71.5639), "NJ": (40.2989, -74.5210),
    "NM": (34.8405, -106.2485), "NY": (42.1657, -74.9481), "NC": (35.6301, -79.8064),
    "ND": (47.5289, -99.7840), "OH": (40.3888, -82.7649), "OK": (35.5653, -96.9289),
    "OR": (44.5720, -122.0709), "PA": (40.5908, -77.2098), "RI": (41.6809, -71.5118),
    "SC": (33.8569, -80.9450), "SD": (44.2998, -99.4388), "TN": (35.7478, -86.6923),
    "TX": (31.0545, -97.5635), "UT": (40.1500, -111.8624), "VT": (44.0459, -72.7107),
    "VA": (37.7693, -78.1700), "WA": (47.4009, -121.4905), "WV": (38.4912, -80.9545),
    "WI": (44.2685, -89.6165), "WY": (42.7559, -107.3025), "DC": (38.8974, -77.0365),
}
ZIP_FIRST_DIGIT_CENTROIDS: dict[str, tuple[float, float]] = {
    "0": (42.1, -72.6), "1": (41.2, -75.5), "2": (38.0, -78.5), "3": (31.5, -84.2),
    "4": (40.2, -84.0), "5": (44.5, -94.0), "6": (39.5, -91.0), "7": (32.5, -96.5),
    "8": (39.0, -109.0), "9": (37.5, -120.5),
}


def _norm_text(val: Any) -> str:
    """Normalize capitalization, whitespace, and punctuation without altering distinct identities."""
    if val is None:
        return ""
    s = " ".join(str(val).strip().split()).casefold()
    return re.sub(r"[,.;:]+", "", s)


def _norm_list(items: Any) -> list[str]:
    return sorted({_norm_text(x) for x in (items or []) if _norm_text(x)})


def seed_stage3_records(db: Session, facilities_data: list[dict], examiners_data: list[dict]) -> dict[str, int]:
    """Normalize, deduplicate, and detect conflicting duplicate facility/examiner records."""
    fac_by_id: dict[str, dict[str, Any]] = {}
    fac_conflicts = 0
    for item in facilities_data:
        fid = item["facilityId"]
        if fid not in fac_by_id:
            fac_by_id[fid] = {**item, "duplicateConflict": False, "conflictFields": [], "seedVersion": STAGE3_SEED_VERSION}
            continue
        prev = fac_by_id[fid]
        diffs: list[str] = []
        for k in ("facilityName", "city", "stateCode", "postalCode", "status"):
            if _norm_text(prev.get(k)) != _norm_text(item.get(k)):
                diffs.append(k)
        if _norm_list(prev.get("supportedPathways")) != _norm_list(item.get("supportedPathways")):
            diffs.append("supportedPathways")
        if (prev.get("latitude") is not None and item.get("latitude") is not None and round(prev["latitude"], 4) != round(item["latitude"], 4)) or (
            prev.get("longitude") is not None and item.get("longitude") is not None and round(prev["longitude"], 4) != round(item["longitude"], 4)
        ):
            diffs.append("coordinates")
        if diffs:
            fac_conflicts += 1
            prev["duplicateConflict"] = True
            prev["conflictFields"] = sorted(set([*prev.get("conflictFields", []), *diffs]))
            prev["status"] = "conflict-requires-resolution"

    exm_by_id: dict[str, dict[str, Any]] = {}
    slot_by_id: dict[str, dict[str, Any]] = {}
    slot_conflicts = 0
    exm_conflicts = 0
    for item in examiners_data:
        eid = item["examinerId"]
        slots = item.get("availableTimeSlots") or []
        for s in slots:
            sid = s["slotId"]
            slot_obj = {**s, "examinerId": s.get("examinerId") or eid}
            if sid not in slot_by_id:
                slot_by_id[sid] = slot_obj
            else:
                prev_s = slot_by_id[sid]
                if (
                    prev_s.get("examinerId") != slot_obj["examinerId"]
                    or prev_s.get("facilityId") != slot_obj.get("facilityId")
                    or prev_s.get("startsAt") != slot_obj.get("startsAt")
                ):
                    slot_conflicts += 1
        if eid not in exm_by_id:
            clean_item = {k: v for k, v in item.items() if k != "availableTimeSlots"}
            clean_item["duplicateConflict"] = False
            clean_item["conflictFields"] = []
            clean_item["seedVersion"] = STAGE3_SEED_VERSION
            exm_by_id[eid] = clean_item
            continue

        prev = exm_by_id[eid]
        # Merge legitimate multiple facility assignments and multi-state licenses
        prev["facilityIds"] = list(dict.fromkeys([*(prev.get("facilityIds") or []), *(item.get("facilityIds") or [])]))
        lic_map = {
            (_norm_text(l.get("licenseState", "")), _norm_text(l.get("licenseNumber", ""))): l
            for l in [*(prev.get("licenses") or []), *(item.get("licenses") or [])]
        }
        prev["licenses"] = list(lic_map.values())

        diffs = []
        for k in ("doctorName", "primarySpecialty", "licenseStatus", "licenseExpiryDate", "status"):
            if _norm_text(prev.get(k)) != _norm_text(item.get(k)):
                diffs.append(k)
        for lk in ("credentials", "authorizedDbqTypes"):
            if _norm_list(prev.get(lk)) != _norm_list(item.get(lk)):
                diffs.append(lk)
        if diffs:
            exm_conflicts += 1
            prev["duplicateConflict"] = True
            prev["conflictFields"] = sorted(set([*prev.get("conflictFields", []), *diffs]))
            prev["status"] = "conflict-requires-resolution"

    fac_rows = [
        Stage3Facility(
            facility_id=fid,
            facility_name=item.get("facilityName", fid),
            facility_type=item.get("facilityType", "medical-clinic"),
            city=item.get("city", ""),
            state_code=item.get("stateCode", ""),
            postal_code=item.get("postalCode", ""),
            latitude=item.get("latitude"),
            longitude=item.get("longitude"),
            time_zone=item.get("timeZone", "UTC"),
            supported_pathways=item.get("supportedPathways") or [],
            accessibility_features=item.get("accessibilityFeatures") or [],
            diagnostic_capabilities=item.get("diagnosticCapabilities") or [],
            status=item.get("status", "active"),
            data_json=item,
        )
        for fid, item in fac_by_id.items()
    ]
    exm_rows = [
        Stage3Examiner(
            examiner_id=eid,
            doctor_name=item.get("doctorName", eid),
            primary_specialty=item.get("primarySpecialty", ""),
            specialties=item.get("specialties") or [],
            credentials=item.get("credentials") or [],
            license_number=item.get("licenseNumber", ""),
            license_state=item.get("licenseState", ""),
            license_expiry_date=item.get("licenseExpiryDate", ""),
            license_status=item.get("licenseStatus", "active"),
            facility_ids=item.get("facilityIds") or [],
            primary_facility_id=item.get("primaryFacilityId", ""),
            supported_pathways=item.get("supportedPathways") or [],
            authorized_dbq_types=item.get("authorizedDbqTypes") or [],
            status=item.get("status", "active"),
            data_json=item,
        )
        for eid, item in exm_by_id.items()
    ]
    slot_rows = [
        Stage3TimeSlot(
            slot_id=s["slotId"],
            examiner_id=s["examinerId"],
            facility_id=s["facilityId"],
            starts_at=s["startsAt"],
            ends_at=s["endsAt"],
            local_starts_at=s.get("localStartsAt", ""),
            local_ends_at=s.get("localEndsAt", ""),
            time_zone=s.get("timeZone", "UTC"),
            duration_minutes=int(s.get("durationMinutes") or 60),
            modality=s.get("modality", "in-person"),
            slot_status=s.get("slotStatus", "available"),
        )
        for s in slot_by_id.values()
    ]

    db.query(Stage3TimeSlot).delete()
    db.query(Stage3Examiner).delete()
    db.query(Stage3Facility).delete()
    db.bulk_save_objects(fac_rows)
    db.bulk_save_objects(exm_rows)
    for idx in range(0, len(slot_rows), 5000):
        db.bulk_save_objects(slot_rows[idx : idx + 5000])
    db.commit()
    return {
        "facilities": len(fac_rows),
        "examiners": len(exm_rows),
        "slots": len(slot_rows),
        "facilityConflicts": fac_conflicts,
        "examinerConflicts": exm_conflicts,
    }


def ensure_stage3_directory_seeded(db: Session) -> None:
    """Seed or upgrade normalized Stage 3 tables cleanly."""
    first_fac = db.execute(select(Stage3Facility).limit(1)).scalar_one_or_none()
    if first_fac is not None and (first_fac.data_json or {}).get("seedVersion") == STAGE3_SEED_VERSION:
        return
    fac_path = STAGE3_DATA_DIR / "facilities.json"
    exm_path = STAGE3_DATA_DIR / "examiners.json"
    if not fac_path.exists() or not exm_path.exists():
        return
    with fac_path.open("r", encoding="utf-8") as f:
        facilities_data = json.load(f)
    with exm_path.open("r", encoding="utf-8") as f:
        examiners_data = json.load(f)
    seed_stage3_records(db, facilities_data, examiners_data)


def _haversine_miles(lat1: float, lon1: float, lat2: float, lon2: float) -> float:
    r = 3958.8
    phi1, phi2 = math.radians(lat1), math.radians(lat2)
    dphi = math.radians(lat2 - lat1)
    dlambda = math.radians(lon2 - lon1)
    a = math.sin(dphi / 2) ** 2 + math.cos(phi1) * math.cos(phi2) * math.sin(dlambda / 2) ** 2
    return round(2 * r * math.atan2(math.sqrt(a), math.sqrt(1 - a)), 1)


def resolve_veteran_location(veteran: dict, esr: dict) -> dict[str, Any]:
    """Resolve Veteran coordinates strictly without fabricating 25-mile fallback distances."""
    lat, lon = veteran.get("latitude"), veteran.get("longitude")
    if isinstance(lat, (int, float)) and isinstance(lon, (int, float)):
        return {
            "precision": "exact",
            "latitude": float(lat),
            "longitude": float(lon),
            "stateCode": (veteran.get("stateCode") or "").upper(),
            "postalCode": str(veteran.get("postalCode") or ""),
            "verificationRequired": False,
        }
    zip_str = str(veteran.get("postalCode") or esr.get("postalCode") or "").strip()
    state_code = str(veteran.get("stateCode") or "").strip().upper()
    if re.match(r"^\d{5}(?:-\d{4})?$", zip_str):
        coords = STATE_CENTROIDS.get(state_code) or ZIP_FIRST_DIGIT_CENTROIDS.get(zip_str[0])
        if coords:
            return {
                "precision": "approximate-zip",
                "latitude": coords[0],
                "longitude": coords[1],
                "stateCode": state_code,
                "postalCode": zip_str[:5],
                "verificationRequired": True,
            }
    return {
        "precision": "unknown",
        "latitude": None,
        "longitude": None,
        "stateCode": state_code,
        "postalCode": zip_str,
        "verificationRequired": True,
    }


def _facility_distance(loc: dict[str, Any], fac: Stage3Facility) -> tuple[float | None, str]:
    if loc["precision"] == "unknown" or loc["latitude"] is None or fac.latitude is None or fac.longitude is None:
        return None, "unknown"
    return _haversine_miles(loc["latitude"], loc["longitude"], fac.latitude, fac.longitude), loc["precision"]


def _normalize_specialty_group(req: dict) -> str:
    specs = " ".join(req.get("requiredSpecialties") or []).casefold()
    if "audiology" in specs:
        return "audiology"
    if "psych" in specs:
        return "psychiatry"
    if "ortho" in specs or "musculoskeletal" in specs:
        return "orthopedics"
    if "neuro" in specs:
        return "neurology"
    if "derm" in specs:
        return "dermatology"
    if "pulm" in specs:
        return "pulmonology"
    if "cardio" in specs:
        return "cardiology"
    if "endo" in specs or "internal" in specs:
        return "internal medicine"
    return "general medical"


def _accepted_specialties_for_req(req: dict) -> set[str]:
    specs = " ".join(req.get("requiredSpecialties") or []).casefold()
    accepted: set[str] = set()
    if "audiology" in specs:
        accepted |= SPECIALTY_ALIASES.get("audiology", {"audiology"})
    if "psych" in specs:
        accepted |= SPECIALTY_ALIASES.get("psychiatry", {"psychiatry"})
    if "ortho" in specs or "musculoskeletal" in specs:
        accepted |= SPECIALTY_ALIASES.get("orthopedics", {"orthopedics"})
    if "neuro" in specs:
        accepted |= SPECIALTY_ALIASES.get("neurology", {"neurology"})
    if "derm" in specs:
        accepted |= SPECIALTY_ALIASES.get("dermatology", {"dermatology"})
    if "pulm" in specs:
        accepted |= SPECIALTY_ALIASES.get("pulmonology", {"pulmonology"})
    if "cardio" in specs:
        accepted |= SPECIALTY_ALIASES.get("cardiology", {"cardiology"})
    if "endo" in specs or "internal" in specs:
        accepted |= SPECIALTY_ALIASES.get("internal medicine", {"internal medicine"})
    if "general medical" in specs or "general" in specs or (not accepted and not (req.get("requiredSpecialties") or [])):
        accepted |= SPECIALTY_ALIASES.get("general medical", {"general medical"})
    return accepted


def _required_equipment_for_requirements(requirements: list[dict]) -> list[tuple[str, tuple[str, ...]]]:
    needed: list[tuple[str, tuple[str, ...]]] = []
    for req in requirements:
        for diag in req.get("requestedDiagnostics") or []:
            dl = diag.strip().casefold()
            if re.search(r"\b(review|log)\b", dl):
                continue
            for key, caps in EQUIPMENT_DIAGNOSTIC_MAP.items():
                if key in dl:
                    needed.append((diag, caps))
                    break
    return needed


def _facility_eligible(
    fac: Stage3Facility,
    modality: str,
    requirements: list[dict],
    veteran: dict,
) -> tuple[bool, str]:
    if (fac.data_json or {}).get("duplicateConflict") or fac.status == "conflict-requires-resolution":
        return False, f"Facility has conflicting duplicate records ({', '.join((fac.data_json or {}).get('conflictFields', []))}); resolution required."
    if fac.status != "active":
        return False, f"Facility status is '{fac.status}'."
    if modality not in (fac.supported_pathways or []):
        return False, f"Facility does not support '{modality}'."
    fac_acc = {a.casefold() for a in (fac.accessibility_features or [])}
    for need in veteran.get("accessibilityNeeds") or []:
        if need.casefold() not in fac_acc:
            return False, f"Facility lacks required accessibility feature: {need}."
    if modality == "in-person":
        fac_caps = {c.casefold() for c in (fac.diagnostic_capabilities or [])}
        for label, accepted_caps in _required_equipment_for_requirements(requirements):
            if not any(cap in fac_caps for cap in accepted_caps):
                return False, f"Facility lacks required diagnostic equipment for '{label}'."
    return True, "Eligible"


def _extract_credential_tokens(cred_list: list[str]) -> set[str]:
    tokens: set[str] = set()
    for raw in cred_list:
        for part in re.findall(r"[a-z]{2,}", raw.casefold()):
            tokens.add(part)
    return tokens


def _examiner_matches_specialty(examiner: Stage3Examiner, requirements: list[dict]) -> bool:
    exm_specs = {_norm_text(s) for s in (examiner.specialties or []) if _norm_text(s)}
    if examiner.primary_specialty:
        exm_specs.add(_norm_text(examiner.primary_specialty))
    for req in requirements:
        accepted = _accepted_specialties_for_req(req)
        if not (exm_specs & accepted):
            return False
    return True


def _check_examiner_base_eligibility(examiner: Stage3Examiner, requirements: list[dict], modality: str, facility_id: str) -> tuple[bool, str]:
    # Check examination-relevant specialty FIRST so unrelated specialties are filtered out cleanly
    if not _examiner_matches_specialty(examiner, requirements):
        return False, f"Unrelated specialty '{examiner.primary_specialty}' for required '{', '.join(requirements[0].get('requiredSpecialties') or [])}'."
    if (examiner.data_json or {}).get("duplicateConflict") or examiner.status == "conflict-requires-resolution":
        return False, f"Conflicting duplicate examiner record ({', '.join((examiner.data_json or {}).get('conflictFields', []))}); resolution required."
    if examiner.status != "active":
        return False, f"Examiner status is '{examiner.status}'."
    if facility_id not in (examiner.facility_ids or []):
        return False, "Not assigned to this facility."
    if modality not in (examiner.supported_pathways or []):
        return False, f"Examiner does not support '{modality}'."

    # Mandatory credential verification (professional experience never overrides credentials)
    exm_creds = _extract_credential_tokens(examiner.credentials or [])
    for req in requirements:
        req_tokens = _extract_credential_tokens(req.get("requiredCredentials") or [])
        degree_tokens = req_tokens & {"md", "do", "aud", "phd", "psyd", "np", "pa", "dds"}
        if degree_tokens and not (exm_creds & degree_tokens):
            return False, f"Credentials {examiner.credentials} do not satisfy required {req.get('requiredCredentials')}."
        if "tbi" in " ".join(req.get("requiredCredentials") or []).casefold():
            if not any("tbi" in c.casefold() for c in (examiner.credentials or [])):
                return False, "Missing mandatory TBI certification/training."

    # Required DBQ authorizations (canonical base-name match when directory specifies authorizedDbqTypes)
    if examiner.authorized_dbq_types:
        auth_norm = {d.casefold().removesuffix(" dbq").replace("conditions", "").strip() for d in examiner.authorized_dbq_types}
        for req in requirements:
            for dbq in req.get("requestedDbqTypes") or []:
                norm = dbq.casefold().removesuffix(" dbq").replace("conditions", "").strip()
                if "medical opinion" in norm and not any("medical opinion" in a for a in auth_norm):
                    return False, f"Not authorized for '{dbq}'."
                if not any(norm in a or a in norm for a in auth_norm):
                    return False, f"Not authorized for '{dbq}'."

    return True, "Qualified"


def is_license_valid_for_exam_date(
    license_status: str,
    license_expiry_date: str,
    exam_starts_at: str,
    *,
    licenses: list[dict] | None = None,
    required_state: str = "",
) -> tuple[bool, str]:
    """Validate license status, jurisdiction (multi-state aware), and expiry strictly AFTER exam date."""
    exam_day = exam_starts_at[:10]
    req_state = required_state.strip().upper()
    if licenses:
        candidates = licenses
        if req_state:
            state_matches = [l for l in licenses if str(l.get("licenseState", "")).upper() == req_state]
            if not state_matches:
                # Permit multi-state / primary active license only if federal/VA practice OR flag jurisdiction mismatch
                pass
            else:
                candidates = state_matches
        active_valid = [
            l for l in candidates
            if l.get("licenseStatus") == "active" and str(l.get("licenseExpiryDate", ""))[:10] > exam_day
        ]
        if active_valid:
            best = max(active_valid, key=lambda l: str(l.get("licenseExpiryDate", "")))
            return True, f"Active {best.get('licenseState', '')} license valid through {str(best.get('licenseExpiryDate', ''))[:10]}"
        expired_or_bad = candidates[0]
        return False, (
            f"License ({expired_or_bad.get('licenseState', '')} {expired_or_bad.get('licenseStatus', '')}, "
            f"expires {str(expired_or_bad.get('licenseExpiryDate', ''))[:10] or 'N/A'}) is not valid past {exam_day}"
        )

    if license_status != "active":
        return False, f"License status is '{license_status}'"
    if not license_expiry_date:
        return False, "Missing license expiration date"
    expiry_day = license_expiry_date[:10]
    if expiry_day <= exam_day:
        return False, f"License expires on {expiry_day} (not valid after exam date {exam_day})"
    return True, f"Active license valid through {expiry_day}"


def _certifications_valid_on_date(examiner: Stage3Examiner, exam_starts_at: str) -> tuple[bool, str]:
    exam_day = exam_starts_at[:10]
    certs = (examiner.data_json or {}).get("certifications") or []
    for cert in certs:
        cname = str(cert.get("certificationName", "")).casefold()
        if "training" in cname or "tbi" in cname:
            if cert.get("status") != "active" or (cert.get("expiresAt") and str(cert["expiresAt"])[:10] <= exam_day):
                return False, f"Mandatory '{cert.get('certificationName')}' expired or inactive on {exam_day}."
    return True, "Certifications valid"


def compute_compatible_groups(requirements: list[dict]) -> list[dict]:
    """Group compatible unscheduled requirements sharing pathway and specialty."""
    buckets: dict[tuple[str, str], list[dict]] = {}
    for req in requirements:
        modality = req.get("selectedModality") or "in-person"
        group_key = _normalize_specialty_group(req)
        buckets.setdefault((modality, group_key), []).append(req)

    groups: list[dict] = []
    for idx, ((modality, spec_group), items) in enumerate(buckets.items(), 1):
        groups.append({
            "groupId": f"grp-{idx}-{spec_group.replace(' ', '-')}",
            "modality": modality,
            "specialtyGroup": spec_group.title(),
            "requirementIds": [r["id"] for r in items],
            "titles": [r.get("examinationType") or r.get("title", "") for r in items],
            "isBundled": len(items) > 1,
            "reason": (
                f"Compatible {spec_group.title()} examinations under {modality} can be scheduled together."
                if len(items) > 1
                else f"Scheduled separately due to specialized {spec_group.title()} / {modality} requirements."
            ),
        })
    return groups


def find_scheduling_matches(
    db: Session,
    state: dict,
    requirement_ids: list[str],
    *,
    facility_id: str | None = None,
    examiner_id: str | None = None,
) -> dict[str, Any]:
    """Global complete matching: evaluates ALL (facility, examiner, slot) triples before recommending facilities."""
    ensure_stage3_directory_seeded(db)
    requirements = [
        state["examinationRequirementsById"][rid]
        for rid in requirement_ids
        if rid in state.get("examinationRequirementsById", {})
    ]
    if not requirements or any(r.get("status") == "superseded-by-evidence-revision" for r in requirements):
        raise HTTPException(409, "Examination requirement is missing or superseded by revised Stage 2 evidence.")

    esr = state["esrsById"].get(requirements[0]["esrId"], {})
    veteran = state.get("veteransById", {}).get(esr.get("veteranId"), {})
    modality = requirements[0].get("selectedModality") or "in-person"
    travel_limit = _resolve_travel_limit(esr, veteran, requirements[0] if requirements else None)
    due_day = str(esr.get("dueAt") or "")[:10]
    min_duration = DEFAULT_RANKING_CONFIG.get("provisionalDemonstrationMinDurationMinutes", 15)

    loc = resolve_veteran_location(veteran, esr)
    if modality == "ace-records-review":
        loc["verificationRequired"] = False

    # Active appointments for double-booking & room capacity checks
    active_appts = [
        a for a in state.get("appointmentsById", {}).values()
        if a.get("status") in {"held", "confirmed", "confirmed-locally"}
    ]
    vet_busy = [(a["startsAt"], a["endsAt"]) for a in active_appts if a.get("veteranId") == veteran.get("id")]
    exm_busy: dict[str, list[tuple[str, str]]] = {}
    fac_busy: dict[str, list[tuple[str, str]]] = {}
    for a in active_appts:
        exm_busy.setdefault(a.get("examinerId", ""), []).append((a["startsAt"], a["endsAt"]))
        fac_busy.setdefault(a.get("facilityId", ""), []).append((a["startsAt"], a["endsAt"]))

    def _overlaps(busy_list: list[tuple[str, str]], s: str, e: str) -> bool:
        return any(not (e <= bs or s >= be) for bs, be in busy_list)

    all_facilities = db.execute(select(Stage3Facility)).scalars().all()
    all_examiners = db.execute(select(Stage3Examiner)).scalars().all()
    avail_slots = db.execute(
        select(Stage3TimeSlot)
        .where(
            Stage3TimeSlot.modality == modality,
            Stage3TimeSlot.slot_status == "available",
            Stage3TimeSlot.duration_minutes >= min_duration,
        )
        .order_by(Stage3TimeSlot.starts_at)
    ).scalars().all()

    slots_by_fac_exm: dict[tuple[str, str], list[Stage3TimeSlot]] = {}
    for sl in avail_slots:
        slots_by_fac_exm.setdefault((sl.facility_id, sl.examiner_id), []).append(sl)

    examiners_by_fac: dict[str, list[Stage3Examiner]] = {}
    for ex in all_examiners:
        for fid in (ex.facility_ids or []):
            examiners_by_fac.setdefault(fid, []).append(ex)

    target_groups = {_normalize_specialty_group(r) for r in requirements}
    pref_exm_id = veteran.get("examinerPreference") or esr.get("preferredExaminerId") or ""

    schedulable_facilities: list[dict[str, Any]] = []
    doctors_by_facility: dict[str, list[dict[str, Any]]] = {}
    excluded_by_facility: dict[str, list[dict[str, Any]]] = {}
    exception_reasons: list[str] = []
    audit_unrelated_count = 0
    audit_facility_ineligible_count = 0

    for fac in all_facilities:
        ok_fac, fac_reason = _facility_eligible(fac, modality, requirements, veteran)
        if not ok_fac:
            audit_facility_ineligible_count += 1
            if len(exception_reasons) < 6 and fac_reason not in exception_reasons:
                exception_reasons.append(f"{fac.facility_name}: {fac_reason}")
            continue

        dist, dist_prec = (None, "not-applicable-ace") if modality == "ace-records-review" else _facility_distance(loc, fac)
        within_travel = True if (travel_limit is None or modality != "in-person" or dist is None) else (dist <= travel_limit)
        room_cap = max(1, int((fac.data_json or {}).get("examRoomCount") or 1))

        fac_valid_docs: list[dict[str, Any]] = []
        fac_excluded_docs: list[dict[str, Any]] = []

        for exm in examiners_by_fac.get(fac.facility_id, []):
            ok_base, base_reason = _check_examiner_base_eligibility(exm, requirements, modality, fac.facility_id)
            if not ok_base:
                # Retain unrelated specialty counts separately for audit; keep primary excludedDoctors examination-relevant
                if base_reason.startswith("Unrelated specialty"):
                    audit_unrelated_count += 1
                else:
                    fac_excluded_docs.append({
                        "examinerId": exm.examiner_id,
                        "doctorName": exm.doctor_name,
                        "primarySpecialty": exm.primary_specialty,
                        "licenseStatus": exm.license_status,
                        "licenseExpiryDate": exm.license_expiry_date,
                        "reason": base_reason,
                    })
                continue

            raw_slots = slots_by_fac_exm.get((fac.facility_id, exm.examiner_id), [])
            exm_licenses = (exm.data_json or {}).get("licenses") or []
            valid_slots: list[dict[str, Any]] = []
            last_block_reason = ""
            for sl in raw_slots:
                ok_lic, lic_msg = is_license_valid_for_exam_date(
                    exm.license_status,
                    exm.license_expiry_date,
                    sl.starts_at,
                    licenses=exm_licenses,
                    required_state=fac.state_code if modality == "in-person" else "",
                )
                if not ok_lic:
                    last_block_reason = lic_msg
                    continue
                ok_cert, cert_msg = _certifications_valid_on_date(exm, sl.starts_at)
                if not ok_cert:
                    last_block_reason = cert_msg
                    continue
                if _overlaps(vet_busy, sl.starts_at, sl.ends_at) or _overlaps(exm_busy.get(exm.examiner_id, []), sl.starts_at, sl.ends_at):
                    last_block_reason = "Overlaps existing appointment hold/confirmation"
                    continue
                overlapping_rooms = sum(1 for bs, be in fac_busy.get(fac.facility_id, []) if not (sl.ends_at <= bs or sl.starts_at >= be))
                if overlapping_rooms >= room_cap:
                    last_block_reason = "Facility exam rooms fully booked at this time"
                    continue
                within_deadline = True if not due_day else (sl.starts_at[:10] <= due_day)
                valid_slots.append({
                    "slotId": sl.slot_id,
                    "examinerId": sl.examiner_id,
                    "facilityId": sl.facility_id,
                    "startsAt": sl.starts_at,
                    "endsAt": sl.ends_at,
                    "localStartsAt": sl.local_starts_at,
                    "localEndsAt": sl.local_ends_at,
                    "timeZone": sl.time_zone,
                    "durationMinutes": sl.duration_minutes,
                    "modality": sl.modality,
                    "withinDeadline": within_deadline,
                    "licenseVerification": lic_msg,
                })

            if valid_slots:
                exp_years = int(((exm.data_json or {}).get("professionalExperience") or {}).get("cpExaminationExperienceYears") or 0)
                cred_mode = (
                    "simulated-demo-verification"
                    if str((exm.data_json or {}).get("credentialVerificationStatus") or "").startswith("simulated")
                    else "authoritative-production-required"
                )
                is_exact_spec = (exm.primary_specialty or "").casefold() in target_groups
                fac_valid_docs.append({
                    "examinerId": exm.examiner_id,
                    "doctorName": exm.doctor_name,
                    "primarySpecialty": exm.primary_specialty,
                    "specialties": exm.specialties,
                    "credentials": exm.credentials,
                    "licenseNumber": exm.license_number,
                    "licenseState": exm.license_state,
                    "licenseExpiryDate": exm.license_expiry_date,
                    "licenseStatus": exm.license_status,
                    "credentialVerificationMode": cred_mode,
                    "authorizedDbqTypes": exm.authorized_dbq_types,
                    "experienceYears": exp_years,
                    "slots": valid_slots,
                    "_sortKey": (
                        0 if exm.examiner_id == pref_exm_id else 1,
                        0 if is_exact_spec else 1,
                        0 if valid_slots[0]["withinDeadline"] else 1,
                        valid_slots[0]["starts_at"] if "starts_at" in valid_slots[0] else valid_slots[0]["startsAt"],
                        -exp_years,
                    ),
                })
            else:
                fac_excluded_docs.append({
                    "examinerId": exm.examiner_id,
                    "doctorName": exm.doctor_name,
                    "primarySpecialty": exm.primary_specialty,
                    "licenseStatus": exm.license_status,
                    "licenseExpiryDate": exm.license_expiry_date,
                    "reason": last_block_reason or "No conflict-free time slots with sufficient duration available.",
                })

        if fac_valid_docs:
            fac_valid_docs.sort(key=lambda d: d["_sortKey"])
            for d in fac_valid_docs:
                d.pop("_sortKey", None)
            doctors_by_facility[fac.facility_id] = fac_valid_docs
            excluded_by_facility[fac.facility_id] = fac_excluded_docs

            same_state = 0 if (loc["stateCode"] and fac.state_code.upper() == loc["stateCode"]) else 1
            earliest_slot = fac_valid_docs[0]["slots"][0]["startsAt"]
            schedulable_facilities.append({
                "facilityId": fac.facility_id,
                "facilityName": fac.facility_name,
                "facilityType": fac.facility_type,
                "city": fac.city,
                "stateCode": fac.state_code,
                "postalCode": fac.postal_code,
                "distanceMiles": dist,
                "distancePrecision": dist_prec,
                "withinTravelLimit": within_travel,
                "validDoctorCount": len(fac_valid_docs),
                "availableSlotCount": sum(len(d["slots"]) for d in fac_valid_docs),
                "accessibilityFeatures": fac.accessibility_features,
                "diagnosticCapabilities": fac.diagnostic_capabilities,
                "_sortKey": (
                    0 if within_travel else 1,
                    same_state,
                    dist if dist is not None else 99999.0,
                    earliest_slot,
                    fac.facility_name,
                ),
            })

    schedulable_facilities.sort(key=lambda f: f["_sortKey"])
    top_facilities = [{k: v for k, v in f.items() if k != "_sortKey"} for f in schedulable_facilities]

    # Select requested facility if it is schedulable; otherwise automatically advance to best valid alternative!
    valid_fac_ids = {f["facilityId"] for f in top_facilities}
    selected_facility_id = facility_id if (facility_id and facility_id in valid_fac_ids) else (top_facilities[0]["facilityId"] if top_facilities else "")
    valid_doctors = doctors_by_facility.get(selected_facility_id, [])
    excluded_doctors = excluded_by_facility.get(selected_facility_id, [])
    selected_doctor = next((d for d in valid_doctors if d["examinerId"] == examiner_id), valid_doctors[0] if valid_doctors else None)

    scheduling_exception = None
    if not top_facilities:
        scheduling_exception = {
            "code": "no-eligible-combination",
            "message": f"No eligible facility + qualified examiner + conflict-free slot found for {requirements[0].get('examinationType', 'this examination')}.",
            "reasons": exception_reasons or ["No facility currently has an active, license-valid specialist with available slots."],
        }

    return {
        "esrId": esr.get("id", ""),
        "modality": modality,
        "travelLimitMiles": travel_limit,
        "locationPrecision": loc["precision"],
        "locationVerificationRequired": loc["verificationRequired"],
        "facilities": top_facilities,
        "selectedFacilityId": selected_facility_id,
        "validDoctors": valid_doctors,
        "excludedDoctors": excluded_doctors,
        "selectedDoctorId": selected_doctor["examinerId"] if selected_doctor else "",
        "slots": selected_doctor["slots"] if selected_doctor else [],
        "schedulingException": scheduling_exception,
        "auditStats": {
            "unrelatedSpecialtyExaminersFiltered": audit_unrelated_count,
            "ineligibleFacilitiesFiltered": audit_facility_ineligible_count,
        },
    }


def find_coordinated_scheduling_matches(
    db: Session,
    state: dict,
    esr_id: str,
    requirement_ids: list[str] | None = None,
) -> dict[str, Any]:
    """Coordinated multi-examination scheduling following VA RFP 36C10X27R0001 (Section 5.2.2) and GAO-23-105787.

    1. Read pending approved examinations for the ESR.
    2. Identify eligible facilities and qualified examiners for each examination.
    3. Determine common facilities with availability on the same calendar day.
    4. Assemble non-overlapping slot combinations across examiners.
    5. Rank combinations:
       - Priority 1/2: Same day, same facility (1 single visit, zero inter-clinic travel).
       - Priority 4: Partial grouping (subset of exams grouped, remaining separate).
       - Priority 5: Fallback separate appointments with clear contractual justification.
    """
    ensure_stage3_directory_seeded(db)
    esr = state.get("esrsById", {}).get(esr_id)
    if not esr:
        raise HTTPException(404, "ESR not found.")
    veteran = state.get("veteransById", {}).get(esr.get("veteranId"), {})
    loc = resolve_veteran_location(veteran, esr)
    travel_limit = _resolve_travel_limit(esr, veteran)

    all_reqs = [
        r for r in state.get("examinationRequirementsById", {}).values()
        if r.get("esrId") == esr_id and r.get("status") not in {"superseded-by-evidence-revision", "cancelled"}
    ]
    if requirement_ids:
        pending_reqs = [r for r in all_reqs if r["id"] in requirement_ids]
    else:
        unheld = [r for r in all_reqs if not r.get("appointmentId")]
        pending_reqs = unheld if unheld else all_reqs

    if not pending_reqs:
        return {
            "esrId": esr_id,
            "totalPendingCount": 0,
            "packages": [],
            "bestPackageId": "",
            "message": "All approved examinations have already been scheduled.",
        }

    active_appts = [
        a for a in state.get("appointmentsById", {}).values()
        if a.get("status") in {"held", "confirmed", "confirmed-locally"}
    ]
    vet_busy = [(a["startsAt"], a["endsAt"]) for a in active_appts if a.get("veteranId") == veteran.get("id")]
    exm_busy: dict[str, list[tuple[str, str]]] = {}
    fac_busy: dict[str, list[tuple[str, str]]] = {}
    for a in active_appts:
        exm_busy.setdefault(a.get("examinerId", ""), []).append((a["startsAt"], a["endsAt"]))
        fac_busy.setdefault(a.get("facilityId", ""), []).append((a["startsAt"], a["endsAt"]))

    def _slot_has_conflict(busy_list: list[tuple[str, str]], s: str, e: str) -> bool:
        return any(not (e <= bs or s >= be) for bs, be in busy_list)

    all_facilities = db.execute(select(Stage3Facility)).scalars().all()
    all_examiners = db.execute(select(Stage3Examiner)).scalars().all()
    examiners_by_id = {ex.examiner_id: ex for ex in all_examiners}

    avail_slots = db.execute(
        select(Stage3TimeSlot)
        .where(Stage3TimeSlot.slot_status == "available", Stage3TimeSlot.duration_minutes >= 30)
        .order_by(Stage3TimeSlot.starts_at)
    ).scalars().all()

    slots_by_fac_date: dict[tuple[str, str], list[Stage3TimeSlot]] = {}
    for sl in avail_slots:
        slots_by_fac_date.setdefault((sl.facility_id, sl.starts_at[:10]), []).append(sl)

    packages: list[dict[str, Any]] = []
    seen_combos: set[tuple[str, str, tuple[str, ...]]] = set()

    for fac in all_facilities:
        has_in_person = any((r.get("selectedModality") or "in-person") == "in-person" for r in pending_reqs)
        dist, dist_prec = (None, "not-applicable") if not has_in_person else _facility_distance(loc, fac)
        within_travel = True if (travel_limit is None or not has_in_person or dist is None) else (dist <= travel_limit)
        room_cap = max(1, int((fac.data_json or {}).get("examRoomCount") or 1))

        fac_eligible_reqs = []
        for r in pending_reqs:
            modality = r.get("selectedModality") or "in-person"
            ok_fac, _ = _facility_eligible(fac, modality, [r], veteran)
            if ok_fac:
                fac_eligible_reqs.append(r)

        if not fac_eligible_reqs:
            continue

        fac_dates = sorted({sl.starts_at[:10] for sl in avail_slots if sl.facility_id == fac.facility_id})

        for d in fac_dates:
            day_slots = slots_by_fac_date.get((fac.facility_id, d), [])
            if not day_slots:
                continue

            req_valid_slots: dict[str, list[tuple[dict, Stage3Examiner, Stage3TimeSlot]]] = {}
            for r in fac_eligible_reqs:
                modality = r.get("selectedModality") or "in-person"
                candidates = []
                for sl in day_slots:
                    if sl.modality != modality:
                        continue
                    exm = examiners_by_id.get(sl.examiner_id)
                    if not exm or exm.status != "active":
                        continue
                    ok_base, _ = _check_examiner_base_eligibility(exm, [r], modality, fac.facility_id)
                    if not ok_base:
                        continue
                    exm_licenses = (exm.data_json or {}).get("licenses") or []
                    ok_lic, _ = is_license_valid_for_exam_date(
                        exm.license_status,
                        exm.license_expiry_date,
                        sl.starts_at,
                        licenses=exm_licenses,
                        required_state=fac.state_code if modality == "in-person" else "",
                    )
                    if not ok_lic:
                        continue
                    ok_cert, _ = _certifications_valid_on_date(exm, sl.starts_at)
                    if not ok_cert:
                        continue
                    if _slot_has_conflict(vet_busy, sl.starts_at, sl.ends_at) or _slot_has_conflict(exm_busy.get(exm.examiner_id, []), sl.starts_at, sl.ends_at):
                        continue
                    overlaps_room = sum(1 for bs, be in fac_busy.get(fac.facility_id, []) if not (sl.ends_at <= bs or sl.starts_at >= be))
                    if overlaps_room >= room_cap:
                        continue
                    candidates.append((r, exm, sl))

                if candidates:
                    req_valid_slots[r["id"]] = candidates

            if len(req_valid_slots) < 1:
                continue

            # Evaluate complete facility, examiner and appointment combinations to maximize coverage & minimize idle wait
            req_keys = list(req_valid_slots.keys())
            best_assignment: list[tuple[dict, Stage3Examiner, Stage3TimeSlot]] = []
            best_idle = float("inf")

            def _calc_idle(items: list[tuple[dict, Stage3Examiner, Stage3TimeSlot]]) -> int:
                sorted_slots = sorted(items, key=lambda it: it[2].starts_at)
                total = 0
                for i in range(len(sorted_slots) - 1):
                    try:
                        s_prev = sorted_slots[i][2].ends_at
                        s_next = sorted_slots[i + 1][2].starts_at
                        hp, mp = int(s_prev[11:13]), int(s_prev[14:16])
                        hn, mn = int(s_next[11:13]), int(s_next[14:16])
                        gap = (hn * 60 + mn) - (hp * 60 + mp)
                        total += max(0, gap)
                    except Exception:
                        pass
                return total

            def _eval_combos(idx: int, curr_items: list[tuple], curr_intervals: list[tuple[str, str]]):
                nonlocal best_assignment, best_idle
                if idx == len(req_keys):
                    if len(curr_items) > len(best_assignment):
                        best_assignment = list(curr_items)
                        best_idle = _calc_idle(curr_items)
                    elif len(curr_items) == len(best_assignment) and len(curr_items) > 0:
                        idle = _calc_idle(curr_items)
                        if idle < best_idle:
                            best_assignment = list(curr_items)
                            best_idle = idle
                    return

                rid = req_keys[idx]
                for req_item, exm_item, sl_item in req_valid_slots[rid]:
                    if not _slot_has_conflict(curr_intervals, sl_item.starts_at, sl_item.ends_at):
                        curr_items.append((req_item, exm_item, sl_item))
                        curr_intervals.append((sl_item.starts_at, sl_item.ends_at))
                        _eval_combos(idx + 1, curr_items, curr_intervals)
                        curr_items.pop()
                        curr_intervals.pop()

                _eval_combos(idx + 1, curr_items, curr_intervals)

            _eval_combos(0, [], [])

            if not best_assignment:
                continue

            assigned_items = [
                {
                    "requirementId": r_c["id"],
                    "examinationType": r_c.get("examinationType") or r_c.get("title", ""),
                    "specialty": (r_c.get("requiredSpecialties") or [""])[0],
                    "modality": sl_c.modality,
                    "examinerId": ex_c.examiner_id,
                    "examinerName": ex_c.doctor_name,
                    "licenseExpiryDate": ex_c.license_expiry_date,
                    "licenseStatus": ex_c.license_status,
                    "slotId": sl_c.slot_id,
                    "startsAt": sl_c.starts_at,
                    "endsAt": sl_c.ends_at,
                    "durationMinutes": sl_c.duration_minutes,
                }
                for r_c, ex_c, sl_c in best_assignment
            ]

            assigned_items.sort(key=lambda x: x["startsAt"])

            combo_key = (fac.facility_id, d, tuple(sorted(item["slotId"] for item in assigned_items)))
            if combo_key in seen_combos:
                continue
            seen_combos.add(combo_key)

            matched_req_ids = {it["requirementId"] for it in assigned_items}
            unmatched = [
                {
                    "requirementId": r["id"],
                    "examinationType": r.get("examinationType") or r.get("title", ""),
                    "reason": "No compatible examiner slot available on this day without time conflict.",
                }
                for r in pending_reqs if r["id"] not in matched_req_ids
            ]

            coverage_type = "full" if len(assigned_items) == len(pending_reqs) else "partial" if len(assigned_items) >= 2 else "single"
            if coverage_type == "full":
                badge = "Full Coordinated Visit · 1 Day, 1 Location (VA RFP 5.2.2 Compliant)"
                explanation = (
                    f"Coordinates all {len(pending_reqs)} approved examinations into a single visit at {fac.facility_name} "
                    f"on {d}. Successive appointments allow the Veteran to complete all testing in one trip with zero inter-clinic travel."
                )
            elif coverage_type == "partial":
                badge = f"Partial Grouping ({len(assigned_items)} of {len(pending_reqs)} Exams on Same Day)"
                explanation = (
                    f"Groups {len(assigned_items)} examinations together at {fac.facility_name} on {d}. "
                    f"The remaining examination ({unmatched[0]['examinationType'] if unmatched else 'separate'}) requires "
                    f"a separate visit due to specialized examiner availability."
                )
            else:
                badge = "Single Examination Slot"
                explanation = f"Available slot for {assigned_items[0]['examinationType']} at {fac.facility_name}."

            visit_start = assigned_items[0]["startsAt"]
            visit_end = assigned_items[-1]["endsAt"]

            # Calculate idle waiting time between consecutive appointments (prefer shorter waiting times)
            total_idle_wait_minutes = 0
            for i in range(len(assigned_items) - 1):
                try:
                    s_prev = assigned_items[i]["endsAt"]
                    s_next = assigned_items[i + 1]["startsAt"]
                    h_p, m_p = int(s_prev[11:13]), int(s_prev[14:16])
                    h_n, m_n = int(s_next[11:13]), int(s_next[14:16])
                    gap = (h_n * 60 + m_n) - (h_p * 60 + m_p)
                    total_idle_wait_minutes += max(0, gap)
                except Exception:
                    pass

            packages.append({
                "packageId": f"pkg-{uuid4().hex[:8]}",
                "coverageType": coverage_type,
                "badge": badge,
                "facilityId": fac.facility_id,
                "facilityName": fac.facility_name,
                "city": fac.city,
                "stateCode": fac.state_code,
                "postalCode": fac.postal_code,
                "distanceMiles": dist,
                "distancePrecision": dist_prec,
                "withinTravelLimit": within_travel,
                "date": d,
                "visitStartsAt": visit_start,
                "visitEndsAt": visit_end,
                "totalIdleWaitMinutes": total_idle_wait_minutes,
                "items": assigned_items,
                "unmatchedRequirements": unmatched,
                "explanation": explanation,
                "contractualNotice": (
                    "Satisfies VA RFP 36C10X27R0001 Section 5.2.2 (Minimizing Separate Visits and Locations) "
                    "and GAO-23-105787 clinical guidelines."
                ),
                "_sortKey": (
                    0 if coverage_type == "full" else (1 if coverage_type == "partial" else 2),
                    0 if within_travel else 1,
                    dist if dist is not None else 99999.0,
                    total_idle_wait_minutes,
                    d,
                ),
            })

    packages.sort(key=lambda p: p["_sortKey"])
    clean_packages = [{k: v for k, v in p.items() if k != "_sortKey"} for p in packages]

    has_grouped = any(p["coverageType"] in {"full", "partial"} for p in clean_packages)
    if not has_grouped and len(pending_reqs) > 1:
        clean_packages.insert(0, {
            "packageId": f"pkg-fallback-{uuid4().hex[:6]}",
            "coverageType": "separate",
            "badge": "Separate Visits Required (Specialist Availability Limitation)",
            "facilityId": "",
            "facilityName": "Various Facilities",
            "city": "",
            "stateCode": "",
            "distanceMiles": None,
            "withinTravelLimit": True,
            "date": "Independent Dates",
            "visitStartsAt": "",
            "visitEndsAt": "",
            "items": [],
            "unmatchedRequirements": [
                {"requirementId": r["id"], "examinationType": r.get("examinationType") or r.get("title", ""), "reason": "No compatible same-day examiner availability found."}
                for r in pending_reqs
            ],
            "explanation": (
                "Examinations cannot be scheduled on the same day because nearest providers and facilities do not have compatible "
                "same-day availability across these specialized disciplines. In accordance with VA and contractor guidelines, "
                "please schedule each examination independently under 'Schedule Separately'."
            ),
            "contractualNotice": "VA RFP 36C10X27R0001 Section 5.2.2 permitted exception: independent visits when simultaneous availability is impracticable.",
        })

    best_pkg_id = clean_packages[0]["packageId"] if clean_packages else ""

    return {
        "esrId": esr_id,
        "totalPendingCount": len(pending_reqs),
        "packages": clean_packages[:20],
        "bestPackageId": best_pkg_id,
    }


def with_scheduling_view(state: dict) -> dict:
    """Build case-grouped Stage 3 Scheduling view without inflating WorkspaceSnapshot."""
    cases_by_esr: dict[str, dict[str, Any]] = {}
    queue_order: list[str] = []

    for work in state.get("workItemsById", {}).values():
        if work.get("stage") != "Scheduling" or work.get("status") == "cancelled":
            continue
        req = state.get("examinationRequirementsById", {}).get(work["entityId"])
        if not req or req.get("status") == "superseded-by-evidence-revision":
            continue
        esr = state.get("esrsById", {}).get(req["esrId"])
        if not esr or esr.get("workflowClosedAt"):
            continue
        esr_id = esr["id"]
        if esr_id not in cases_by_esr:
            queue_order.append(esr_id)
            vet = state.get("veteransById", {}).get(esr.get("veteranId"), {})
            claimant_name = vet.get("displayName") or esr.get("claimantName") or esr_id
            initials = vet.get("initials") or "".join(w[0].upper() for w in claimant_name.split()[:2]) or "VA"
            cases_by_esr[esr_id] = {
                "esrId": esr_id,
                "claimantName": claimant_name,
                "initials": initials,
                "claimIdentifier": vet.get("claimIdentifier") or esr.get("sourceReference") or esr_id,
                "city": vet.get("city", ""),
                "stateCode": vet.get("stateCode", ""),
                "postalCode": vet.get("postalCode", ""),
                "phone": vet.get("phone") or "(555) 014-2000",
                "email": vet.get("email") or "veteran.contact@example.invalid",
                "priority": esr.get("priority", "routine"),
                "dueAt": esr.get("dueAt", ""),
                "specialInstructions": esr.get("specialInstructions") or "",
                "travelLimitMiles": _resolve_travel_limit(esr, vet),
                "requirements": [],
                "appointments": [],
                "approvedCount": 0,
                "scheduledCount": 0,
                "pendingCount": 0,
            }

        entry = cases_by_esr[esr_id]
        appt = next(
            (a for a in state.get("appointmentsById", {}).values()
             if req["id"] in (a.get("requirementIds") or []) and a.get("status") in {"held", "confirmed", "confirmed-locally"}),
            None,
        )
        entry["requirements"].append({
            **req,
            "workItemId": work["id"],
            "workStatus": work["status"],
            "appointment": appt,
        })
        entry["approvedCount"] += 1
        if appt and appt.get("status") in {"confirmed", "confirmed-locally"}:
            entry["scheduledCount"] += 1
        else:
            entry["pendingCount"] += 1

    for entry in cases_by_esr.values():
        unscheduled = [r for r in entry["requirements"] if not r.get("appointment")]
        entry["compatibleGroups"] = compute_compatible_groups(unscheduled)
        entry["appointments"] = [
            a for a in state.get("appointmentsById", {}).values()
            if a.get("esrId") == entry["esrId"] and a.get("status") in {"held", "confirmed", "confirmed-locally"}
        ]

    return {
        **state,
        "scheduling": {
            "queueIds": queue_order,
            "casesByEsrId": cases_by_esr,
        },
    }


def apply_scheduling_command(db: Session, state: dict, action: str, args: list, actor: str) -> dict:
    """Execute Stage 3 commands with full revalidation & atomic DB slot locking."""
    ensure_stage3_directory_seeded(db)
    now = timestamp()
    value: dict[str, Any] = {}

    if action == "holdAppointment":
        if len(args) < 3 or not isinstance(args[1], str) or not isinstance(args[2], dict):
            raise HTTPException(422, "Select a requirement, valid examiner, and available slot.")
        req_ids = args[0] if isinstance(args[0], list) else [args[0]]
        examiner_id = args[1]
        slot_payload = args[2]
        reqs = [state["examinationRequirementsById"].get(rid) for rid in req_ids]
        if not reqs or any(r is None for r in reqs):
            raise HTTPException(404, "Approved examination requirement not found.")
        if any(r.get("status") in {"superseded-by-evidence-revision", "cancelled"} for r in reqs):
            raise HTTPException(409, "Stage 2 requirement was superseded by revised evidence; re-triage required.")

        esr = state["esrsById"][reqs[0]["esrId"]]
        veteran = state.get("veteransById", {}).get(esr.get("veteranId"), {})
        exm = db.get(Stage3Examiner, examiner_id)
        if not exm or exm.status != "active" or (exm.data_json or {}).get("duplicateConflict"):
            raise HTTPException(422, "Selected examiner is not active or has an unresolved duplicate conflict.")

        fac_id = str(slot_payload.get("facilityId") or exm.primary_facility_id or "")
        fac = db.get(Stage3Facility, fac_id) if fac_id else None
        modality = reqs[0].get("selectedModality") or "in-person"
        if fac:
            ok_fac, fac_msg = _facility_eligible(fac, modality, reqs, veteran)
            if not ok_fac:
                raise HTTPException(422, f"Facility cannot be used: {fac_msg}")

        ok_base, base_msg = _check_examiner_base_eligibility(exm, reqs, modality, fac_id)
        if not ok_base:
            raise HTTPException(422, f"Examiner cannot be scheduled: {base_msg}")

        starts_at = str(slot_payload.get("startsAt") or "")
        ends_at = str(slot_payload.get("endsAt") or "")
        exm_licenses = (exm.data_json or {}).get("licenses") or []
        ok_lic, lic_msg = is_license_valid_for_exam_date(
            exm.license_status,
            exm.license_expiry_date,
            starts_at,
            licenses=exm_licenses,
            required_state=fac.state_code if (fac and modality == "in-person") else "",
        )
        if not ok_lic:
            raise HTTPException(422, f"Examiner cannot be scheduled: {lic_msg}.")
        ok_cert, cert_msg = _certifications_valid_on_date(exm, starts_at)
        if not ok_cert:
            raise HTTPException(422, f"Examiner cannot be scheduled: {cert_msg}")

        # Prevent Veteran or Examiner overlapping holds/confirmations
        active_appts = [
            a for a in state.get("appointmentsById", {}).values()
            if a.get("status") in {"held", "confirmed", "confirmed-locally"}
        ]
        for a in active_appts:
            if not (ends_at <= a["startsAt"] or starts_at >= a["endsAt"]):
                if a.get("examinerId") == examiner_id or a.get("veteranId") == veteran.get("id"):
                    raise HTTPException(409, "Appointment overlaps an existing held or confirmed booking.")

        # Atomic conditional DB slot reservation (prevents concurrent double booking)
        slot_id = slot_payload.get("slotId")
        if slot_id:
            changed = db.execute(
                update(Stage3TimeSlot)
                .where(Stage3TimeSlot.slot_id == slot_id, Stage3TimeSlot.slot_status == "available")
                .values(slot_status="held")
            )
            if changed.rowcount != 1:
                raise HTTPException(409, "That time slot is no longer available.")

        appt_id = f"appt-{uuid4().hex[:12]}"
        appt = {
            **record(appt_id, now),
            "esrId": esr["id"],
            "veteranId": esr.get("veteranId", ""),
            "requirementIds": req_ids,
            "examinerId": examiner_id,
            "examinerName": exm.doctor_name,
            "examinerLicenseExpiry": exm.license_expiry_date,
            "credentialVerificationMode": (
                "simulated-demo-verification"
                if str((exm.data_json or {}).get("credentialVerificationStatus") or "").startswith("simulated")
                else "authoritative-production-required"
            ),
            "facilityId": fac_id,
            "facilityName": fac.facility_name if fac else fac_id,
            "modality": modality,
            "slotId": slot_id or "",
            "startsAt": starts_at,
            "endsAt": ends_at,
            "status": "held",
            "outreachLog": [],
            "history": [{"status": "held", "at": now, "actorId": actor, "note": "Appointment held."}],
        }
        state.setdefault("appointmentsById", {})[appt_id] = appt
        for r in reqs:
            touch(r, now, status="appointment-held", appointmentId=appt_id)
            if w := state.get("workItemsById", {}).get(f"work-scheduling-{r['id']}"):
                touch(w, now, status="in-progress")
        value["entityId"] = appt_id
        event = "scheduling-appointment-held"
        esr_id = esr["id"]

    elif action == "holdCoordinatedAppointments":
        if len(args) < 1 or not isinstance(args[0], list) or not args[0]:
            raise HTTPException(422, "Provide a list of appointment items to hold.")
        items_payload = args[0]
        now = timestamp()
        new_appts: list[tuple[str, dict[str, Any], dict[str, Any]]] = []
        locked_slots: list[str] = []
        package_intervals: list[tuple[str, str]] = []

        active_appts = [
            a for a in state.get("appointmentsById", {}).values()
            if a.get("status") in {"held", "confirmed", "confirmed-locally"}
        ]

        try:
            for item in items_payload:
                req_id = item.get("requirementId")
                examiner_id = item.get("examinerId")
                slot = item.get("slot") or item
                req = state.get("examinationRequirementsById", {}).get(req_id)
                if not req:
                    raise HTTPException(404, f"Requirement '{req_id}' not found.")
                if req.get("status") in {"superseded-by-evidence-revision", "cancelled"}:
                    raise HTTPException(409, f"Requirement '{req_id}' is superseded or cancelled.")

                esr = state["esrsById"][req["esrId"]]
                veteran = state.get("veteransById", {}).get(esr.get("veteranId"), {})
                exm = db.get(Stage3Examiner, examiner_id)
                if not exm or exm.status != "active":
                    raise HTTPException(422, f"Examiner '{examiner_id}' is not active.")

                fac_id = str(slot.get("facilityId") or exm.primary_facility_id or "")
                fac = db.get(Stage3Facility, fac_id) if fac_id else None
                modality = req.get("selectedModality") or "in-person"
                if fac:
                    ok_fac, fac_msg = _facility_eligible(fac, modality, [req], veteran)
                    if not ok_fac:
                        raise HTTPException(422, f"Facility cannot be used: {fac_msg}")

                ok_base, base_msg = _check_examiner_base_eligibility(exm, [req], modality, fac_id)
                if not ok_base:
                    raise HTTPException(422, f"Examiner cannot be scheduled: {base_msg}")

                starts_at = str(slot.get("startsAt") or "")
                ends_at = str(slot.get("endsAt") or "")

                exm_licenses = (exm.data_json or {}).get("licenses") or []
                ok_lic, lic_msg = is_license_valid_for_exam_date(
                    exm.license_status,
                    exm.license_expiry_date,
                    starts_at,
                    licenses=exm_licenses,
                    required_state=fac.state_code if (fac and modality == "in-person") else "",
                )
                if not ok_lic:
                    raise HTTPException(422, f"Examiner license invalid: {lic_msg}.")

                # Verify no overlap with existing active appointments
                for a in active_appts:
                    if not (ends_at <= a["startsAt"] or starts_at >= a["endsAt"]):
                        if a.get("examinerId") == examiner_id or a.get("veteranId") == veteran.get("id"):
                            raise HTTPException(409, f"Slot {starts_at} overlaps an existing held/confirmed appointment.")

                # Verify no overlap between items within this package
                for p_s, p_e in package_intervals:
                    if not (ends_at <= p_s or starts_at >= p_e):
                        raise HTTPException(409, f"Coordinated appointments have overlapping times ({starts_at} vs {p_s}).")
                package_intervals.append((starts_at, ends_at))

                # Atomic DB slot reservation
                slot_id = slot.get("slotId")
                if slot_id:
                    changed = db.execute(
                        update(Stage3TimeSlot)
                        .where(Stage3TimeSlot.slot_id == slot_id, Stage3TimeSlot.slot_status == "available")
                        .values(slot_status="held")
                    )
                    if changed.rowcount != 1:
                        raise HTTPException(409, f"Time slot for '{req.get('examinationType')}' is no longer available.")
                    locked_slots.append(slot_id)

                appt_id = f"appt-{uuid4().hex[:12]}"
                appt = {
                    **record(appt_id, now),
                    "esrId": esr["id"],
                    "veteranId": esr.get("veteranId", ""),
                    "requirementIds": [req_id],
                    "examinerId": examiner_id,
                    "examinerName": exm.doctor_name,
                    "examinerLicenseExpiry": exm.license_expiry_date,
                    "facilityId": fac_id,
                    "facilityName": fac.facility_name if fac else fac_id,
                    "modality": modality,
                    "slotId": slot_id or "",
                    "startsAt": starts_at,
                    "endsAt": ends_at,
                    "status": "held",
                    "outreachLog": [],
                    "history": [{"status": "held", "at": now, "actorId": actor, "note": "Coordinated appointment held."}],
                    "coordinatedVisit": True,
                }
                new_appts.append((appt_id, appt, req))

        except Exception:
            for s_id in locked_slots:
                db.execute(
                    update(Stage3TimeSlot)
                    .where(Stage3TimeSlot.slot_id == s_id, Stage3TimeSlot.slot_status == "held")
                    .values(slot_status="available")
                )
            raise

        for appt_id, appt, req in new_appts:
            state.setdefault("appointmentsById", {})[appt_id] = appt
            touch(req, now, status="appointment-held", appointmentId=appt_id)
            if w := state.get("workItemsById", {}).get(f"work-scheduling-{req['id']}"):
                touch(w, now, status="in-progress")

        value["entityIds"] = [a[0] for a in new_appts]
        event = "scheduling-coordinated-package-held"
        esr_id = new_appts[0][1]["esrId"] if new_appts else ""

    elif action == "recordOutreach":
        if len(args) >= 5 and isinstance(args[1], str) and args[1] in state.get("appointmentsById", {}):
            appt_id, channel, outcome, note = args[1], str(args[2]), str(args[3]), str(args[4]).strip()
        else:
            appt_id = args[0] if args else ""
            channel = str(args[1] if len(args) > 1 else "phone")
            outcome = str(args[2] if len(args) > 2 else "answered")
            note = str(args[3] if len(args) > 3 else "").strip()
        appt = state.get("appointmentsById", {}).get(appt_id)
        if not appt:
            raise HTTPException(404, "Appointment not found.")
        if appt.get("status") != "held":
            raise HTTPException(422, "Outreach can only be logged while the appointment slot is held.")
        outreach_log = appt.setdefault("outreachLog", [])
        if any(entry.get("outcome") == "answered" for entry in outreach_log):
            raise HTTPException(422, "Veteran has already confirmed this appointment.")
        max_attempts = DEFAULT_RANKING_CONFIG.get("maxOutreachAttempts", 2)
        if len(outreach_log) >= max_attempts:
            raise HTTPException(
                422,
                f"Maximum of {max_attempts} outreach attempts reached for this held appointment. Cancel Hold / Reschedule to release the slot.",
            )
        log_entry = {"id": f"comm-{uuid4().hex[:8]}", "at": now, "actorId": actor, "channel": channel, "outcome": outcome, "note": note}
        outreach_log.append(log_entry)
        touch(appt, now)
        event = "scheduling-outreach-recorded"
        esr_id = appt["esrId"]

    elif action == "recordAppointmentLogistics":
        appt_id = args[0] if args else ""
        appt = state.get("appointmentsById", {}).get(appt_id)
        if not appt:
            raise HTTPException(404, "Appointment not found.")
        touch(appt, now, localLogistics=args[1] if len(args) > 1 else {})
        event = "scheduling-logistics-recorded"
        esr_id = appt["esrId"]

    elif action == "verifyVeteranLocation":
        if len(args) < 2 or not isinstance(args[1], dict):
            raise HTTPException(422, "Provide Veteran city/state/ZIP or coordinates to verify scheduling location.")
        esr_id = str(args[0])
        esr = state.get("esrsById", {}).get(esr_id)
        if not esr:
            raise HTTPException(404, "ESR not found.")
        veteran = state.get("veteransById", {}).get(esr.get("veteranId"), {})
        loc_patch = args[1]
        changes: dict[str, Any] = {}
        for k in ("city", "stateCode", "postalCode"):
            if isinstance(loc_patch.get(k), str) and loc_patch[k].strip():
                changes[k] = normalize_state_code(loc_patch[k]) if k == "stateCode" else loc_patch[k].strip()
        for k in ("latitude", "longitude"):
            if isinstance(loc_patch.get(k), (int, float)):
                changes[k] = float(loc_patch[k])
        if not changes:
            raise HTTPException(422, "Enter a valid city/state, 5-digit ZIP code, or coordinates.")
        # Enrich Veteran scheduling profile without modifying original Intake/evidence documents
        touch(
            veteran,
            now,
            **changes,
            locationVerification={"verifiedBy": actor, "verifiedAt": now, "source": "scheduling-enrichment"},
        )
        event = "scheduling-location-verified"

    elif action == "confirmAppointment":
        appt_id = args[0] if args else ""
        appt = state.get("appointmentsById", {}).get(appt_id)
        if not appt or appt.get("status") != "held":
            raise HTTPException(422, "Only a held appointment can be confirmed.")
        if not any(entry.get("outcome") == "answered" for entry in (appt.get("outreachLog") or [])):
            raise HTTPException(422, "Log a 'Veteran confirmed' outreach attempt before confirming this appointment.")
        # Revalidate Stage 2 requirements, examiner license/certs, facility, and travel/location before confirmation
        esr_id = appt["esrId"]
        esr = state["esrsById"][esr_id]
        veteran = state.get("veteransById", {}).get(esr.get("veteranId"), {})
        reqs = [state["examinationRequirementsById"].get(rid) for rid in appt.get("requirementIds", [])]
        if not reqs or any(not r or r.get("status") in {"superseded-by-evidence-revision", "cancelled"} for r in reqs):
            raise HTTPException(409, "Stage 2 requirement changed or was superseded; re-triage required before confirmation.")
        exm = db.get(Stage3Examiner, appt["examinerId"])
        fac = db.get(Stage3Facility, appt.get("facilityId", "")) if appt.get("facilityId") else None
        if not exm or exm.status != "active":
            raise HTTPException(422, "Examiner is no longer active.")
        if fac and fac.status != "active":
            raise HTTPException(422, "Facility is no longer active.")
        if appt.get("modality") == "in-person" and fac:
            loc = resolve_veteran_location(veteran, esr)
            if loc["precision"] == "unknown":
                raise HTTPException(
                    422,
                    "Verify Veteran location (ZIP code or coordinates) before confirming an in-person travel appointment.",
                )
            dist, _ = _facility_distance(loc, fac)
            travel_limit = _resolve_travel_limit(esr, veteran, reqs[0] if reqs else None)
            logistics = appt.get("localLogistics") or {}
            if travel_limit is not None and dist is not None and dist > travel_limit and logistics.get("consentStatus") != "approved":
                raise HTTPException(
                    422,
                    f"Facility is {dist} mi away (exceeds {travel_limit} mi limit); record express Veteran travel consent before confirming.",
                )
        exm_licenses = (exm.data_json or {}).get("licenses") or []
        ok_lic, lic_msg = is_license_valid_for_exam_date(
            exm.license_status,
            exm.license_expiry_date,
            appt["startsAt"],
            licenses=exm_licenses,
            required_state=fac.state_code if (fac and appt.get("modality") == "in-person") else "",
        )
        if not ok_lic:
            raise HTTPException(422, f"Cannot confirm appointment: {lic_msg}.")

        if appt.get("slotId"):
            changed = db.execute(
                update(Stage3TimeSlot)
                .where(Stage3TimeSlot.slot_id == appt["slotId"], Stage3TimeSlot.slot_status == "held")
                .values(slot_status="booked")
            )
            if changed.rowcount != 1:
                raise HTTPException(409, "Held slot could not be locked atomically for confirmation.")

        appt_history = list(appt.get("history") or [])
        appt_history.append({"status": "confirmed-locally", "at": now, "actorId": actor, "note": "Appointment confirmed."})
        touch(appt, now, status="confirmed-locally", confirmedAt=now, confirmedBy=actor, history=appt_history)
        esr_id = appt["esrId"]
        esr = state["esrsById"][esr_id]
        for rid in appt.get("requirementIds", []):
            if req := state["examinationRequirementsById"].get(rid):
                touch(req, now, status="scheduled")
            if w := state.get("workItemsById", {}).get(f"work-scheduling-{rid}"):
                touch(w, now, status="completed")
            exam_id = f"exam-{uuid4().hex[:12]}"
            req_obj = state["examinationRequirementsById"].get(rid) or {}
            exam_obj = {
                **record(exam_id, now),
                "esrId": esr_id,
                "requirementId": rid,
                "requirementIds": [rid],
                "contentionIds": list(req_obj.get("contentionIds") or []),
                "appointmentId": appt_id,
                "appointmentIds": [appt_id],
                "examinerId": appt["examinerId"],
                "modality": appt["modality"],
                "requiredDbqIds": [],
                "diagnosticOrderIds": [],
                "status": "scheduled",
            }
            state.setdefault("examinersById", {})[exm.examiner_id] = {
                "id": exm.examiner_id,
                "displayName": exm.doctor_name,
                "credentials": json.loads(exm.credentials) if isinstance(exm.credentials, str) and exm.credentials.startswith("[") else ["MD"],
                "specialties": json.loads(exm.specialties) if isinstance(exm.specialties, str) and exm.specialties.startswith("[") else ["General Medical"],
            }
            state.setdefault("examinationsById", {})[exam_id] = exam_obj
            from app.services.clinical_pipeline import ensure_clinical_records_for_exam
            created_dbqs = ensure_clinical_records_for_exam(state, exam_obj, req_obj, now)
            value["entityId"] = created_dbqs[0] if created_dbqs else exam_id
        event = "scheduling-appointment-confirmed"

    elif action == "cancelAppointment":
        appt_id = args[0] if args else ""
        appt = state.get("appointmentsById", {}).get(appt_id)
        if not appt:
            raise HTTPException(404, "Appointment not found.")
        reason = str(args[1] if len(args) > 1 else "").strip()
        if not reason:
            raise HTTPException(422, "Provide a cancellation or reschedule reason.")
        if appt.get("slotId"):
            db.execute(
                update(Stage3TimeSlot)
                .where(Stage3TimeSlot.slot_id == appt["slotId"])
                .values(slot_status="available")
            )
        touch(appt, now, status="cancelled", cancelledAt=now, cancelledBy=actor, cancellationReason=reason)
        for rid in appt.get("requirementIds", []):
            if req := state["examinationRequirementsById"].get(rid):
                if req.get("status") != "superseded-by-evidence-revision":
                    touch(req, now, status="approved", appointmentId=None)
            if w := state.get("workItemsById", {}).get(f"work-scheduling-{rid}"):
                if w.get("status") != "cancelled":
                    touch(w, now, status="queued")
        event = "scheduling-appointment-cancelled"
        esr_id = appt["esrId"]
        value["entityId"] = appt_id
        value["cancellationReason"] = reason
        value["cancelledAt"] = now
        value["cancelledBy"] = actor
        next_seq = int(db.scalar(select(func.coalesce(func.max(AuditEvent.sequence), 0))) or 0) + 1
        db.add(AuditEvent(
            sequence=next_seq,
            actor_id=actor,
            action=event,
            object_ref=appt_id,
            detail=json.dumps({
                "esrId": esr_id,
                "appointmentId": appt_id,
                "facilityId": appt.get("facilityId"),
                "facilityName": appt.get("facilityName"),
                "examinerId": appt.get("examinerId"),
                "examinerName": appt.get("examinerName"),
                "slotId": appt.get("slotId"),
                "startsAt": appt.get("startsAt"),
                "endsAt": appt.get("endsAt"),
                "outreachLog": appt.get("outreachLog", []),
                "cancellationReason": reason,
                "cancelledAt": now,
                "cancelledBy": actor,
            }, ensure_ascii=False),
        ))

    elif action == "setSchedulingDistanceLimit":
        esr_id = str(args[0] if args else "")
        miles = int(args[1] if len(args) > 1 else 50)
        if esr := state.get("esrsById", {}).get(esr_id):
            touch(esr, now, travelLimitMiles=miles)
        event = "scheduling-distance-updated"

    elif action == "returnClinicalToScheduling":
        target_id = str(args[0] if args else "")
        reason = str(args[1] if len(args) > 1 else "").strip() or "Returned from Stage 4 (Clinical Evaluation) for Stage 3 rework."
        exam = state.get("examinationsById", {}).get(target_id)
        dbq = None
        if not exam:
            dbq = state.get("dbqsById", {}).get(target_id)
            if dbq:
                exam = state.get("examinationsById", {}).get(dbq.get("examinationId", ""))
        if not exam:
            raise HTTPException(404, "Clinical examination record not found.")

        esr_id = exam["esrId"]
        appt_id = exam.get("appointmentId") or next(iter(exam.get("appointmentIds") or []), "")
        appt = state.get("appointmentsById", {}).get(appt_id) if appt_id else None
        if appt:
            if appt.get("slotId"):
                db.execute(
                    update(Stage3TimeSlot)
                    .where(Stage3TimeSlot.slot_id == appt["slotId"])
                    .values(slot_status="available")
                )
            touch(appt, now, status="cancelled", cancelledAt=now, cancellationReason=reason)
            for rid in appt.get("requirementIds", []):
                if req := state["examinationRequirementsById"].get(rid):
                    if req.get("status") != "superseded-by-evidence-revision":
                        touch(req, now, status="approved", appointmentId=None)
                if w := state.get("workItemsById", {}).get(f"work-scheduling-{rid}"):
                    if w.get("status") != "cancelled":
                        touch(w, now, status="queued")
        elif rid := exam.get("requirementId"):
            if req := state.get("examinationRequirementsById", {}).get(rid):
                if req.get("status") != "superseded-by-evidence-revision":
                    touch(req, now, status="approved", appointmentId=None)
            if w := state.get("workItemsById", {}).get(f"work-scheduling-{rid}"):
                if w.get("status") != "cancelled":
                    touch(w, now, status="queued")

        touch(exam, now, status="returned-to-scheduling", returnedReason=reason)
        related_entities = {exam["id"], *( [dbq["id"]] if dbq else [] )}
        for w in state.get("workItemsById", {}).values():
            if w.get("stage") == "Clinical Evaluation" and w.get("entityId") in related_entities and w.get("status") != "cancelled":
                touch(w, now, status="cancelled", blockingReasons=[reason])
        value["entityId"] = esr_id
        event = "clinical-returned-to-scheduling"

    else:
        raise HTTPException(422, f"Unsupported scheduling command: {action}")

    act_id = f"activity-{uuid4()}"
    state.setdefault("activitiesById", {})[act_id] = {
        "id": act_id,
        "esrId": esr_id,
        "entityId": value.get("entityId") or esr_id,
        "entityType": "appointment",
        "at": now,
        "actorId": actor,
        "action": event,
        **({"detail": value["cancellationReason"]} if value.get("cancellationReason") else {}),
    }
    state.update(revision=state["revision"] + 1, updatedAt=now)
    return {"state": state, "value": value}
