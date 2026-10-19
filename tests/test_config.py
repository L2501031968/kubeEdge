import json
import tempfile
import unittest
from pathlib import Path

from kb.config import Config


class ConfigLoadTest(unittest.TestCase):
    def _load(self, payload):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "kb.json"
            path.write_text(json.dumps(payload), encoding="utf-8")
            return Config.load(str(path))

    def test_host_console_and_agents(self):
        cfg = self._load({
            "role": "edge",
            "host_console": "http://192.168.1.10:8080",
            "agents": [{"name": "sdr", "url": "http://192.168.1.51:8081"}],
        })
        self.assertEqual(cfg.role, "edge")
        self.assertEqual(cfg.host_console, "http://192.168.1.10:8080")
        self.assertEqual([item.name for item in cfg.agents], ["sdr"])

    def test_optional_fields_default_to_empty(self):
        cfg = self._load({"role": "host"})
        self.assertIsNone(cfg.host_console)
        self.assertIsNone(cfg.prepare_script)
        self.assertEqual(cfg.agents, [])

    def test_prepare_script_is_loaded(self):
        cfg = self._load({
            "role": "edge",
            "prepare_script": "/opt/amari-edge/amari-host-prepare.sh",
        })
        self.assertEqual(cfg.prepare_script, "/opt/amari-edge/amari-host-prepare.sh")

    def test_user_source_and_capacity_are_loaded(self):
        cfg = self._load({
            "role": "host",
            "user_source": "/var/lib/kb/users.json",
            "ue_capacity": 128,
        })
        self.assertEqual(cfg.user_source, "/var/lib/kb/users.json")
        self.assertEqual(cfg.ue_capacity, 128)

    def test_user_facet_defaults(self):
        cfg = self._load({"role": "host"})
        self.assertIsNone(cfg.user_source)
        self.assertEqual(cfg.ue_capacity, 64)

    def test_invalid_capacity_keeps_default(self):
        cfg = self._load({"role": "host", "ue_capacity": "many"})
        self.assertEqual(cfg.ue_capacity, 64)

    def test_missing_file_uses_defaults(self):
        cfg = Config.load(str(Path(tempfile.gettempdir()) / "kb-does-not-exist.json"))
        self.assertEqual(cfg.role, "host")
        self.assertIsNone(cfg.host_console)


if __name__ == "__main__":
    unittest.main()
