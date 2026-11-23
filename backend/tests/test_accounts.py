import secrets
import sqlite3
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch
from uuid import uuid4

from fastapi.testclient import TestClient

from app.core.config import settings
from app.main import app
from app.services.accounts import SESSION_COOKIE, initialize_accounts
from app.services.stage_ai import STAGE_TASKS


class AccountTests(unittest.TestCase):
    def setUp(self):
        self.database = Path(self.enterContext(tempfile.TemporaryDirectory())) / "accounts.sqlite3"
        self.enterContext(patch.object(settings, "auth_database_path", self.database))
        self.enterContext(patch.object(settings, "app_env", "dev"))
        initialize_accounts()
        self.client = TestClient(app)
        self.addCleanup(self.client.close)
        self.credentials = {"email": f"{secrets.token_hex(8)}@example.test", "password": secrets.token_urlsafe(24)}

    def test_sqlite_account_and_session_lifecycle(self):
        self.assertEqual(self.client.get("/api/workspace").status_code, 401)
        signup = self.client.post("/api/auth/signup", json={**self.credentials, "role": "admin"})
        self.assertEqual(signup.status_code, 201)
        self.assertEqual(signup.json(), {"ok": True})
        self.assertIn("HttpOnly", signup.headers["set-cookie"])
        self.assertIn("SameSite=strict", signup.headers["set-cookie"])
        token = self.client.cookies[SESSION_COOKIE]
        with sqlite3.connect(self.database) as db:
            account = db.execute("SELECT email, password_hash, role FROM accounts").fetchone()
            self.assertEqual((account[0], account[2]), (self.credentials["email"], "intake"))
            self.assertNotEqual(account[1], self.credentials["password"].encode())
            self.assertNotEqual(db.execute("SELECT token_hash FROM sessions").fetchone()[0], token)

        duplicate = self.client.post("/api/auth/signup", json={**self.credentials, "email": self.credentials["email"].upper()})
        self.assertEqual(duplicate.status_code, 409)
        self.assertEqual(self.client.post("/api/auth/logout").status_code, 200)
        self.assertNotIn(SESSION_COOKIE, self.client.cookies)
        self.client.cookies.set(SESSION_COOKIE, token)
        self.assertEqual(self.client.get("/api/workspace").status_code, 401)
        self.assertEqual(self.client.post("/api/auth/login", json={**self.credentials, "password": secrets.token_urlsafe(24)}).status_code, 401)
        self.assertEqual(self.client.post("/api/auth/login", json=self.credentials).status_code, 200)

    def test_local_auth_rejects_cross_site_and_hides_validation_input(self):
        response = self.client.post("/api/auth/signup", json=self.credentials, headers={"Origin": "https://example.test"})
        self.assertEqual(response.status_code, 403)
        invalid = secrets.token_urlsafe(130)
        response = self.client.post("/api/auth/login", json={**self.credentials, "password": invalid})
        self.assertEqual(response.status_code, 422)
        self.assertNotIn(invalid, response.text)
        self.assertNotIn(self.credentials["email"], response.text)

    def test_production_rejects_local_signups(self):
        with patch.object(settings, "app_env", "prod"):
            self.assertEqual(self.client.post("/api/auth/signup", json=self.credentials).status_code, 404)

    def test_same_origin_browser_requests_work_through_frontend_proxy(self):
        headers = {"Origin": "http://127.0.0.1:5173", "Sec-Fetch-Site": "same-origin"}
        self.assertEqual(self.client.post("/api/auth/signup", json=self.credentials, headers=headers).status_code, 201)

    def test_intake_account_can_request_ai_for_all_stages(self):
        url = "/api/workspace/ai-review"
        self.assertEqual(self.client.post(url, json={"stage": "Intake", "entityId": "example"}).status_code, 401)
        self.assertEqual(self.client.post("/api/auth/signup", json=self.credentials).status_code, 201)

        def review(_state, stage, entity):
            return {"overview": {"text": f"{stage} review for {entity}", "source_ids": ["S1"]},
                    "review_points": [], "sources": [{"id": "S1", "excerpt": "Synthetic evidence"}]}

        with (
            patch.object(settings, "intake_provider", "azure-openai"),
            patch("app.api.workspace.get_state"),
            patch("app.api.workspace.generate_stage_review", side_effect=review),
        ):
            for stage in STAGE_TASKS:
                with self.subTest(stage=stage):
                    response = self.client.post(url, json={"stage": stage, "entityId": "example"})
                    self.assertEqual(response.status_code, 200)
                    self.assertIn(stage, response.json()["overview"]["text"])
                    source = response.json()["sources"][0]
                    self.assertEqual(source["excerpt"], "Synthetic evidence")
                    self.assertNotIn("document", source)

        command = self.client.post("/api/workspace/commands",
                                   json={"action": "approveQAReview", "args": [], "revision": 0},
                                   headers={"Idempotency-Key": str(uuid4())})
        self.assertEqual(command.status_code, 403)

    def test_original_document_route_is_unavailable(self):
        self.assertEqual(self.client.post("/api/auth/signup", json=self.credentials).status_code, 201)
        paths = self.client.get("/openapi.json").json()["paths"]
        self.assertNotIn("/api/intake/esrs/{esr_id}/documents/{document_id}/source", paths)
        response = self.client.get("/api/intake/esrs/example/documents/example/source")
        self.assertEqual(response.status_code, 404)
        self.assertNotIn("Content-Disposition", response.headers)
