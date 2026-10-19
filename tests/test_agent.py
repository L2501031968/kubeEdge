import tempfile
import unittest
from pathlib import Path

from kb.agent import EdgeAgent, service_check_status
from kb.runner import CommandResult, CommandRunner


class SimAgentTest(unittest.TestCase):
    def test_default_state(self):
        with tempfile.TemporaryDirectory() as tmp:
            agent = EdgeAgent(CommandRunner(dry_run=True), sim=True, state_dir=tmp)
            data = agent.collect()
            self.assertEqual(data["kind"], "edge")
            self.assertEqual(data["mode"], "sim")
            self.assertEqual(data["agent"]["ue_power"], "off")
            self.assertEqual(data["agent"]["mode"], "edge-container")
            self.assertEqual(data["agent"]["network_mode"], "direct")
            self.assertEqual(len(data["checks"]), 10)
            checks = {check["id"]: check for check in data["checks"]}
            for service in ("tailscaled", "containerd", "edgecore"):
                self.assertEqual(checks["service-" + service]["status"], "ok")
            self.assertEqual(checks["container-tool"]["status"], "ok")
            self.assertIn("nerdctl", checks["container-tool"]["detail"])
            self.assertIn("prepare-script", checks)

    def test_state_files_are_read(self):
        with tempfile.TemporaryDirectory() as tmp:
            Path(tmp, "ue-power").write_text("on\n", encoding="utf-8")
            Path(tmp, "amari-mode").write_text("local\n", encoding="utf-8")
            Path(tmp, "ue-network-mode").write_text("bridge\n", encoding="utf-8")
            agent = EdgeAgent(CommandRunner(dry_run=True), sim=True, state_dir=tmp)
            data = agent.collect()
            self.assertEqual(data["agent"]["ue_power"], "on")
            self.assertEqual(data["agent"]["mode"], "local")
            self.assertEqual(data["agent"]["network_mode"], "bridge")

    def test_missing_state_directory_falls_back(self):
        with tempfile.TemporaryDirectory() as tmp:
            agent = EdgeAgent(CommandRunner(dry_run=True), sim=True, state_dir=str(Path(tmp, "not-created")))
            data = agent.collect()
            self.assertEqual(data["agent"]["ue_power"], "off")


class ServiceCheckStatusTest(unittest.TestCase):
    def test_active_is_ok(self):
        self.assertEqual(service_check_status("active"), "ok")

    def test_transitional_states_are_warn(self):
        for state in ("activating", "deactivating", "reloading", "unknown", ""):
            self.assertEqual(service_check_status(state), "warn")

    def test_failed_states_are_fail(self):
        for state in ("inactive", "failed", "dead"):
            self.assertEqual(service_check_status(state), "fail")


class LiveAgentContainerToolTest(unittest.TestCase):
    class ToolRunner:
        def __init__(self, available):
            self.available = available

        def which(self, name):
            return "/usr/bin/" + name if name in self.available else None

        def run(self, argv, timeout=None, input_text=None, cwd=None):
            if list(argv[:2]) == ["systemctl", "is-active"]:
                return CommandResult(argv=list(argv), returncode=0, stdout="active\n")
            return CommandResult(argv=list(argv), returncode=0, stdout="")

    def test_live_health_reports_selected_tool(self):
        agent = EdgeAgent(self.ToolRunner({"crictl"}), sim=False)
        data = agent.collect()
        checks = {check["id"]: check for check in data["checks"]}
        self.assertEqual(checks["container-tool"]["status"], "ok")
        self.assertIn("crictl ps", checks["container-tool"]["detail"])
        self.assertEqual(data["agent"]["container_tool"], "crictl ps")

    def test_live_health_marks_missing_tool_as_optional_warning(self):
        agent = EdgeAgent(self.ToolRunner(set()), sim=False)
        data = agent.collect()
        checks = {check["id"]: check for check in data["checks"]}
        self.assertEqual(checks["container-tool"]["status"], "warn")
        self.assertIn("不影响 KubeEdge", checks["container-tool"]["detail"])
        self.assertIsNone(data["agent"]["container_tool"])


if __name__ == "__main__":
    unittest.main()
