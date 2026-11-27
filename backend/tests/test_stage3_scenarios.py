"""Comprehensive Stage 3 Intelligent Examination Scheduling Scenario Test Suite.

Tests 7 diverse real-world scenarios:
  1. Multi-Specialty Coordinated Single-Visit Scheduling (VA RFP Section 5.2.2)
  2. Veteran Declines/Cancels Hold with Reason & Atomic Slot Release
  3. 2-Attempt Outreach Rule & Local Confirmation Release to Stage 4
  4. Travel Limit Distance Check & Express Consent Validation
  5. Unverified Location Protection & Clean Location Enrichment
  6. Same-Specialty Bundling (GAO-23-105787 Multiple DBQs per Exam)
  7. Graceful Fallback when Same-Day Availability is Impracticable
"""

import unittest
from uuid import uuid4

from fastapi import HTTPException
from sqlalchemy import select

from app.core.database import Base, SessionLocal, engine
from app.models import AuditEvent, Stage3Examiner, Stage3Facility, Stage3TimeSlot
from app.services.intake_store import get_state, touch
from app.services.scheduling import (
    apply_scheduling_command,
    ensure_stage3_directory_seeded,
    find_coordinated_scheduling_matches,
    find_scheduling_matches,
    resolve_veteran_location,
    with_scheduling_view,
)


