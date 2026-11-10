from copy import deepcopy
import contextlib
import io
import json
import os
from pathlib import Path
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import patch

from faults import AzureDriver, CATALOG, FaultRunner, Journal, KubeDriver, main, plan, value_at
from scaling import MANAGED, OWNER


class FakeDriver:
    def __init__(self):
        self.obj = {"metadata": {"uid": "service-uid", "resourceVersion": "1"},
                    "spec": {"selector": {"app": "store-front"}, "unrelated": "keep"}}
        self.target = {"namespace_uid": "namespace-uid", "command": ["fake-context"]}
        self.writes = 0
        self.fail_after_write = False

    def get(self, kind, name):
        return deepcopy(self.obj)

    def binding(self):
        return deepcopy(self.target)

    def healthy(self):
        return None

    def prerequisites(self, card):
        return None

    def patch(self, kind, name, obj, edits, reverse=False, dry_run=False):
        if obj["metadata"] != self.obj["metadata"]:
            raise ValueError("Concurrent change")
        for edit in edits:
            if value_at(self.obj, edit["path"]) != edit["after" if reverse else "before"]:
                raise ValueError("Field changed")
        if not dry_run:
            for edit in edits:
                tokens = [token.replace("~1", "/").replace("~0", "~") for token in edit["path"].strip("/").split("/")]
                parent = self.obj
                for token in tokens[:-1]:
                    parent = parent[int(token)] if isinstance(parent, list) else parent[token]
                parent[int(tokens[-1]) if isinstance(parent, list) else tokens[-1]] = deepcopy(edit["before" if reverse else "after"])
            self.writes += 1
            self.obj["metadata"]["resourceVersion"] = str(self.writes + 1)
            if self.fail_after_write:
                raise RuntimeError("Connection lost after acceptance")


class FaultTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.driver = FakeDriver()
        self.runner = FaultRunner(self.driver, Journal(self.temporary.name))

    def inject(self):
        return self.runner.inject("service-selector", "private-baseline-reference")

    def test_preview_does_not_write(self):
        self.runner.preview("service-selector")
        self.assertEqual(self.driver.writes, 0)
        self.assertFalse(self.runner.journal.path.exists())

    def test_journal_repository_boundary(self):
        repo = Path(__file__).resolve().parents[2]
        with patch.object(Path, "mkdir") as mkdir:
            with self.assertRaises(ValueError):
                Journal(repo / "private-fault-journal")
            mkdir.assert_not_called()
            journal = Journal(repo.parent / "private-fault-journal")
            self.assertEqual(journal.directory.parent, repo.parent)
            mkdir.assert_called_once_with(mode=0o700, parents=True, exist_ok=True)

    def test_exact_restore_and_archive(self):
        self.inject()
        self.driver.obj["spec"]["unrelated"] = "human-change"
        self.runner.revert()
        self.assertEqual(self.driver.obj["spec"]["selector"], {"app": "store-front"})
        self.assertEqual(self.driver.obj["spec"]["unrelated"], "human-change")
        entry = self.runner.close("private-recovery-reference")
        self.assertTrue((Path(self.temporary.name) / (entry["run_id"] + ".json")).exists())

    def test_second_fault_requires_closed_incident(self):
        self.inject()
        with self.assertRaises(ValueError):
            self.inject()
        self.runner.revert()
        with self.assertRaises(ValueError):
            self.inject()

    def test_drift_refuses_restore(self):
        self.inject()
        self.driver.obj["spec"]["selector"] = {"app": "human-change"}
        with self.assertRaises(ValueError):
            self.runner.revert()
        self.assertEqual(self.driver.writes, 1)

    def test_recreated_object_refuses_restore(self):
        self.inject()
        self.driver.obj["metadata"]["uid"] = "replacement"
        with self.assertRaises(ValueError):
            self.runner.revert()

    def test_context_or_namespace_change_refuses_restore(self):
        self.inject()
        self.driver.target["namespace_uid"] = "other"
        with self.assertRaises(ValueError):
            self.runner.revert()

    def test_uncertain_write_keeps_recoverable_journal(self):
        self.driver.fail_after_write = True
        with self.assertRaises(RuntimeError):
            self.inject()
        self.assertEqual(self.runner.journal.read()["phase"], "prepared")
        self.assertEqual(self.runner.inspect()[2], "fault")
        self.driver.fail_after_write = False
        self.runner.revert()
        self.assertEqual(self.runner.inspect()[2], "baseline")

    def test_human_repair_is_not_overwritten(self):
        self.inject()
        self.driver.obj["spec"]["selector"] = {"app": "store-front"}
        self.runner.revert()
        self.assertEqual(self.driver.writes, 1)

    def test_close_rejects_unrestored_fault(self):
        self.inject()
        with self.assertRaises(ValueError):
            self.runner.close("not-recovered")

    def test_symlink_journal_refused(self):
        other = Path(self.temporary.name) / "other.json"
        other.write_text(json.dumps({"keep": True}))
        self.runner.journal.path.symlink_to(other)
        with self.assertRaises(ValueError):
            self.inject()
        self.assertEqual(json.loads(other.read_text()), {"keep": True})

    def test_lock_excludes_second_operator(self):
        with self.runner.journal.lock():
            with self.assertRaises(ValueError):
                with Journal(self.temporary.name).lock():
                    self.fail("Second operator obtained the lock")

    def test_never_injected_prepared_record_can_be_recovered(self):
        original = self.driver.patch

        def fail_before_write(*args, **kwargs):
            if not kwargs.get("dry_run"):
                raise RuntimeError("Not sent")
            return original(*args, **kwargs)

        self.driver.patch = fail_before_write
        with self.assertRaises(RuntimeError):
            self.inject()
        self.driver.patch = original
        self.runner.revert()
        self.assertEqual(self.driver.writes, 0)
        self.assertEqual(self.runner.journal.read()["phase"], "reverted")


