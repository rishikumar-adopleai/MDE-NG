import json
import unittest
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest.mock import patch

from fastapi import HTTPException
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from app.core.config import Settings
from app.import_references import import_library
from app.models import ReferenceFile
from app.services.reference_store import EXCHANGE_ROOT, EXCHANGE_SCHEMA, FORM_INDEX, FORM_ROOT, reference_bytes, reference_paths
from app.services.va_dbq_catalog import catalog, reference_forms


class ReferenceStoreTests(unittest.TestCase):
    def setUp(self):
        self.engine = create_engine("sqlite://")
        self.addCleanup(self.engine.dispose)
        self.enterContext(patch("app.services.reference_store.SessionLocal", sessionmaker(self.engine)))
        for cached in (reference_paths, catalog, reference_forms):
            cached.cache_clear()
            self.addCleanup(cached.cache_clear)

    def test_import_survives_removal_of_originals(self):
        request = EXCHANGE_ROOT / "case/1_ExamSchedulingRequestCreatedEvent.xml"
        event = request.with_name("2_FollowUp.xml")
        form = {"formName": "Test form", "documentId": 1, "revisionId": 2,
                "documentStructureJson": "forms/test.json", "conditionalLogic": "rules/test.json"}
        files = {
            EXCHANGE_SCHEMA: b'<schema targetNamespace="urn:test"/>',
            FORM_INDEX: json.dumps({"documents": [form]}).encode(),
            FORM_ROOT / form["documentStructureJson"]: b'{"form":"Test form"}',
            FORM_ROOT / form["conditionalLogic"]: b'{"defaultFieldState":["test"]}',
            request: b"<request/>\r\n", event: b"<event/>\r\n",
        }
        with TemporaryDirectory() as directory:
            source = Path(directory)
            for key, content in files.items():
                path = source / key
                path.parent.mkdir(parents=True, exist_ok=True)
                path.write_bytes(content)
            (source / "unused.pdf").write_bytes(b"unrelated")
            self.assertEqual(import_library(self.engine, source), len(files))
            self.assertEqual(import_library(self.engine, source), len(files))
            (source / event).write_bytes(b"<changed/>")
            with self.assertRaisesRegex(ValueError, "different reference library"):
                import_library(self.engine, source)
            form["conditionalLogic"] = "../../outside.json"
            (source / FORM_INDEX).write_text(json.dumps({"documents": [form]}))
            with self.assertRaisesRegex(ValueError, "Invalid catalog reference"):
                import_library(self.engine, source)
        self.assertEqual({key: reference_bytes(key) for key in reference_paths()}, files)
        self.assertEqual(reference_forms()[0]["fieldCount"], 1)

    def test_missing_library_is_unavailable(self):
        ReferenceFile.__table__.create(self.engine)
        for lookup in (reference_paths, lambda: reference_bytes(EXCHANGE_SCHEMA)):
            with self.assertRaises(HTTPException) as error:
                lookup()
            self.assertEqual(error.exception.status_code, 503)

    def test_development_rejects_external_database(self):
        with self.assertRaisesRegex(ValueError, "requires a SQLite"):
            Settings(_env_file=None, app_env="dev", database_url="postgresql://localhost/example")
