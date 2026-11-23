import json
from functools import lru_cache
from pathlib import PurePosixPath

from app.services.reference_store import FORM_INDEX, FORM_ROOT, reference_bytes

# These are contractor workflow labels mapped to form revisions in the bundled catalog.
DBQ_REVISIONS = {
    "Knee and Lower Leg Conditions DBQ": 2977,
    "Hearing Loss and Tinnitus DBQ": 2563,
    "Back Conditions DBQ": 3049,
    "Mental Disorders DBQ": 2959,
    "Headaches DBQ": 3011,
    "Skin Diseases DBQ": 3016,
    "Peripheral Nerves DBQ": 2979,
    "Shoulder and Arm Conditions DBQ": 2605,
    "Medical Opinion DBQ": 2632,
    "General Medical DBQ": 2107,
    "Diabetes Mellitus DBQ": 2631,
}

REQUESTED_FORM_ALIASES = {
    "musckneeandlowerleg": "Knee and Lower Leg Conditions DBQ",
    "audiohearinglossandtinnitus": "Hearing Loss and Tinnitus DBQ",
    "muscbackthoracolumbarspine": "Back Conditions DBQ",
    "psychmentaldisordersotherthanptsd": "Mental Disorders DBQ",
    "neuroheadachesincludingmigraineheadaches": "Headaches DBQ",
    "dermskindiseases": "Skin Diseases DBQ",
    "neuroperipheralnerves": "Peripheral Nerves DBQ",
    "muscshoulderandarm": "Shoulder and Arm Conditions DBQ",
    "medicalopinion": "Medical Opinion DBQ",
    "endodiabetesmellitus": "Diabetes Mellitus DBQ",
}


@lru_cache(maxsize=1)
def catalog() -> dict[int, dict]:
    rows = json.loads(reference_bytes(FORM_INDEX))["documents"]
    return {int(item["revisionId"]): item for item in rows}


def catalog_references(labels: list[str]) -> dict[str, dict]:
    forms = catalog()
    return {
        label: {"documentId": row["documentId"], "revisionId": row["revisionId"], "formName": row["formName"]}
        for label in labels if (row := forms.get(DBQ_REVISIONS.get(label)))
    }


def requested_form_label(label: str) -> str | None:
    key = "".join(character for character in label.casefold() if character.isalnum()).removeprefix("dbq")
    return REQUESTED_FORM_ALIASES.get(key) or next(
        (name for name in DBQ_REVISIONS if key == "".join(c for c in name.casefold() if c.isalnum()).removesuffix("dbq")),
        None,
    )


@lru_cache(maxsize=1)
def reference_forms() -> list[dict]:
    forms = []
    for row in catalog().values():
        layout = json.loads(reference_bytes(FORM_ROOT / row["documentStructureJson"]))
        rules = json.loads(reference_bytes(FORM_ROOT / row["conditionalLogic"]))
        source = PurePosixPath(row["documentStructureJson"]).name
        forms.append({
            "id": f"dbq-{row['revisionId']}", "title": layout.get("form") or row["formName"],
            "bodySystem": layout.get("bodySystem", ""), "revision": row["revisionId"],
            "documentId": row["documentId"], "source": source,
            "sections": [section["text"] for section in layout.get("structure", []) if section.get("text")],
            "fieldCount": len(rules.get("defaultFieldState", [])),
            "ruleCount": sum(len(items) for key, items in rules.items() if key != "defaultFieldState" and isinstance(items, list)),
        })
    return sorted(forms, key=lambda row: (row["title"].casefold(), row["revision"]))
