"""Adversarial checks through policy loading, delegation, and decisions."""

import os
import tempfile
import unittest
from pathlib import Path

from planner.policy import Policy
from planner.records import Action, Grant


class PolicyAdversarialTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.base = Path(self.temp.name)
        self.root = self.base / "project"
        self.root.mkdir()
        (self.root / "src").mkdir()
        self.policy = Policy(
            self.root, read=("**",), write=("src/**",),
            deny_read=(".env*", "**/.env*"), deny_write=("src/protected",),
        )
        self.grant = Grant("task", "worker", read=("**",), write=("src/**",))

    def decide(self, resource, operation="read", **fields):
        return self.policy.decide(Action("task", "worker", operation, resource, **fields), self.grant)

    def test_symlink_followed_by_parent_traversal_uses_actual_filesystem(self):
        outside = self.base / "outside"
        (outside / "deep").mkdir(parents=True)
        secret = outside / "secret.txt"
        secret.write_text("DUMMY_OUTSIDE_SECRET")
        (self.root / "src" / "jump").symlink_to(outside / "deep", target_is_directory=True)
        (self.root / "src" / "secret.txt").write_text("PERMITTED_DECOY")
        resource = "src/jump/../secret.txt"
        self.assertEqual((self.root / resource).resolve(), secret.resolve())
        self.assertEqual((self.root / resource).read_text(), "DUMMY_OUTSIDE_SECRET")
        self.assertFalse(self.decide(resource).allowed)

    def test_unknown_tools_and_operations_are_denied(self):
        for fields in ({"operation": "delete"}, {"tool": "unregistered"}):
            with self.subTest(fields=fields):
                self.assertFalse(self.decide("src/file.py", **fields).allowed)

    def test_explicit_denial_covers_descendants_and_new_files(self):
        protected = self.root / "src" / "protected"
        protected.mkdir()
        self.assertTrue(self.decide("src/ordinary.py", "write").allowed)
        self.assertFalse(self.decide("src/protected/new.txt", "write").allowed)
        (protected / "new.txt").write_text("existing")
        self.assertFalse(self.decide("src/protected/new.txt", "write").allowed)

    def test_grant_prefix_and_traversal_cannot_escape_parent_scope(self):
        parent = Grant("task", "parent", read=("src/**",))
        for scope in ("src-other/**", "src/../tests/**", "src//**", "src/./**"):
            child = Grant("task", "child", read=(scope,), parent_id="parent")
            with self.subTest(scope=scope), self.assertRaises(ValueError):
                self.policy.validate_grant(child, parent)

    def test_exact_child_cannot_gain_access_through_symlink_alias(self):
        (self.root / "docs").mkdir()
        (self.root / "docs" / "private.txt").write_text("DUMMY_PRIVATE")
        (self.root / "src" / "alias.txt").symlink_to(self.root / "docs" / "private.txt")
        parent = Grant("task", "parent", read=("src/**",))
        child = Grant("task", "child", read=("src/alias.txt",), parent_id="parent")
        self.policy.validate_grant(child, parent)
        decision = self.policy.decide(Action("task", "child", "read", "src/alias.txt"), child)
        self.assertFalse(decision.allowed)

    def test_case_alias_cannot_bypass_denial_on_case_insensitive_filesystem(self):
        sensitive = self.root / ".env.local"
        sensitive.write_text("DUMMY_SECRET")
        alias = self.root / ".ENV.LOCAL"
        if not alias.exists():
            self.skipTest("Filesystem treats case variants as different resources")
        decision = self.decide(".ENV.LOCAL")
        self.assertFalse(decision.allowed)
        self.assertTrue(decision.sensitive)

    def test_malformed_section_types_return_validation_errors(self):
        policy_file = self.base / "policy.toml"
        for section in ("files", "tools", "external"):
            for value in ("1", "true", '"text"', "[]"):
                policy_file.write_text(f"{section} = {value}\n")
                with self.subTest(section=section, value=value), self.assertRaises(ValueError):
                    Policy.load(self.root, policy_file)

    def test_external_input_authorization_is_exact_and_read_only(self):
        external = self.base / "input.txt"
        sibling = self.base / "input.txt.backup"
        external.write_text("EXPLICIT_INPUT")
        sibling.write_text("PRIVATE_SIBLING")
        policy = Policy(self.root, read=("**",), external_inputs=(external,))
        grant = Grant("task", "worker", read=("**",))
        normalized, _ = policy.normalize(str(external))
        self.assertEqual(normalized, external.resolve())
        for resource in (str(sibling), str(self.base), str(external / "child")):
            with self.subTest(resource=resource), self.assertRaises((ValueError, OSError)):
                policy.normalize(resource)
        self.assertFalse(policy.decide(Action("task", "worker", "write", str(external)), grant).allowed)
        self.assertFalse(policy.decide(Action("task", "worker", "read", str(external)), grant).allowed)

    def test_hard_link_from_external_input_does_not_authorize_project_alias(self):
        external = self.base / "secret.txt"
        external.write_text("DUMMY_SECRET")
        alias = self.root / "src" / "allowed-name.txt"
        os.link(external, alias)
        self.assertFalse(self.decide("src/allowed-name.txt").allowed)


if __name__ == "__main__":
    unittest.main()
