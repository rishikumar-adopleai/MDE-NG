from app.core.database import SessionLocal
from app.services.intake_store import get_state, touch, record
from app.services.workspace_store import save_next_state
from app.services.scheduling import with_scheduling_view

db = SessionLocal()
state = get_state(db)
now = "2026-11-26T12:00:00Z"

# 1. Update Riley Q Example (esr-local-99d9ccd6661a) to have unheld pending requirements
for rid in ["req-99ff6a04604b4013b2aeffaf7fbde947", "req-45d202bc760c4b9a8862fbdf4f19d4d5", "req-b57ed5c697bc43a985612b01b9f30cbc"]:
    if r := state.get("examinationRequirementsById", {}).get(rid):
        r["status"] = "approved"
        r.pop("appointmentId", None)
        if w := state.get("workItemsById", {}).get(f"work-scheduling-{rid}"):
            w["status"] = "queued"

# Clear held appointments for Riley Q Example so it is purely 'Needs Scheduling' (0/3 Scheduled)
state["appointmentsById"] = {
    aid: a for aid, a in state.get("appointmentsById", {}).items()
    if a.get("esrId") != "esr-local-99d9ccd6661a"
}

# 2. Add Morgan Q Demo (esr-local-89a1583b3970) as 'Outreach Pending' (1 held appointment with 1 voicemail)
esr_morgan = state["esrsById"]["esr-local-89a1583b3970"]
esr_morgan["priority"] = "expedited"
esr_morgan["dueAt"] = "2026-12-01T00:00:00Z"
vet_morgan = state["veteransById"]["vet-local-89a1583b3970"]
vet_morgan["postalCode"] = "43215"
vet_morgan["city"] = "Columbus"
vet_morgan["stateCode"] = "OH"

req_morgan_id = "req-morgan-knee-01"
state["examinationRequirementsById"][req_morgan_id] = {
    **record(req_morgan_id, now),
    "esrId": "esr-local-89a1583b3970",
    "examinationType": "Right Knee Examination",
    "title": "Right Knee Examination",
    "requiredSpecialties": ["Orthopedics / Musculoskeletal"],
    "requiredCredentials": ["MD"],
    "requestedDbqTypes": ["Knee and Lower Leg DBQ"],
    "requestedDiagnostics": [],
    "selectedModality": "in-person",
    "status": "appointment-held",
    "appointmentId": "appt-morgan-01",
}
state["workItemsById"][f"work-scheduling-{req_morgan_id}"] = {
    **record(f"work-scheduling-{req_morgan_id}", now),
    "esrId": "esr-local-89a1583b3970",
    "entityId": req_morgan_id,
    "entityType": "examination-requirement",
    "stage": "Scheduling",
    "title": "Schedule Right Knee Examination",
    "priority": "expedited",
    "status": "in-progress",
    "blockingReasons": [],
}
state["appointmentsById"]["appt-morgan-01"] = {
    **record("appt-morgan-01", now),
    "esrId": "esr-local-89a1583b3970",
    "veteranId": "vet-local-89a1583b3970",
    "requirementIds": [req_morgan_id],
    "examinerId": "DEMO-DOC-00001",
    "examinerName": "Dr. Sarah Jenkins, MD",
    "examinerLicenseExpiry": "2027-08-31",
    "facilityId": "DEMO-FAC-00031",
    "facilityName": "Demo Santa Fe Diagnostic Assessment Center 31",
    "modality": "in-person",
    "slotId": "slot-morgan-demo",
    "startsAt": "2026-12-01T14:00:00Z",
    "endsAt": "2026-12-01T14:45:00Z",
    "status": "held",
    "outreachLog": [
        {
            "id": "comm-morgan-1",
            "at": "2026-11-26T10:00:00Z",
            "actorId": "scheduler-system",
            "channel": "phone",
            "outcome": "left-voicemail",
            "note": "Attempt 1/2: Left voicemail regarding Dec 1st appointment date.",
        }
    ],
}