def fixture(card):
    kind, name, description = CATALOG[card]
    obj = {"metadata": {"uid": "fixture-uid", "resourceVersion": "1", "labels": {MANAGED: OWNER}}, "spec": {}}
    if kind == "deployment":
        obj["spec"] = {"replicas": 1, "template": {"spec": {
            "nodeSelector": {"kubernetes.io/os": "linux"},
            "containers": [{"name": name, "image": f"ghcr.io/azure-samples/aks-store-demo/{name}:2.2.0",
                            "readinessProbe": {"httpGet": {"port": 3002, "path": "/health"}},
                            "resources": {"requests": {"cpu": "1m", "memory": "1Mi"},
                                          "limits": {"cpu": "2m", "memory": "20Mi"}},
                            "envFrom": [{"configMapRef": {"name": "order-service-configs"}}],
                            "env": [{"name": "ORDER_DB_URI", "value": "mongodb://documentdb:10260/?tls=true&tlsAllowInvalidCertificates=true"}]}]}}}
    elif kind == "service":
        obj["spec"] = {"selector": {"app": name}, "ports": [{"port": 3002, "targetPort": 3002}]}
        if card == "queue-port":
            obj["spec"]["ports"] = [{"port": 15672, "targetPort": 15672}, {"port": 5672, "targetPort": 5672}]
    elif kind == "ingress":
        obj["metadata"]["annotations"] = {"kubernetes.io/ingress.class": "azure/application-gateway",
                                         "appgw.ingress.kubernetes.io/health-probe-path": "/"}
        obj["spec"] = {"tls": [{"secretName": "store-front-tls"}], "rules": [{"http": {"paths": [
            {"backend": {"service": {"name": "store-front", "port": {"number": 80}}}}]}}]}
    elif kind == "scaledobject":
        obj["spec"] = {"scaleTargetRef": {"name": "virtual-worker"}, "triggers": [
            {"type": "rabbitmq", "authenticationRef": {"name": "lab-scaling"}, "metadata": {"queueName": "orders"}}]}
    elif kind == "nodepool":
        obj["spec"] = {"limits": {"cpu": "8"}, "template": {"spec": {"requirements": [
            {"key": "karpenter.azure.com/sku-cpu", "operator": "In", "values": ["2", "4"]}]}}}
    elif kind == "azure-nsg":
        obj["properties"] = {"access": "Allow", "direction": "Inbound", "protocol": "Tcp",
                             "sourceAddressPrefix": "Internet", "sourcePortRange": "*", "destinationPortRange": "443"}
    elif kind == "azure-alert":
        obj["properties"] = {"enabled": True}
    return obj


