import sqlite3
import json
from datetime import datetime, timezone
from pathlib import Path

def reset_workspace_data():
    backend_root = Path(__file__).resolve().parent.parent
    db_path = backend_root / "data" / "mde-ng-dev.sqlite3"

    if not db_path.exists():
        print(f"Database not found at {db_path}")
        return

    con = sqlite3.connect(str(db_path))
    cur = con.cursor()

    # Load existing demo workspace snapshot
    row = cur.execute("SELECT state FROM workspace_snapshots WHERE id='mde-ng-demo-workspace'").fetchone()
    state = json.loads(row[0]) if row else {}

    # Preserve reference data (facilities, examiners, DBQ catalogs)
    facilities = state.get("facilitiesById", {})
    examiners = state.get("examinersById", {})
    dbqs = state.get("dbqsById", {})
    dbq_versions = state.get("dbqVersionsById", {})

    # Patient & Case collections to wipe clean
    patient_collections = [
        "veteransById", "esrsById", "contentionsById", "examinationRequirementsById",
        "examinationsById", "appointmentsById", "diagnosticOrdersById", "diagnosticResultsById",
        "evidenceDocumentsById", "intakeExceptionsById", "communicationsById",
        "outreachAttemptsById", "qaReviewsById", "qaFindingsById", "correctionRequestsById",
        "resultsPackagesById", "deliveryEventsById", "aiInsightsById", "workItemsById", "activitiesById"
    ]

    for key in patient_collections:
        state[key] = {}

    state["facilitiesById"] = facilities
    state["examinersById"] = examiners
    state["dbqsById"] = dbqs
    state["dbqVersionsById"] = dbq_versions
    state["loadedExamRequestScenarios"] = []
    state["revision"] = 0
    state["updatedAt"] = datetime.now(timezone.utc).isoformat()

    if "intake" in state:
        state["intake"] = {"queueIds": [], "viewsByEsrId": {}}
    if "triage" in state:
        state["triage"] = {"rows": [], "examChoices": []}

    # Update demo workspace
    cur.execute("UPDATE workspace_snapshots SET state = ?, revision = 0 WHERE id='mde-ng-demo-workspace'", (json.dumps(state),))

    # Delete legacy generated snapshots, outbox jobs, audit events, idempotency records
    cur.execute("DELETE FROM workspace_snapshots WHERE id='mde-ng-legacy-generated-records'")
    cur.execute("DELETE FROM outbox_jobs")
    cur.execute("DELETE FROM audit_events")
    cur.execute("DELETE FROM idempotency_records")

    con.commit()
    con.close()

    print("Success: All patient data, case records, and queue items have been completely removed.")
    print("Admin accounts, examiners, facilities, and DBQ templates are safely preserved.")

if __name__ == "__main__":
    reset_workspace_data()
