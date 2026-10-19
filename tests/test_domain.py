import unittest

from kb import fixtures
from kb.discovery import build_snapshot
from kb.domain import build_domain, is_accelerator_key, node_alias


def demo_domain(users_source=None, capacity=64):
    snapshot = build_snapshot(fixtures.demo_raw(), "demo")
    return build_domain(
        snapshot["nodes"],
        snapshot["pods"],
        snapshot["deployments"],
        users_source,
        capacity,
        "demo",
    )


class ModuleMembershipTest(unittest.TestCase):
    def setUp(self):
        domain = demo_domain(fixtures.demo_users())
        self.modules = {item["id"]: item for item in domain["modules"]}

    def test_components_are_grouped_by_business_module(self):
        ntn_ran = {item["name"] for item in self.modules["ntn-ran"]["components"]}
        self.assertEqual(ntn_ran, {"amari-ran", "amari-sat"})
        core = {item["name"] for item in self.modules["core"]["components"]}
        self.assertEqual(core, {"amari-core", "amari-ims"})
        ue = {item["name"] for item in self.modules["ue"]["components"]}
        self.assertEqual(ue, {"amari-ue"})

    def test_modules_run_on_expected_nodes(self):
        self.assertEqual(self.modules["ntn-ran"]["nodes"], ["NTN-RAN"])
        self.assertEqual(self.modules["ue"]["nodes"], ["UE"])
        self.assertEqual(self.modules["ntn-ran"]["status"], "running")
        self.assertEqual(self.modules["ue"]["status"], "running")

    def test_diagnostic_load_is_not_a_business_module(self):
        for item in self.modules.values():
            self.assertNotIn("amari-load", [component["name"] for component in item["components"]])
            self.assertFalse([pod for pod in item["pods"] if pod["name"].startswith("amari-load")])

    def test_ai_and_accelerator_modules_are_not_deployed(self):
        self.assertEqual(self.modules["ai-model"]["status"], "not-deployed")
        self.assertEqual(self.modules["accelerator"]["status"], "not-deployed")
        self.assertEqual(self.modules["ai-model"]["pods_total"], 0)

    def test_module_resources_cover_cpu_memory_storage(self):
        rows = {row["key"]: row for row in self.modules["ntn-ran"]["resources"]}
        self.assertAlmostEqual(rows["cpu"]["request"], 0.8)
        self.assertEqual(rows["memory"]["request"], 640 * 1024 ** 2)
        self.assertIsNotNone(rows["storage"]["pool"])


class ResourcePoolTest(unittest.TestCase):
    def setUp(self):
        self.resources = demo_domain(fixtures.demo_users())["resources"]

    def test_core_pools_present(self):
        pools = {pool["key"]: pool for pool in self.resources["pools"]}
        for key in ("cpu", "memory", "storage", "network"):
            self.assertIn(key, pools)
        self.assertEqual(pools["network"]["allocatable"], 2500)

    def test_accelerators_are_discovered_from_extended_resources(self):
        pools = {pool["key"]: pool for pool in self.resources["pools"]}
        self.assertEqual(pools["accel-gpu"]["allocatable"], 2)
        self.assertEqual(pools["accel-npu"]["allocatable"], 1)
        labels = sorted(pool["label"] for pool in self.resources["accelerators"])
        self.assertEqual(labels, ["GPU", "NPU"])

    def test_missing_accelerators_are_reported_not_zeroed(self):
        missing = " ".join(self.resources["missing_accelerators"])
        for label in ("FPGA", "DPU", "ASIC"):
            self.assertIn(label, missing)
        self.assertNotIn("GPU", missing)
        self.assertNotIn("NPU", missing)

    def test_free_schedulable_equals_allocatable_minus_requested(self):
        for pool in self.resources["pools"]:
            if pool["requested"] is None or pool["allocatable"] is None:
                continue
            self.assertAlmostEqual(
                pool["free_schedulable"],
                pool["allocatable"] - pool["requested"],
            )

    def test_node_view_uses_display_alias(self):
        nodes = {node["name"]: node for node in self.resources["nodes"]}
        self.assertEqual(nodes["d2000-edge"]["alias"], "NTN-RAN")
        self.assertEqual(nodes["sdr"]["alias"], "UE")
        self.assertEqual(nodes["cloud"]["alias"], "云端控制节点")
        self.assertEqual(nodes["d2000-edge"]["arch"], "arm64")
        self.assertEqual([item["label"] for item in nodes["cloud"]["accelerators"]], ["GPU"])


class UserFacetTest(unittest.TestCase):
    def test_missing_source_reports_unavailable(self):
        users = demo_domain(None)["users"]
        self.assertFalse(users["available"])
        self.assertIsNone(users["online"])
        self.assertEqual(users["capacity"], 64)
        self.assertEqual(users["users"], [])

    def test_demo_source_counts_online_users(self):
        users = demo_domain(fixtures.demo_users())["users"]
        self.assertTrue(users["available"])
        self.assertEqual(users["online"], 8)
        self.assertEqual(len(users["users"]), 12)
        self.assertEqual(users["ue_pod_count"], 1)
        self.assertIn("按 Pod 申请量折算为平均值", users["share_note"])
        self.assertIn("cell-1", users["cells"])

    def test_per_user_request_is_derived_from_ue_pod(self):
        users = demo_domain(fixtures.demo_users())["users"]
        # UE Pod 申请 200m，8 个在线用户折算 25m。
        self.assertAlmostEqual(users["per_user_cpu_request_cores"], 0.025)
        self.assertEqual(users["per_user_mem_request_bytes"], 192 * 1024 ** 2 // 8)
        self.assertEqual(users["generated_at"], "2026-10-08T09:00:00Z")

    def test_node_alias_helper(self):
        self.assertEqual(node_alias("d2000-edge"), "NTN-RAN")
        self.assertEqual(node_alias("sdr"), "UE")
        self.assertEqual(node_alias("cloud"), "云端控制节点")
        self.assertEqual(node_alias("unknown-node"), "unknown-node")


class AcceleratorKeyTest(unittest.TestCase):
    def test_extended_resource_keys(self):
        self.assertTrue(is_accelerator_key("nvidia.com/gpu"))
        self.assertTrue(is_accelerator_key("huawei.com/Ascend310"))
        self.assertTrue(is_accelerator_key("xilinx.com/fpga"))

    def test_platform_and_standard_keys_are_not_accelerators(self):
        self.assertFalse(is_accelerator_key("cpu"))
        self.assertFalse(is_accelerator_key("memory"))
        self.assertFalse(is_accelerator_key("ephemeral-storage"))
        self.assertFalse(is_accelerator_key("hugepages-2Mi"))
        self.assertFalse(is_accelerator_key("kb.local/bandwidth-mbps"))
        self.assertFalse(is_accelerator_key(""))


class BuildDomainTest(unittest.TestCase):
    def test_domain_shape(self):
        domain = demo_domain(fixtures.demo_users())
        self.assertEqual(
            sorted(domain.keys()),
            ["extra_summary", "modules", "resources", "users"],
        )
        summary = domain["extra_summary"]
        self.assertEqual(summary["modules_total"], 5)
        self.assertEqual(summary["modules_running"], 3)
        self.assertEqual(summary["modules_absent"], 2)
        self.assertEqual(summary["users_online"], 8)
        self.assertTrue(summary["users_available"])
        self.assertEqual(summary["accelerator_labels"], ["GPU", "NPU"])
        self.assertEqual(summary["accelerator_total"], 3)


if __name__ == "__main__":
    unittest.main()