class CatalogTests(unittest.TestCase):
    setUp = FaultTests.setUp

    def test_all_cards_round_trip_with_unrelated_fields_preserved(self):
        for card in CATALOG:
            with self.subTest(card=card), tempfile.TemporaryDirectory() as directory:
                driver = FakeDriver()
                driver.obj = fixture(card)
                baseline = deepcopy(driver.obj)
                runner = FaultRunner(driver, Journal(directory))
                runner.inject(card, "baseline")
                self.assertEqual(runner.inspect()[2], "fault")
                runner.revert()
                driver.obj["metadata"]["resourceVersion"] = baseline["metadata"]["resourceVersion"]
                self.assertEqual(driver.obj, baseline)
                runner.close("recovery")

    def test_scaling_objects_require_ownership(self):
        for card in ["keda-auth", "keda-queue", "nap-capacity"]:
            obj = fixture(card)
            obj["metadata"]["labels"] = {}
            with self.subTest(card=card), self.assertRaises(ValueError):
                plan(card, obj)

    def test_https_fault_rejects_control_plane_and_http_rules(self):
        for change in [{"sourceAddressPrefix": "GatewayManager"}, {"destinationPortRange": "80"},
                       {"destinationPortRanges": ["443", "65200-65535"]}]:
            obj = fixture("azure-https-deny")
            obj["properties"].update(change)
            with self.subTest(change=change), self.assertRaises(ValueError):
                plan("azure-https-deny", obj)

    def test_legacy_hostname_ingress_rejected(self):
        obj = fixture("tls-secret")
        obj["spec"]["rules"][0]["host"] = "sre.example.test"
        with self.assertRaises(ValueError):
            plan("tls-secret", obj)

    def test_cli_write_confirmation_precedes_driver_creation(self):
        with patch("faults.KubeDriver") as driver, contextlib.redirect_stderr(io.StringIO()):
            for arguments in [
                ["inject", "--fault", "service-selector"],
                ["inject", "--fault", "service-selector", "--context", "lab", "--api-server", "https://lab", "--state-dir", self.temporary.name],
                ["inject", "--fault", "azure-alert-disable", "--context", "lab", "--api-server", "https://lab",
                 "--state-dir", self.temporary.name, "--confirm-namespace", "aks-store-demo", "--baseline-verified", "--evidence", "ref"],
            ]:
                with self.assertRaises(SystemExit):
                    main(arguments)
            driver.assert_not_called()


