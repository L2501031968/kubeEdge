import unittest

from kb.actions import ActionError, ActionRejected, catalog, get_action, guard, run_action
from kb.config import Config
from kb.runner import CommandRunner


class CatalogTest(unittest.TestCase):
    def test_host_catalog_hides_edge_only_actions(self):
        data = catalog(Config(role="host"))
        ids = {item["id"] for item in data["items"]}
        self.assertIn("startup_stack", ids)
        self.assertIn("scale_component", ids)
        self.assertNotIn("ue_power_on", ids)
        self.assertEqual(data["role"], "host")

    def test_edge_catalog_allows_simulated_hardware(self):
        data = catalog(Config(role="edge", sim=True))
        items = {item["id"]: item for item in data["items"]}
        self.assertIn("ue_power_on", items)
        self.assertIn("amari_mode", items)
        self.assertIsNone(items["ue_power_on"]["blocked"])
        self.assertIsNone(items["amari_mode"]["blocked"])

    def test_live_mode_blocks_hardware_actions(self):
        cfg = Config(role="edge", sim=False, allow_real_hardware=False)
        self.assertIsNotNone(guard(cfg, get_action("ue_power_on")))
        self.assertIsNotNone(guard(cfg, get_action("ue_network_mode")))
        self.assertIsNone(guard(cfg, get_action("ue_status")))

    def test_allow_real_hardware_unblocks(self):
        cfg = Config(role="edge", allow_real_hardware=True)
        self.assertIsNone(guard(cfg, get_action("ue_power_on")))

    def test_host_shutdown_needs_allow_real(self):
        self.assertIsNotNone(guard(Config(role="host"), get_action("host_shutdown")))
        cfg = Config(role="host", allow_real_hardware=True)
        self.assertIsNone(guard(cfg, get_action("host_shutdown")))


class RunActionTest(unittest.TestCase):
    def _run(self, action_id, params=None, **cfg_kwargs):
        cfg = Config(role=cfg_kwargs.pop("role", "host"), dry_run=True, **cfg_kwargs)
        runner = CommandRunner(dry_run=True)
        return run_action(cfg, runner, action_id, params)

    def test_scale_uses_kubectl_scale(self):
        result = self._run("scale_component", {"component": "amari-ue", "replicas": 2})
        self.assertTrue(result["ok"])
        argv = result["steps"][0]["argv"]
        self.assertEqual(argv[0], "kubectl")
        self.assertEqual(argv[1], "scale")
        self.assertEqual(argv[2], "deployment/amari-ue")
        self.assertIn("amari-edge", argv)
        self.assertIn("--replicas=2", argv)
        self.assertTrue(result["steps"][0]["stdout"].startswith("[dry-run]"))

    def test_startup_order(self):
        result = self._run("startup_stack")
        scaled = [step["argv"][2] for step in result["steps"] if step["argv"][:2] == ["kubectl", "scale"]]
        self.assertEqual(scaled, [
            "deployment/amari-core",
            "deployment/amari-ims",
            "deployment/amari-ran",
            "deployment/amari-sat",
            "deployment/amari-ue",
        ])

    def test_shutdown_order(self):
        result = self._run("shutdown_stack")
        scaled = [step["argv"][2] for step in result["steps"] if step["argv"][:2] == ["kubectl", "scale"]]
        self.assertEqual(scaled, [
            "deployment/amari-ue",
            "deployment/amari-sat",
            "deployment/amari-ran",
            "deployment/amari-ims",
            "deployment/amari-core",
        ])
        self.assertTrue(all("--replicas=0" in step["argv"] for step in result["steps"] if step["argv"][:2] == ["kubectl", "scale"]))

    def test_replicas_out_of_range(self):
        with self.assertRaises(ActionError):
            self._run("scale_component", {"component": "amari-core", "replicas": 9})

    def test_unknown_component(self):
        with self.assertRaises(ActionError):
            self._run("scale_component", {"component": "amari-nope", "replicas": 1})

    def test_role_mismatch(self):
        cfg = Config(role="edge", sim=True, dry_run=True)
        with self.assertRaises(ActionRejected):
            run_action(cfg, CommandRunner(dry_run=True), "startup_stack")

    def test_edge_mode_offline_uses_no_sudo(self):
        cfg = Config(role="edge", sim=True, dry_run=True)
        runner = CommandRunner(dry_run=True)
        status = run_action(cfg, runner, "ue_status")
        self.assertEqual(status["steps"][0]["argv"], ["amari-ue-status", "--once"])

        mode = run_action(cfg, runner, "amari_mode", {"mode": "edge"})
        self.assertEqual(mode["steps"][0]["argv"], ["amari-mode", "edge"])

    def test_edge_mode_live_uses_sudo_non_interactive(self):
        cfg = Config(role="edge", allow_real_hardware=True, dry_run=True)
        result = run_action(cfg, CommandRunner(dry_run=True), "ue_network_mode", {"mode": "bridge"})
        self.assertEqual(result["steps"][0]["argv"], ["sudo", "-n", "amari-ue-network-mode", "bridge"])

    def test_migrate_sets_node_selector(self):
        result = self._run("migrate_component", {"component": "amari-ran", "node": "sdr"})
        patch_step = result["steps"][0]
        self.assertEqual(patch_step["argv"][:2], ["kubectl", "patch"])
        self.assertIn('"kb.local/name":"sdr"', patch_step["argv"][-1])

    def test_optional_steps_do_not_fail_action(self):
        cfg = Config(role="host", dry_run=True)
        result = run_action(cfg, CommandRunner(dry_run=True), "cluster_overview")
        self.assertTrue(result["ok"])
        self.assertTrue(result["steps"][0]["optional"])


if __name__ == "__main__":
    unittest.main()
