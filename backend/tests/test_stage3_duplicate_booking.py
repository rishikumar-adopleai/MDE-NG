"""Requirements keep a single appointment across the supported booking commands."""
import unittest
from unittest.mock import Mock, patch

from fastapi import HTTPException
from app.services.scheduling import apply_scheduling_command, _ensure_requirements_unbooked


class DuplicateBookingTests(unittest.TestCase):
    def setUp(self):
        self.state = {
            "examinationRequirementsById": {"r1": {"id": "r1", "status": "approved"}},
            "appointmentsById": {"a1": {"id": "a1", "status": "held", "requirementIds": ["r1"]}},
        }

    def blocked(self, action, args):
        with patch("app.services.scheduling.ensure_stage3_directory_seeded"):
            with self.assertRaises(HTTPException) as result:
                apply_scheduling_command(Mock(), self.state, action, args, "tester")
        self.assertEqual(result.exception.status_code, 409)

    def test_new_hold_for_held_requirement(self):
        self.blocked("holdAppointment", ["r1", "doctor-2", {}])

    def test_coordinated_hold_for_booked_requirement(self):
        self.blocked("holdCoordinatedAppointments", [[{"requirementId": "r1"}]])

    def test_repeated_requirement_within_package(self):
        self.state["appointmentsById"].clear()
        self.blocked("holdCoordinatedAppointments", [[{"requirementId": "r1"}, {"requirementId": "r1"}]])

    def test_confirmation_rechecks_other_existing_hold(self):
        self.state["appointmentsById"]["a2"] = {"id": "a2", "status": "held", "requirementIds": ["r1"]}
        self.blocked("confirmAppointment", ["a2"])

    def test_legacy_links_through_exam(self):
        self.state["appointmentsById"]["a1"] = {"id": "a1", "status": "completed", "examinationId": "exam1"}
        self.state["examinationsById"] = {"exam1": {"id": "exam1", "requirementIds": ["r1"]}}
        self.blocked("holdAppointment", ["r1", "doctor-2", {}])

    def test_cancelled_and_unrelated_appointments_do_not_block(self):
        _ensure_requirements_unbooked(self.state, ["r2"])
        _ensure_requirements_unbooked(self.state, ["r1"], confirming_id="a1")
        self.state["appointmentsById"]["a1"]["status"] = "cancelled"
        _ensure_requirements_unbooked(self.state, ["r1"])
