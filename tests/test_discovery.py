import unittest

from kb import fixtures
from kb.discovery import build_snapshot, parse_top_nodes, parse_top_pods


class BuildSnapshotTest(unittest.TestCase):
    def setUp(self):
        self.snapshot = build_snapshot(fixtures.demo_raw(), "demo")

    def test_summary(self):
        summary = self.snapshot["summary"]
        self.assertEqual(summary["nodes_total"], 3)
        self.assertEqual(summary["nodes_ready"], 3)
        self.assertEqual(summary["pods_total"], 6)
        self.assertEqual(summary["pods_running"], 6)
        self.assertEqual(summary["deployments_total"], 6)

    def test_nodes_carry_usage_and_requests(self):
        nodes = {node["name"]: node for node in self.snapshot["nodes"]}
        self.assertEqual(nodes["d2000-edge"]["display_name"], "d2000-edge")
        self.assertIn("edge", nodes["sdr"]["roles"])
        self.assertAlmostEqual(nodes["cloud"]["cpu_used_cores"], 0.12)
        self.assertAlmostEqual(nodes["d2000-edge"]["cpu_used_cores"], 1.45)
        self.assertAlmostEqual(nodes["d2000-edge"]["cpu_requested_cores"], 1.7)
        self.assertEqual(nodes["sdr"]["pods"], 1)

    def test_pods_resources_and_qos(self):
        pods = {(pod["namespace"], pod["name"]): pod for pod in self.snapshot["pods"]}
        core = pods[("amari-d2000", "amari-core-7d9f8c5b4-a1b2c")]
        self.assertEqual(core["deployment"], "amari-core")
        self.assertEqual(core["qos"], "Burstable")
        self.assertAlmostEqual(core["cpu_request_cores"], 0.3)
        self.assertAlmostEqual(core["cpu_limit_cores"], 0.6)
        self.assertAlmostEqual(core["cpu_used_cores"], 0.29)
        self.assertTrue(core["ready"])

    def test_deployments(self):
        deployments = {item["name"]: item for item in self.snapshot["deployments"]}
        self.assertEqual(deployments["amari-ue"]["namespace"], "amari-edge")
        self.assertAlmostEqual(deployments["amari-ran"]["cpu_request_total_cores"], 0.5)
        self.assertEqual(deployments["amari-ue"]["nodes"], ["sdr"])

    def test_checks(self):
        checks = {check["id"]: check for check in self.snapshot["checks"]}
        for check_id in ("kubectl", "cluster", "nodes", "metrics", "device-crd", "amari"):
            self.assertIn(check_id, checks)
        self.assertEqual(checks["metrics"]["status"], "ok")
        self.assertEqual(checks["amari"]["status"], "ok")
        self.assertEqual(self.snapshot["kubeedge"]["edge_nodes"], ["d2000-edge", "sdr"])

    def test_unreachable_cluster(self):
        snapshot = build_snapshot({"error": "无法连接集群"}, "live")
        self.assertEqual(snapshot["checks"][0]["status"], "fail")
        self.assertEqual(snapshot["nodes"], [])
        self.assertFalse(snapshot["kubernetes"]["reachable"])


class ParseTopTest(unittest.TestCase):
    def test_nodes(self):
        text = "NAME          CPU(cores)   CPU%   MEMORY(bytes)   MEMORY%\ncloud   120m   3%   900Mi   12%\n"
        parsed = parse_top_nodes(text)
        self.assertAlmostEqual(parsed["cloud"]["cpu_cores"], 0.12)
        self.assertEqual(parsed["cloud"]["mem_bytes"], 900 * 1024 ** 2)

    def test_pods(self):
        text = "NAMESPACE   NAME   CPU(cores)   MEMORY(bytes)\namari-d2000   amari-ran-x   480m   350Mi\n"
        parsed = parse_top_pods(text)
        self.assertAlmostEqual(parsed[("amari-d2000", "amari-ran-x")]["cpu_cores"], 0.48)
        self.assertEqual(parsed[("amari-d2000", "amari-ran-x")]["mem_bytes"], 350 * 1024 ** 2)

    def test_garbage_ignored(self):
        self.assertEqual(parse_top_nodes("错误: metrics 不可用"), {})
        self.assertEqual(parse_top_pods(None), {})


if __name__ == "__main__":
    unittest.main()
