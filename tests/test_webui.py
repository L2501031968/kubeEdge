import json
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from kb.config import AgentTarget, Config
from kb.runner import CommandRunner
from kb.webui.server import AppState, _merge_user_sources


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


class UserSourceTest(unittest.TestCase):
    def _host_state(self, tmp, **overrides):
        options = {
            "role": "host",
            "demo": False,
            "audit_log": Path(tmp) / "audit.jsonl",
        }
        options.update(overrides)
        return AppState(Config(**options), CommandRunner(dry_run=True))

    def test_edge_users_are_unavailable_even_when_sim_ue_is_powered_on(self):
        with tempfile.TemporaryDirectory() as tmp:
            state = AppState(
                Config(
                    role="edge",
                    sim=True,
                    audit_log=Path(tmp) / "audit.jsonl",
                ),
                CommandRunner(dry_run=True),
            )
            state.agent.state_dir = Path(tmp)
            Path(tmp, "ue-power").write_text("on\n", encoding="utf-8")
            self.assertEqual(state.agent.collect()["agent"]["ue_power"], "on")

            payload = state.users_payload()
            self.assertFalse(payload["available"])
            self.assertEqual(payload["users"], [])
            self.assertIn("上电", payload["detail"])

    def test_local_user_source_has_priority_over_agents(self):
        with tempfile.TemporaryDirectory() as tmp:
            source_path = Path(tmp) / "users.json"
            source_path.write_text(
                json.dumps({
                    "source": "local-amf",
                    "users": [{"id": "ue-1", "online": True}],
                }),
                encoding="utf-8",
            )
            state = self._host_state(
                tmp,
                user_source=str(source_path),
                agents=[AgentTarget("edge-a", "http://127.0.0.1:8081")],
            )
            with mock.patch.object(
                state,
                "_fetch_agent_user_source",
                side_effect=AssertionError("agents must not be queried"),
            ) as fetch:
                payload = state.users_payload()

            self.assertTrue(payload["available"])
            self.assertEqual(payload["source"], "local-amf")
            self.assertEqual(payload["users"][0]["id"], "ue-1")
            fetch.assert_not_called()

    def test_merge_user_sources_deduplicates_by_id(self):
        merged = _merge_user_sources([
            {
                "source": "edge-a",
                "generated_at": "2026-10-20T01:00:00Z",
                "users": [
                    {"id": "ue-1", "state": "connected"},
                    {"id": "ue-2", "state": "idle"},
                ],
            },
            {
                "source": "edge-b",
                "generated_at": "2026-10-20T01:00:05Z",
                "users": [
                    {"id": "ue-1", "state": "offline"},
                    {"id": "ue-3", "state": "connected"},
                ],
            },
        ])

        self.assertEqual([item["id"] for item in merged["users"]], ["ue-1", "ue-2", "ue-3"])
        self.assertEqual(merged["users"][0]["state"], "connected")
        self.assertIn("edge-b", merged["users"][2]["note"])

    def test_partial_edge_failure_keeps_other_edge_users(self):
        with tempfile.TemporaryDirectory() as tmp:
            state = self._host_state(
                tmp,
                agents=[
                    AgentTarget("edge-a", "http://edge-a:8081"),
                    AgentTarget("edge-b", "http://edge-b:8081"),
                ],
            )

            def fetch(url):
                if "edge-b" in url:
                    raise RuntimeError("connection refused")
                return {
                    "available": True,
                    "source": "edge-a",
                    "users": [{"id": "ue-1", "online": True}],
                }

            with mock.patch.object(state, "_fetch_agent_user_source", side_effect=fetch):
                payload = state.users_payload()

            self.assertTrue(payload["available"])
            self.assertEqual(len(payload["users"]), 1)
            self.assertEqual(len(payload["source_errors"]), 1)
            self.assertIn("edge-b", payload["source_errors"][0])

    def test_forwarded_action_invalidates_snapshot_cache(self):
        with tempfile.TemporaryDirectory() as tmp:
            state = self._host_state(
                tmp,
                demo=True,
                agents=[AgentTarget("edge-a", "http://127.0.0.1:8081")],
            )
            state.snapshot()
            self.assertIsNotNone(state._cache)

            with mock.patch("kb.webui.server._forward", return_value={"ok": True}):
                state.dispatch("ue_power_on", {}, "edge-a")

            self.assertIsNone(state._cache)


if __name__ == "__main__":
    unittest.main()
