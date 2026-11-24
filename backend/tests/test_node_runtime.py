import unittest
from unittest.mock import patch

from fastapi import HTTPException

from app.services.node_runtime import resolve_node_binary


class NodeRuntimeTests(unittest.TestCase):
    def setUp(self):
        resolve_node_binary.cache_clear()
        self.addCleanup(resolve_node_binary.cache_clear)

    @patch("app.services.node_runtime.supports_workflow", side_effect=lambda path: path == "/modern/bin/node")
    @patch("app.services.node_runtime.shutil.which", side_effect=lambda command, path: f"{path}/{command}")
    @patch("app.services.node_runtime.os.get_exec_path", return_value=["/legacy/bin", "/modern/bin"])
    def test_discovery_skips_an_older_node_on_path(self, _path, _which, _version):
        self.assertEqual(resolve_node_binary("", development=False), "/modern/bin/node")

    @patch("app.services.node_runtime.supports_workflow", return_value=True)
    @patch("app.services.node_runtime.shutil.which", side_effect=lambda command, **_: command if command.startswith("/") else None)
    @patch("app.services.node_runtime.development_candidates", return_value=["/local-install/bin/node"])
    @patch("app.services.node_runtime.os.get_exec_path", return_value=[])
    def test_local_install_fallback_is_development_only(self, _path, _locations, _which, _version):
        self.assertEqual(resolve_node_binary("", development=True), "/local-install/bin/node")
        with self.assertRaisesRegex(HTTPException, "Install Node.js 24"):
            resolve_node_binary("", development=False)

    @patch("app.services.node_runtime.shutil.which", return_value=None)
    @patch("app.services.node_runtime.development_candidates")
    def test_invalid_explicit_override_reports_error(self, locations, _which):
        with self.assertRaisesRegex(HTTPException, "NODE_BINARY must point"):
            resolve_node_binary("/missing/node", development=True)
        locations.assert_not_called()