class DriverTests(unittest.TestCase):
    def kube(self, runner):
        directory = tempfile.TemporaryDirectory()
        self.addCleanup(directory.cleanup)
        config = Path(directory.name) / "kubeconfig"
        config.touch()
        environment = patch.dict(os.environ, {"KUBECONFIG": str(config)})
        environment.start()
        self.addCleanup(environment.stop)
        return KubeDriver("lab", "https://lab.example", runner)

    def test_kube_patch_has_uid_version_and_field_tests(self):
        from test_scaling import Runner
        runner = Runner()
        driver = self.kube(runner)
        obj = fixture("service-port")
        with patch.object(driver, "call") as call:
            driver.patch("service", "product-service", obj, plan("service-port", obj), dry_run=True)
            args = call.call_args.args
            operations = json.loads(args[args.index("-p") + 1])
            self.assertEqual([operation["path"] for operation in operations[:3]],
                             ["/metadata/uid", "/metadata/resourceVersion", "/spec/ports/0/targetPort"])
            self.assertIn("--dry-run=server", args)

    def test_unhealthy_baseline_blocks_injection(self):
        from test_scaling import Runner
        runner = Runner()
        driver = self.kube(runner)
        runner.objects[("deployment", "store-front")] = {
            "metadata": {"generation": 2}, "spec": {"replicas": 1},
            "status": {"observedGeneration": 1, "readyReplicas": 1, "updatedReplicas": 1}}
        with self.assertRaises(ValueError):
            driver.healthy()
        self.assertFalse(runner.writes())

    def test_worker_fault_refuses_active_scaler(self):
        from test_scaling import Runner
        runner = Runner()
        driver = self.kube(runner)
        runner.objects[("hpa", None)] = {"items": [{"spec": {"scaleTargetRef": {"name": "virtual-worker"}}}]}
        with self.assertRaises(ValueError):
            driver.prerequisites("worker-down")

    def test_nap_fault_refuses_existing_capacity(self):
        from test_scaling import Runner
        runner = Runner()
        driver = self.kube(runner)
        runner.objects[("nodes", None)] = {"items": [{"metadata": {"labels": {"karpenter.sh/nodepool": "lab-scaling"}}}]}
        with self.assertRaises(ValueError):
            driver.prerequisites("nap-capacity")

    def test_azure_scope_and_field_only_update(self):
        prefix = "/subscriptions/00000000-0000-0000-0000-000000000001/resourceGroups/lab/providers/"
        target = prefix + "Microsoft.Network/networkSecurityGroups/lab/securityRules/AllowHTTPSInbound"
        aks = prefix + "Microsoft.ContainerService/managedClusters/lab"
        obj = fixture("azure-https-deny")
        obj.update(id=target, etag="version-1")
        calls = []

        def az(command, **kwargs):
            calls.append(command)
            if "update" in command:
                obj["properties"]["access"] = command[command.index("--access") + 1]
                obj["etag"] = "version-2"
            return SimpleNamespace(returncode=0, stdout=json.dumps(obj))

        driver = AzureDriver(None, aks, target, az)
        current = driver.get("azure-nsg", "approved-https-rule")
        edits = plan("azure-https-deny", current)
        driver.patch("azure-nsg", "approved-https-rule", current, edits, dry_run=True)
        self.assertFalse(any("update" in command for command in calls))
        driver.patch("azure-nsg", "approved-https-rule", current, edits)
        updates = [command for command in calls if "update" in command]
        self.assertEqual(len(updates), 1)
        self.assertEqual(updates[0][updates[0].index("--access") + 1], "Deny")
        self.assertIn(target, updates[0])
        with self.assertRaises(ValueError):
            AzureDriver(None, aks, target.replace("resourceGroups/lab/", "resourceGroups/other/"), az)

    def test_azure_etag_change_prevents_update(self):
        prefix = "/subscriptions/00000000-0000-0000-0000-000000000001/resourceGroups/lab/providers/"
        target = prefix + "Microsoft.Insights/metricAlerts/alert-appgw-unhealthy-host"
        driver = AzureDriver(None, prefix + "Microsoft.ContainerService/managedClusters/lab", target)
        before = fixture("azure-alert-disable")
        fresh = deepcopy(before)
        fresh["metadata"]["resourceVersion"] = "other"
        with patch.object(driver, "get", return_value=fresh), patch.object(driver, "az") as az:
            with self.assertRaises(ValueError):
                driver.patch("azure-alert", "alert-appgw-unhealthy-host", before, plan("azure-alert-disable", before))
            az.assert_not_called()


if __name__ == "__main__":
    unittest.main()