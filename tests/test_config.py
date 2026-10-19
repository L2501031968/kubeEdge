import json
import os
import tempfile
import unittest
from pathlib import Path

from kb.__main__ import ensure_local_config
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

    def test_node_arch_overrides_default_to_empty(self):
        cfg = self._load({"role": "host"})
        self.assertEqual(cfg.node_arch_overrides, {})

    def test_node_arch_overrides_are_loaded(self):
        cfg = self._load({
            "role": "host",
            "node_arch_overrides": {"d2000-edge": "arm64"},
        })
        self.assertEqual(cfg.node_arch_overrides, {"d2000-edge": "arm64"})

    def test_empty_node_arch_override_is_dropped(self):
        cfg = self._load({"role": "host", "node_arch_overrides": {"d2000-edge": ""}})
        self.assertEqual(cfg.node_arch_overrides, {})


class EnsureLocalConfigTest(unittest.TestCase):
    """首次启动自动生成 kb.json，省掉手动 copy 一步。"""

    def _in_dir(self, tmp):
        class _Cwd:
            def __enter__(inner):
                inner.previous = os.getcwd()
                os.chdir(tmp)

            def __exit__(inner, *exc):
                os.chdir(inner.previous)
                return False

        return _Cwd()

    def test_missing_file_is_created_from_example(self):
        with tempfile.TemporaryDirectory() as tmp:
            Path(tmp, "kb.json.example").write_text(
                json.dumps({"role": "edge", "ue_capacity": 8}), encoding="utf-8"
            )
            with self._in_dir(tmp):
                created = ensure_local_config(None)
                self.assertIsNotNone(created)
                self.assertEqual(created, Path(tmp) / "kb.json")
                cfg = Config.load(None)
            self.assertEqual(cfg.role, "edge")
            self.assertEqual(cfg.ue_capacity, 8)

    def test_existing_file_is_not_overwritten(self):
        with tempfile.TemporaryDirectory() as tmp:
            Path(tmp, "kb.json").write_text('{"role": "edge"}', encoding="utf-8")
            Path(tmp, "kb.json.example").write_text('{"role": "host"}', encoding="utf-8")
            with self._in_dir(tmp):
                self.assertIsNone(ensure_local_config(None))
                cfg = Config.load(None)
            self.assertEqual(cfg.role, "edge")

    def test_explicit_path_is_left_alone(self):
        with tempfile.TemporaryDirectory() as tmp:
            Path(tmp, "kb.json.example").write_text('{"role": "host"}', encoding="utf-8")
            with self._in_dir(tmp):
                self.assertIsNone(ensure_local_config(str(Path(tmp) / "custom.json")))
                self.assertFalse((Path(tmp) / "kb.json").exists())

    def test_without_example_nothing_is_created(self):
        with tempfile.TemporaryDirectory() as tmp:
            with self._in_dir(tmp):
                self.assertIsNone(ensure_local_config(None))
                self.assertFalse((Path(tmp) / "kb.json").exists())


if __name__ == "__main__":
    unittest.main()
