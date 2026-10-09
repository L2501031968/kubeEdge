import tempfile
import unittest
from pathlib import Path

from kb.agent import EdgeAgent
from kb.runner import CommandRunner


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
            self.assertEqual(len(data["checks"]), 4)

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


if __name__ == "__main__":
    unittest.main()
