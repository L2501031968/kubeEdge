import tempfile
import unittest
from pathlib import Path

from kb.config import Config
from kb.runner import CommandRunner
from kb.webui.server import AppState


class HostAppStateTest(unittest.TestCase):
    def setUp(self):
        cfg = Config(
            role="host",
            demo=True,
            audit_log=Path(tempfile.gettempdir()) / "kb-test-audit.jsonl",
        )
        self.state = AppState(cfg, CommandRunner(dry_run=True))

    def test_snapshot_carries_business_domain(self):
        data = self.state.snapshot()
        self.assertEqual(data["kind"], "host")
        domain = data["domain"]
        self.assertIsNotNone(domain)
        self.assertEqual(len(domain["modules"]), 5)
        self.assertTrue(domain["users"]["available"])
        self.assertEqual(domain["users"]["online"], 8)
        labels = [pool["label"] for pool in domain["resources"]["pools"]]
        self.assertIn("GPU", labels)
        self.assertIn("NPU", labels)

    def test_action_catalog_exposes_facet_config(self):
        catalog = self.state.action_catalog()
        self.assertEqual(catalog["role"], "host")
        self.assertIn("user_source", catalog)
        self.assertEqual(catalog["ue_capacity"], 64)
        self.assertEqual(catalog["host_console"], None)

    def test_snapshot_is_cached(self):
        first = self.state.snapshot()
        second = self.state.snapshot()
        self.assertIs(first, second)


if __name__ == "__main__":
    unittest.main()