class Stage3ScenarioTests(unittest.TestCase):
    def setUp(self):
        Base.metadata.create_all(engine)
        self.db = SessionLocal()
        ensure_stage3_directory_seeded(self.db)
        self.state = get_state(self.db)
        self.esr_id = next(iter(self.state.get("esrsById", {}).keys()), "esr-local-test")
        if self.esr_id not in self.state.get("esrsById", {}):
            self.state["esrsById"][self.esr_id] = {
                "id": self.esr_id, "veteranId": "vet-test-1", "contentionIds": ["c1", "c2", "c3"],
                "status": "in-progress", "priority": "routine",
            }
            self.state["veteransById"]["vet-test-1"] = {
                "id": "vet-test-1", "fullName": "Test Veteran", "city": "Columbus", "stateCode": "OH", "postalCode": "43215"
            }

    def tearDown(self):
        self.db.close()

    def _reset_esr_requirements(self):
        """Reset test ESR requirements to unheld 'approved' status."""
        esr = self.state["esrsById"][self.esr_id]
        veteran = self.state["veteransById"].setdefault(esr["veteranId"], {
            "id": esr["veteranId"], "fullName": "Morgan Q Demo", "city": "Columbus", "stateCode": "OH", "postalCode": "43215"
        })
        veteran["postalCode"] = "43215"
        veteran["stateCode"] = "OH"
        veteran["city"] = "Columbus"

        specs = [
            ("req-test-1", "Knee and Lower Leg", "Orthopedics / Musculoskeletal", "in-person", ["Knee and Lower Leg Conditions DBQ"]),
            ("req-test-2", "Hearing Loss and Tinnitus", "Audiology", "in-person", ["Hearing Loss and Tinnitus DBQ"]),
            ("req-test-3", "General Medical", "General Medicine", "in-person", ["General Medical DBQ"]),
        ]
        self.state["examinationRequirementsById"] = {
            rid: r for rid, r in self.state.get("examinationRequirementsById", {}).items()
            if r.get("esrId") != self.esr_id
        }
        for req_id, title, spec, mod, dbqs in specs:
            self.state["examinationRequirementsById"][req_id] = {
                "id": req_id, "esrId": self.esr_id, "title": title, "status": "approved",
                "examinationType": title, "requiredSpecialties": [spec], "requiredCredentials": ["MD / DO", "AuD"],
                "selectedModality": mod, "requestedDbqTypes": dbqs, "requestedDiagnostics": [],
            }

        for r in self.state.get("examinationRequirementsById", {}).values():
            if r.get("esrId") == self.esr_id:
                r["status"] = "approved"
                r.pop("appointmentId", None)
        self.state["appointmentsById"] = {
            aid: a for aid, a in self.state.get("appointmentsById", {}).items()
            if a.get("esrId") != self.esr_id
        }

    def test_scenario_1_multi_specialty_coordinated_visit(self):
        """Scenario 1: Multi-Specialty Coordinated Single-Visit (RFP 5.2.2)."""
        self._reset_esr_requirements()

        res = find_coordinated_scheduling_matches(self.db, self.state, self.esr_id)
        self.assertEqual(res["totalPendingCount"], 3)
        self.assertTrue(len(res["packages"]) > 0)

        best_pkg = res["packages"][0]
        self.assertEqual(best_pkg["coverageType"], "full")
        self.assertIn("Full Coordinated Visit", best_pkg["badge"])
        self.assertIn("36C10X27R0001 Section 5.2.2", best_pkg["contractualNotice"])
        self.assertEqual(len(best_pkg["items"]), 3)

        # Verify items are in non-overlapping chronological sequence
        items = best_pkg["items"]
        for i in range(len(items) - 1):
            self.assertTrue(
                items[i]["endsAt"] <= items[i + 1]["startsAt"],
                f"Item {i} ({items[i]['endsAt']}) overlaps item {i+1} ({items[i+1]['startsAt']})"
            )

        # Test atomic hold of the coordinated package
        hold_payload = [
            {
                "requirementId": it["requirementId"],
                "examinerId": it["examinerId"],
                "slot": {
                    "slotId": it["slotId"],
                    "startsAt": it["startsAt"],
                    "endsAt": it["endsAt"],
                    "facilityId": best_pkg["facilityId"],
                    "modality": it["modality"],
                },
            }
            for it in items
        ]
        outcome = apply_scheduling_command(
            self.db, self.state, "holdCoordinatedAppointments", [hold_payload], "scheduler-test"
        )
        entity_ids = outcome["value"]["entityIds"]
        self.assertEqual(len(entity_ids), 3)

        # Verify all 3 slots locked to 'held' in database
        for it in items:
            sl = self.db.get(Stage3TimeSlot, it["slotId"])
            self.assertEqual(sl.slot_status, "held")

        # Verify all 3 requirements updated to 'appointment-held'
        for it in items:
            req = self.state["examinationRequirementsById"][it["requirementId"]]
            self.assertEqual(req["status"], "appointment-held")

    def test_scenario_2_veteran_declines_hold_with_reason(self):
        """Scenario 2: Veteran declines one exam in the package; cancellation releases slot and audits reason."""
        self._reset_esr_requirements()

        # Hold a coordinated package first
        res = find_coordinated_scheduling_matches(self.db, self.state, self.esr_id)
        pkg = res["packages"][0]
        hold_payload = [
            {
                "requirementId": it["requirementId"],
                "examinerId": it["examinerId"],
                "slot": {
                    "slotId": it["slotId"],
                    "startsAt": it["startsAt"],
                    "endsAt": it["endsAt"],
                    "facilityId": pkg["facilityId"],
                    "modality": it["modality"],
                },
            }
            for it in pkg["items"]
        ]
        outcome = apply_scheduling_command(
            self.db, self.state, "holdCoordinatedAppointments", [hold_payload], "scheduler-test"
        )
        held_appt_id = outcome["value"]["entityIds"][0]
        released_slot_id = pkg["items"][0]["slotId"]
        released_req_id = pkg["items"][0]["requirementId"]

        # Empty reason is rejected with 422
        with self.assertRaises(HTTPException) as ctx:
            apply_scheduling_command(
                self.db, self.state, "cancelAppointment", [held_appt_id, "   "], "scheduler-test"
            )
        self.assertEqual(ctx.exception.status_code, 422)

        # Cancel with valid Veteran reason
        cancel_reason = "Veteran declined same-day third exam due to fatigue; requested reschedule next week."
        cancel_out = apply_scheduling_command(
            self.db, self.state, "cancelAppointment", [held_appt_id, cancel_reason], "scheduler-test"
        )
        self.assertEqual(cancel_out["value"]["cancellationReason"], cancel_reason)

        # Slot must be back to 'available'
        sl = self.db.get(Stage3TimeSlot, released_slot_id)
        self.assertEqual(sl.slot_status, "available")

        # Requirement must be reset to 'approved'
        req = self.state["examinationRequirementsById"][released_req_id]
        self.assertEqual(req["status"], "approved")

        # Audit event must be written
        self.db.flush()
        audit_events = self.db.execute(
            select(AuditEvent).where(AuditEvent.object_ref == held_appt_id)
        ).scalars().all()
        self.assertTrue(any("fatigue" in ev.detail for ev in audit_events))

    def test_scenario_3_outreach_limits_and_stage4_confirmation(self):
        """Scenario 3: 2-attempt outreach limit enforcement and final confirmation to Stage 4."""
        self._reset_esr_requirements()

        # Hold single requirement
        req = next(r for r in self.state["examinationRequirementsById"].values() if r["esrId"] == self.esr_id)
        matches = find_scheduling_matches(self.db, self.state, [req["id"]])
        doc = matches["validDoctors"][0]
        slot = doc["slots"][0]

        hold_out = apply_scheduling_command(
            self.db, self.state, "holdAppointment",
            [req["id"], doc["examinerId"], slot], "scheduler-test"
        )
        appt_id = hold_out["value"]["entityId"]

        # Attempt confirmation without outreach -> fails 422
        with self.assertRaises(HTTPException) as ctx:
            apply_scheduling_command(self.db, self.state, "confirmAppointment", [appt_id], "scheduler-test")
        self.assertEqual(ctx.exception.status_code, 422)

        # Outreach attempt 1: Left voicemail
        apply_scheduling_command(
            self.db, self.state, "recordOutreach",
            [self.esr_id, appt_id, "phone", "left-voicemail", "Left voicemail regarding examination date"],
            "scheduler-test"
        )

        # Still cannot confirm after only a voicemail
        with self.assertRaises(HTTPException) as ctx:
            apply_scheduling_command(self.db, self.state, "confirmAppointment", [appt_id], "scheduler-test")
        self.assertEqual(ctx.exception.status_code, 422)

        # Outreach attempt 2: Veteran confirmed
        apply_scheduling_command(
            self.db, self.state, "recordOutreach",
            [self.esr_id, appt_id, "phone", "answered", "Veteran confirmed availability"],
            "scheduler-test"
        )

        # Attempt 3: Exceeds 2-attempt limit / already confirmed -> raises 422
        with self.assertRaises(HTTPException) as ctx:
            apply_scheduling_command(
                self.db, self.state, "recordOutreach",
                [self.esr_id, appt_id, "phone", "answered", "Redundant attempt"],
                "scheduler-test"
            )
        self.assertEqual(ctx.exception.status_code, 422)

        # Confirm appointment -> locks slot to 'booked' and releases to Stage 4
        conf_out = apply_scheduling_command(
            self.db, self.state, "confirmAppointment", [appt_id], "scheduler-test"
        )
        appt = self.state["appointmentsById"][appt_id]
        self.assertEqual(appt["status"], "confirmed-locally")

        sl = self.db.get(Stage3TimeSlot, slot["slotId"])
        self.assertEqual(sl.slot_status, "booked")

        # Stage 4 examination entity generated
        exams = [e for e in self.state.get("examinationsById", {}).values() if e.get("appointmentId") == appt_id]
        self.assertEqual(len(exams), 1)

    def test_scenario_4_travel_distance_limit_and_consent(self):
        """Scenario 4: Strict travel limit enforcement; over-limit facility requires express Veteran consent."""
        self._reset_esr_requirements()

        esr = self.state["esrsById"][self.esr_id]
        veteran = self.state["veteransById"][esr["veteranId"]]
        # Set a tight 10-mile travel limit on ESR
        esr["travelLimitMiles"] = 10

        req = next(r for r in self.state["examinationRequirementsById"].values() if r["esrId"] == self.esr_id)
        matches = find_scheduling_matches(self.db, self.state, [req["id"]])
        
        # Pick a facility that is beyond 10 miles
        far_fac = next((f for f in matches["facilities"] if f["distanceMiles"] and f["distanceMiles"] > 10), None)
        if far_fac:
            self.assertFalse(far_fac["withinTravelLimit"])
            far_matches = find_scheduling_matches(self.db, self.state, [req["id"]], facility_id=far_fac["facilityId"])
            if far_matches["validDoctors"]:
                doc = far_matches["validDoctors"][0]
                slot = doc["slots"][0]
                hold_out = apply_scheduling_command(
                    self.db, self.state, "holdAppointment",
                    [req["id"], doc["examinerId"], slot], "scheduler-test"
                )
                appt_id = hold_out["value"]["entityId"]

                # Log confirmation outreach
                apply_scheduling_command(
                    self.db, self.state, "recordOutreach",
                    [self.esr_id, appt_id, "phone", "answered", "Veteran answered"], "scheduler-test"
                )

                # Attempting to confirm without travel consent fails 422
                with self.assertRaises(HTTPException) as ctx:
                    apply_scheduling_command(self.db, self.state, "confirmAppointment", [appt_id], "scheduler-test")
                self.assertEqual(ctx.exception.status_code, 422)
                self.assertIn("exceeds 10 mi limit", ctx.exception.detail)

                # Record express consent in logistics
                apply_scheduling_command(
                    self.db, self.state, "recordAppointmentLogistics",
                    [appt_id, {"consentStatus": "approved", "notes": "Veteran consented to drive 15 miles"}],
                    "scheduler-test"
                )

                # Now confirmation succeeds
                conf_out = apply_scheduling_command(
                    self.db, self.state, "confirmAppointment", [appt_id], "scheduler-test"
                )
                self.assertEqual(self.state["appointmentsById"][appt_id]["status"], "confirmed-locally")

    def test_scenario_5_unverified_location_protection(self):
        """Scenario 5: Unverified location blocks confirmation until enriched with valid ZIP."""
        self._reset_esr_requirements()

        esr = self.state["esrsById"][self.esr_id]
        veteran = self.state["veteransById"][esr["veteranId"]]
        orig_zip = veteran.get("postalCode", "")

        # Temporarily clear ZIP to simulate unverified location
        veteran["postalCode"] = ""
        loc = resolve_veteran_location(veteran, esr)
        self.assertEqual(loc["precision"], "unknown")
        self.assertTrue(loc["verificationRequired"])

        # Enforce valid 5-digit ZIP verification via verifyVeteranLocation (empty/whitespace raises 422)
        with self.assertRaises(HTTPException) as ctx:
            apply_scheduling_command(
                self.db, self.state, "verifyVeteranLocation",
                [self.esr_id, {"postalCode": "   ", "city": ""}], "scheduler-test"
            )
        self.assertEqual(ctx.exception.status_code, 422)

        # Supply valid ZIP code
        apply_scheduling_command(
            self.db, self.state, "verifyVeteranLocation",
            [self.esr_id, {"city": "Columbus", "stateCode": "OH", "postalCode": "43215"}], "scheduler-test"
        )
        self.assertEqual(veteran["postalCode"], "43215")
        self.assertEqual(veteran["stateCode"], "OH")

        # Location is now resolved cleanly
        loc_after = resolve_veteran_location(veteran, esr)
        self.assertNotEqual(loc_after["precision"], "unknown")

        # Restore original ZIP
        veteran["postalCode"] = orig_zip

    def test_scenario_6_same_specialty_appointment_bundling(self):
        """Scenario 6: Same-specialty DBQ bundling into single appointment (GAO-23-105787 Tier 1)."""
        # Create 2 mock requirements sharing the exact same specialty (Orthopedics)
        req_a_id = f"req-mock-{uuid4().hex[:8]}"
        req_b_id = f"req-mock-{uuid4().hex[:8]}"
        now = "2026-11-26T12:00:00Z"

        self.state["examinationRequirementsById"][req_a_id] = {
            "id": req_a_id, "esrId": self.esr_id, "examinationType": "Left Knee Condition",
            "requiredSpecialties": ["Orthopedics / Musculoskeletal"], "requiredCredentials": ["MD"],
            "requestedDbqTypes": ["Knee and Lower Leg DBQ"], "selectedModality": "in-person", "status": "approved",
        }
        self.state["examinationRequirementsById"][req_b_id] = {
            "id": req_b_id, "esrId": self.esr_id, "examinationType": "Lumbar Spine Condition",
            "requiredSpecialties": ["Orthopedics / Musculoskeletal"], "requiredCredentials": ["MD"],
            "requestedDbqTypes": ["Back (Thoracolumbar Spine) DBQ"], "selectedModality": "in-person", "status": "approved",
        }

        # Multi-requirement match for both compatible requirements simultaneously
        matches = find_scheduling_matches(self.db, self.state, [req_a_id, req_b_id])
        self.assertTrue(len(matches["validDoctors"]) > 0)
        doc = matches["validDoctors"][0]
        slot = doc["slots"][0]

        # Hold single joint appointment for both DBQs
        hold_out = apply_scheduling_command(
            self.db, self.state, "holdAppointment",
            [[req_a_id, req_b_id], doc["examinerId"], slot], "scheduler-test"
        )
        appt_id = hold_out["value"]["entityId"]
        appt = self.state["appointmentsById"][appt_id]
        self.assertEqual(appt["requirementIds"], [req_a_id, req_b_id])
        self.assertEqual(self.state["examinationRequirementsById"][req_a_id]["appointmentId"], appt_id)
        self.assertEqual(self.state["examinationRequirementsById"][req_b_id]["appointmentId"], appt_id)

    def test_scenario_7_graceful_fallback_when_same_day_impracticable(self):
        """Scenario 7: Fallback to independent scheduling when specialists have no common slots."""
        # Query coordinated matches for an ESR where all requirements are already scheduled or impossible
        fake_esr_id = f"esr-fake-{uuid4().hex[:8]}"
        self.state["esrsById"][fake_esr_id] = {
            "id": fake_esr_id, "veteranId": "vet-1", "priority": "routine", "dueAt": "2026-12-31"
        }
        # Add 2 requirements with mutually exclusive specialties and modalities
        rid1 = f"req-fake-{uuid4().hex[:8]}"
        rid2 = f"req-fake-{uuid4().hex[:8]}"
        self.state["examinationRequirementsById"][rid1] = {
            "id": rid1, "esrId": fake_esr_id, "examinationType": "Exotic Specialist A",
            "requiredSpecialties": ["Neurosurgery / Brain"], "requiredCredentials": ["MD"],
            "requestedDbqTypes": [], "selectedModality": "in-person", "status": "approved",
        }
        self.state["examinationRequirementsById"][rid2] = {
            "id": rid2, "esrId": fake_esr_id, "examinationType": "Exotic Specialist B",
            "requiredSpecialties": ["Pediatric Ophthalmology"], "requiredCredentials": ["MD"],
            "requestedDbqTypes": [], "selectedModality": "in-person", "status": "approved",
        }

        res = find_coordinated_scheduling_matches(self.db, self.state, fake_esr_id, [rid1, rid2])
        self.assertTrue(len(res["packages"]) > 0)
        fallback = res["packages"][0]
        self.assertEqual(fallback["coverageType"], "separate")
        self.assertIn("Separate Visits Required", fallback["badge"])
        self.assertIn("permitted exception", fallback["contractualNotice"])


if __name__ == "__main__":
    unittest.main()
