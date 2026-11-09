import json
from pathlib import Path
import shutil
import subprocess
import tempfile
import unittest


REPO = Path(__file__).resolve().parents[1]
SUBSCRIPTION = "/subscriptions/00000000-0000-0000-0000-000000000000"
GROUP = f"{SUBSCRIPTION}/resourceGroups/monitoring-test"
CLUSTER = f"{GROUP}/providers/Microsoft.ContainerService/managedClusters/test"


class MonitoringConfigurationTests(unittest.TestCase):
    def test_alerts_validate_against_installed_provider(self):
        if not shutil.which("terraform") or not (REPO / ".terraform").exists():
            self.skipTest("Run terraform init before the offline provider-schema test")
        replacements = {
            "data.azurerm_resource_group.main.name": '"monitoring-test"',
            "data.azurerm_resource_group.main.location": '"westeurope"',
            "azurerm_resource_group.main.name": '"monitoring-test"',
            "azurerm_resource_group.main.location": '"westeurope"',
            "azurerm_kubernetes_cluster.main.id": json.dumps(CLUSTER),
            "azurerm_kubernetes_cluster.main.name": '"test"',
            "azurerm_application_gateway.main.id": json.dumps(
                f"{GROUP}/providers/Microsoft.Network/applicationGateways/test"
            ),
            "azurerm_key_vault.main.id": json.dumps(
                f"{GROUP}/providers/Microsoft.KeyVault/vaults/test"
            ),
            "azurerm_storage_account.main.id": json.dumps(
                f"{GROUP}/providers/Microsoft.Storage/storageAccounts/test"
            ),
            "azurerm_log_analytics_workspace.main.id": json.dumps(
                f"{GROUP}/providers/Microsoft.OperationalInsights/workspaces/test"
            ),
            "azurerm_monitor_workspace.prometheus.id": json.dumps(
                f"{GROUP}/providers/Microsoft.Monitor/accounts/test"
            ),
        }
        with tempfile.TemporaryDirectory() as temporary_directory:
            directory = Path(temporary_directory)
            (directory / ".terraform").symlink_to(REPO / ".terraform", target_is_directory=True)
            shutil.copyfile(REPO / ".terraform.lock.hcl", directory / ".terraform.lock.hcl")
            (directory / "providers.tf").write_text(
                'terraform {\n  required_providers {\n    azurerm = { source = "hashicorp/azurerm" }\n  }\n}\n'
                'provider "azurerm" {\n  features {}\n}\n'
                'variable "name_prefix" { default = "test" }\n'
                'variable "alert_email" { default = "" }\n'
                'locals { tags = {} }\n'
            )
            for filename in ("monitoring.tf", "incident-alerts.tf", "availability-alerts.tf"):
                content = (REPO / filename).read_text()
                for expression, replacement in replacements.items():
                    content = content.replace("${" + expression + "}", json.loads(replacement))
                    content = content.replace(expression, replacement)
                (directory / filename).write_text(content)
            result = subprocess.run(
                ["terraform", f"-chdir={directory}", "validate", "-no-color"],
                capture_output=True, text=True, check=False,
            )
            self.assertEqual(result.returncode, 0, result.stdout + result.stderr)

    def test_restart_query_uses_container_delta(self):
        content = (REPO / "monitoring.tf").read_text()
        self.assertIn("LastCount - FirstCount", content)
        self.assertIn("by Namespace, PodUid, Name, ContainerName", content)
        self.assertNotIn("Restarts = max(PodRestartCount)", content)

    def test_node_conditions_and_image_errors_are_filtered(self):
        content = (REPO / "monitoring.tf").read_text()
        self.assertIn('Status !has "Ready"', content)
        self.assertIn('and Message has_any ("pull", "pulling"', content)

    def test_missing_inventory_returns_a_row_when_empty(self):
        content = (REPO / "incident-alerts.tf").read_text()
        self.assertIn("summarize Samples = count()\n        | where Samples == 0", content)
        self.assertIn("skip_query_validation   = false", content)

    def test_external_probe_is_opt_in_and_checks_tls(self):
        content = (REPO / "availability-alerts.tf").read_text()
        self.assertIn("default     = null", content)
        self.assertEqual(content.count("count               = var.availability_test_url == null ? 0 : 1"), 2)
        self.assertIn("ssl_check_enabled           = true", content)
        self.assertIn("ssl_cert_remaining_lifetime = 14", content)
        self.assertIn("failed_location_count = 2", content)

    def test_statefulset_rule_uses_default_collected_metric(self):
        content = (REPO / "incident-alerts.tf").read_text()
        self.assertIn("kube_statefulset_status_replicas_ready < kube_statefulset_status_replicas", content)
        self.assertNotIn("kube_statefulset_replicas", content)


if __name__ == "__main__":
    unittest.main()