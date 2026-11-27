from hashlib import sha256
from string import ascii_uppercase

FIRST_NAMES = ("Avery", "Blake", "Cameron", "Casey", "Drew", "Emerson", "Finley",
               "Harper", "Jamie", "Jordan", "Morgan", "Parker", "Reese", "Riley", "Rowan", "Taylor")
LAST_NAMES = ("Abbott", "Barton", "Callahan", "Cruz", "Dalton", "Elliott", "Fleming",
              "Griffin", "Hale", "Irwin", "Keller", "Lane", "Marsh", "Palmer", "Reed", "Wells")
EXAMPLE_DOCUMENT_NAMES = {"synthetic-service-note.txt", "service-treatment-note.txt"}
PLACEHOLDER_NAMES = {"Synthetic Intake Claimant", "Intake Claimant"}

def example_claimant(esr_id: str) -> tuple[str, str]:
    first_key, last_key, middle_key = sha256(esr_id.encode()).digest()[:3]
    first = FIRST_NAMES[first_key % len(FIRST_NAMES)]
    last = LAST_NAMES[last_key % len(LAST_NAMES)]
    middle = ascii_uppercase[middle_key % len(ascii_uppercase)]
    return f"{last}, {first} {middle}.", f"{first[0]}{last[0]}"

def is_legacy_example(state: dict, esr: dict) -> bool:
    claimant = state["veteransById"].get(esr.get("veteranId"))
    return bool(
        esr["id"].startswith("esr-local-") and claimant and claimant.get("displayName") in PLACEHOLDER_NAMES
        and any(
            state["evidenceDocumentsById"].get(key, {}).get("sourceReference") in EXAMPLE_DOCUMENT_NAMES
            for key in esr["evidenceDocumentIds"]
        )
    )
