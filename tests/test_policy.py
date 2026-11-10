import os
from pathlib import Path
import tempfile
import unittest

from planner.policy import Policy
from planner.records import Action, Grant


class PolicyTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        (self.root / "src").mkdir()
        (self.root / "tests").mkdir()
        self.policy = Policy(self.root, read=("**",), write=("src/**",),
                             deny_read=(".env*", "**/.env*"), deny_write=("tests/**",))
        self.grant = Grant("task", "worker", ("**",), ("src/**",))

    def decision(self, operation, resource):
        return self.policy.decide(Action("task", "worker", operation, resource), self.grant)

    def test_source_edit_policy_distinguishes_reads_writes_and_denials(self):
        for operation, resource, allowed in [
            ("read", "README.md", True), ("read", ".env", False),
            ("read", "config/.env.production", False), ("write", "src/refunds.py", True),
            ("write", "tests/test_refunds.py", False), ("write", "README.md", False),
        ]:
            with self.subTest(operation=operation, resource=resource):
                self.assertEqual(self.decision(operation, resource).allowed, allowed)

    def test_symlink_alias_and_traversal_do_not_escape(self):
        (self.root / ".env").write_text("DUMMY_SECRET")
        (self.root / "src" / "alias").symlink_to(self.root / ".env")
        (self.root / "src" / "outside").symlink_to(self.root.parent)
        for resource in ("src/alias", "src/outside/file", "../outside"):
            with self.subTest(resource=resource):
                self.assertFalse(self.decision("read", resource).allowed)

    def test_new_sensitive_files_are_denied_on_each_decision(self):
        (self.root / "src" / ".env.new").write_text("DUMMY_SECRET")
        self.assertFalse(self.decision("read", "src/.env.new").allowed)

    def test_hard_link_alias_requires_review(self):
        (self.root / ".env").write_text("DUMMY_SECRET")
        os.link(self.root / ".env", self.root / "src" / "alias")
        self.assertFalse(self.decision("read", "src/alias").allowed)

    def test_delegation_cannot_increase_parent_scope(self):
        parent = Grant("task", "parent", ("src/**",), ("src/**",))
        child = Grant("task", "child", ("**",), (), parent_id="parent")
        with self.assertRaisesRegex(ValueError, "increase authority"):
            self.policy.validate_grant(child, parent)

    def test_policy_loader_rejects_typographical_and_malformed_settings(self):
        path = self.root / "policy.toml"
        for content in ('[files]\nreed=["**"]', '[files]\nread="**"', '[files]\nread=["../**"]'):
            path.write_text(content)
            with self.subTest(content=content), self.assertRaises(ValueError):
                Policy.load(self.root, path)

    def test_case_alias_cannot_read_denied_resource_on_case_insensitive_filesystem(self):
        secret = self.root / ".env"
        secret.write_text("DUMMY_SECRET")
        alias = self.root / ".ENV"
        if not alias.exists():
            self.skipTest("Case-sensitive filesystem")
        self.assertFalse(self.decision("read", ".ENV").allowed)


if __name__ == "__main__":
    unittest.main()
