"""Checks at the private OpenCode launch boundary."""

import tempfile
import unittest
import os
import sys
from pathlib import Path
from unittest.mock import patch

from planner.connector import Connector, ConnectorConfig


class ConnectorTests(unittest.TestCase):
    def executable(self, directory, version):
        executable = Path(directory) / "opencode"
        executable.write_text(f"#!/bin/sh\nprintf '%s\\n' '{version}'\n")
        executable.chmod(0o700)
        return str(executable)

    def test_missing_binary_blocks_execution(self):
        with tempfile.TemporaryDirectory() as directory:
            connector = Connector(ConnectorConfig(
                executable=str(Path(directory) / "missing"), model="test/model"))
            status = connector.check()
        self.assertFalse(status.available)
        self.assertIn("unavailable", status.reason)

    @unittest.skipIf(os.name == "nt", "POSIX executable fixture")
    def test_different_release_cannot_claim_connector_compatibility(self):
        with tempfile.TemporaryDirectory() as directory:
            executable = self.executable(directory, "opencode v2.2.6")
            status = Connector(ConnectorConfig(model="test/model", executable=executable)).check()
        self.assertFalse(status.available)
        self.assertIn("2.2.5", status.reason)
        self.assertIn("incompatible", status.reason)

    @unittest.skipIf(os.name == "nt", "POSIX executable fixture")
    def test_matching_release_does_not_bypass_missing_mediation(self):
        with tempfile.TemporaryDirectory() as directory:
            executable = self.executable(directory, "opencode v2.2.5")
            status = Connector(ConnectorConfig(model="test/model", executable=executable)).check()
        self.assertFalse(status.available)
        self.assertFalse(status.verified)
        self.assertIn("provenance", status.reason)

    def test_launch_proposal_is_private_and_does_not_inherit_credentials(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            with patch.dict(os.environ, {"UNRELATED_API_KEY": "dummy-secret", "OPENCODE_CONFIG": "/ambient/config"}):
                launch = Connector(ConnectorConfig(model="laas/test-model", executable=sys.executable)).prepare(root, "Review the project")
            self.assertIn("--standalone", launch.argv)
            self.assertTrue(Path(launch.argv[0]).is_absolute())
            self.assertIn("laas/test-model", launch.argv)
            self.assertNotIn("UNRELATED_API_KEY", launch.env)
            self.assertNotIn("OPENCODE_CONFIG", launch.env)
            self.assertEqual(launch.env["OPENCODE_DB"], ":memory:")
            self.assertEqual(launch.env["OPENCODE_CONFIG_PROJECT_DISABLE"], "1")
            self.assertTrue(Path(launch.env["HOME"]).is_relative_to(root))

    def test_invalid_configuration_never_prepares_a_launch(self):
        for values in ({"model": ""}, {"model": "test"}, {"model": "test/model", "timeout_seconds": 0}):
            with self.subTest(values=values), self.assertRaises(ValueError):
                ConnectorConfig(**values)


if __name__ == "__main__":
    unittest.main()