# 3. Add Jordan K River (esr-local-jordan-river) as 'Fully Scheduled' (2/2 confirmed appointments)
esr_jordan_id = "esr-local-jordan-river"
vet_jordan_id = "vet-local-jordan-river"
state["veteransById"][vet_jordan_id] = {
    **record(vet_jordan_id, now),
    "displayName": "Jordan K River",
    "initials": "JR",
    "claimIdentifier": "TEST-ESR-003",
    "city": "Cincinnati",
    "stateCode": "OH",
    "postalCode": "45202",
    "phone": "(555) 349-1100",
    "email": "jordan.river@example.invalid",
}
state["esrsById"][esr_jordan_id] = {
    **record(esr_jordan_id, now),
    "veteranId": vet_jordan_id,
    "claimantName": "Jordan K River",
    "sourceReference": "TEST-ESR-003",
    "priority": "routine",
    "dueAt": "2026-12-25T00:00:00Z",
    "assignedTeamId": "team-scheduling-oh",
}
req_jordan_1 = "req-jordan-aud-01"
req_jordan_2 = "req-jordan-gen-02"
state["examinationRequirementsById"][req_jordan_1] = {
    **record(req_jordan_1, now),
    "esrId": esr_jordan_id,
    "examinationType": "Audiology Assessment",
    "title": "Audiology Assessment",
    "requiredSpecialties": ["Audiology"],
    "requiredCredentials": ["AuD"],
    "requestedDbqTypes": ["Hearing Loss and Tinnitus DBQ"],
    "requestedDiagnostics": [],
    "selectedModality": "in-person",
    "status": "scheduled",
    "appointmentId": "appt-jordan-01",
}
state["examinationRequirementsById"][req_jordan_2] = {
    **record(req_jordan_2, now),
    "esrId": esr_jordan_id,
    "examinationType": "General Medical Examination",
    "title": "General Medical Examination",
    "requiredSpecialties": ["General Medical"],
    "requiredCredentials": ["MD"],
    "requestedDbqTypes": ["Medical Opinion DBQ"],
    "requestedDiagnostics": [],
    "selectedModality": "in-person",
    "status": "scheduled",
    "appointmentId": "appt-jordan-02",
}
state["workItemsById"][f"work-scheduling-{req_jordan_1}"] = {
    **record(f"work-scheduling-{req_jordan_1}", now),
    "esrId": esr_jordan_id,
    "entityId": req_jordan_1,
    "entityType": "examination-requirement",
    "stage": "Scheduling",
    "title": "Schedule Audiology Assessment",
    "priority": "routine",
    "status": "completed",
    "blockingReasons": [],
}
state["workItemsById"][f"work-scheduling-{req_jordan_2}"] = {
    **record(f"work-scheduling-{req_jordan_2}", now),
    "esrId": esr_jordan_id,
    "entityId": req_jordan_2,
    "entityType": "examination-requirement",
    "stage": "Scheduling",
    "title": "Schedule General Medical Examination",
    "priority": "routine",
    "status": "completed",
    "blockingReasons": [],
}
state["appointmentsById"]["appt-jordan-01"] = {
    **record("appt-jordan-01", now),
    "esrId": esr_jordan_id,
    "veteranId": vet_jordan_id,
    "requirementIds": [req_jordan_1],
    "examinerId": "DEMO-DOC-00002",
    "examinerName": "Victoria Hill, AuD",
    "examinerLicenseExpiry": "2027-10-31",
    "facilityId": "DEMO-FAC-00031",
    "facilityName": "Demo Santa Fe Diagnostic Assessment Center 31",
    "modality": "in-person",
    "slotId": "slot-jordan-aud",
    "startsAt": "2026-12-05T09:00:00Z",
    "endsAt": "2026-12-05T09:45:00Z",
    "status": "confirmed-locally",
    "outreachLog": [
        {"id": "comm-jordan-1", "at": now, "actorId": "scheduler", "channel": "phone", "outcome": "answered", "note": "Veteran confirmed availability."}
    ],
}
state["appointmentsById"]["appt-jordan-02"] = {
    **record("appt-jordan-02", now),
    "esrId": esr_jordan_id,
    "veteranId": vet_jordan_id,
    "requirementIds": [req_jordan_2],
    "examinerId": "DEMO-DOC-00003",
    "examinerName": "Robert Mitchell, MD",
    "examinerLicenseExpiry": "2027-12-31",
    "facilityId": "DEMO-FAC-00031",
    "facilityName": "Demo Santa Fe Diagnostic Assessment Center 31",
    "modality": "in-person",
    "slotId": "slot-jordan-gen",
    "startsAt": "2026-12-05T10:15:00Z",
    "endsAt": "2026-12-05T11:00:00Z",
    "status": "confirmed-locally",
    "outreachLog": [
        {"id": "comm-jordan-2", "at": now, "actorId": "scheduler", "channel": "phone", "outcome": "answered", "note": "Veteran confirmed availability."}
    ],
}

# Save next state
current_rev = state.get("revision", 0)
state["revision"] = current_rev + 1
save_next_state(db, current_rev, state)

view = with_scheduling_view(state)
print("Seeding successful!")
print("Scheduling queue cases count:", len(view["scheduling"]["queueIds"]))
for cid in view["scheduling"]["queueIds"]:
    c = view["scheduling"]["casesByEsrId"][cid]
    print(f"  - {c['claimantName']} ({c['claimIdentifier']}): {c['scheduledCount']}/{c['approvedCount']} Scheduled, Pending: {c['pendingCount']}, Priority: {c['priority']}")

db.close()
