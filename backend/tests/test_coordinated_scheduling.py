import unittest
from uuid import uuid4

from app.core.database import Base, SessionLocal, engine
from app.models import Stage3Examiner, Stage3Facility, Stage3TimeSlot
from app.services.intake_store import get_state, touch
from app.services.scheduling import (
    apply_scheduling_command,
    ensure_stage3_directory_seeded,
    find_coordinated_scheduling_matches,
)


class CoordinatedSchedulingTests(unittest.TestCase):
    def setUp(self):
        Base.metadata.create_all(engine)
        self.db = SessionLocal()
        ensure_stage3_directory_seeded(self.db)
        self.state = get_state(self.db)

    def tearDown(self):
        self.db.close()

    def test_coordinated_matching_and_atomic_hold(self):
        # Locate an ESR that has examination requirements
        all_reqs = list(self.state.get("examinationRequirementsById", {}).values())
        self.assertTrue(len(all_reqs) > 0)
        esr_id = all_reqs[0]["esrId"]

        # Ensure requirements for this ESR are in 'approved' status with no existing appointment
        for r in all_reqs:
            if r.get("esrId") == esr_id:
                r["status"] = "approved"
                r.pop("appointmentId", None)

        # Clear any conflicting active appointments for this ESR
        self.state["appointmentsById"] = {
            aid: a for aid, a in self.state.get("appointmentsById", {}).items()
            if a.get("esrId") != esr_id
        }

        res = find_coordinated_scheduling_matches(self.db, self.state, esr_id)
        self.assertIn("packages", res)
        self.assertIn("bestPackageId", res)
        self.assertTrue(len(res["packages"]) > 0)

        best_pkg = res["packages"][0]
        self.assertIn(best_pkg["coverageType"], {"full", "partial", "separate"})
        self.assertIn("badge", best_pkg)
        self.assertIn("contractualNotice", best_pkg)

        if best_pkg["items"]:
            # Test holding the coordinated package
            payload = [
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
                for it in best_pkg["items"]
            ]

            # Verify that holding the package executes cleanly
            outcome = apply_scheduling_command(
                self.db, self.state, "holdCoordinatedAppointments", [payload], "test-scheduler"
            )
            self.assertIn("entityIds", outcome["value"])
            self.assertEqual(len(outcome["value"]["entityIds"]), len(payload))

            # Verify each slot is held
            for it in best_pkg["items"]:
                sl = self.db.get(Stage3TimeSlot, it["slotId"])
                self.assertEqual(sl.slot_status, "held")

            # Verify each requirement is updated
            for it in best_pkg["items"]:
                req = self.state["examinationRequirementsById"][it["requirementId"]]
                self.assertEqual(req["status"], "appointment-held")

            # Clean up: cancel one held appointment with reason and verify release to available
            first_appt_id = outcome["value"]["entityIds"][0]
            cancel_outcome = apply_scheduling_command(
                self.db, self.state, "cancelAppointment", [first_appt_id, "Veteran requested alternate date"], "test-scheduler"
            )
            released_slot = self.db.get(Stage3TimeSlot, best_pkg["items"][0]["slotId"])
            self.assertEqual(released_slot.slot_status, "available")


if __name__ == "__main__":
    unittest.main()
