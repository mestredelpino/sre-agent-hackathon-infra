import base64
import contextlib
import io
import json
import os
from pathlib import Path
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import patch

import scaling


class Runner:
    def __init__(self):
        self.calls = []
        self.objects = {}
        self.config = {"clusters": [{"name": "lab", "cluster": {"server": "https://lab.example"}}],
                       "contexts": [{"name": "lab", "context": {"cluster": "lab"}}]}

    def __call__(self, command, **kwargs):
        args = command[5:]
        self.calls.append((args, kwargs))
        result = ""
        if args[:2] == ["config", "view"]:
            result = json.dumps(self.config)
        elif args[0] == "get":
            name = args[2] if len(args) > 2 and not args[2].startswith("-") else None
            obj = self.objects.get((args[1], name), {} if name else {"items": []})
            result = json.dumps(obj) if obj else ""
        elif args[0] in {"create", "replace"}:
            obj = json.loads(kwargs["input"])
            obj["metadata"].update(resourceVersion="2", uid="test-uid")
            self.objects[(obj["kind"].lower(), obj["metadata"]["name"])] = obj
        elif args[0] == "patch":
            operations = json.loads(args[-1])
            self.objects[("deployment", "virtual-customer")]["metadata"]["annotations"] = operations[-1]["value"]
        elif args[0] == "scale":
            self.objects[("deployment", "virtual-customer")]["spec"]["replicas"] = int(args[-1].split("=")[1])
        return SimpleNamespace(returncode=0, stdout=result, stderr="")

    def writes(self):
        return [(args, kwargs) for args, kwargs in self.calls if args[0] not in {"get", "config", "exec"}]


class ScalingTests(unittest.TestCase):
    def setUp(self):
        self.directory = tempfile.TemporaryDirectory()
        self.addCleanup(self.directory.cleanup)
        config = Path(self.directory.name) / "dedicated"
        config.touch()
        self.environment = patch.dict(os.environ, {"KUBECONFIG": str(config)}, clear=True)
        self.environment.start()
        self.addCleanup(self.environment.stop)
        self.runner = Runner()
        for name in ["virtual-worker", "virtual-customer"]:
            self.runner.objects[("deployment", name)] = {"metadata": {"name": name, "resourceVersion": "1"}, "spec": {"replicas": 2}}
        for name in ["scaledobjects.keda.sh", "triggerauthentications.keda.sh", "nodepools.karpenter.sh", "nodeclaims.karpenter.sh", "aksnodeclasses.karpenter.azure.com"]:
            self.runner.objects[("crd", name)] = {"metadata": {"name": name}}
        self.runner.objects[("configmap", "rabbitmq")] = {"data": {"rabbitmq.conf": "default_user = test-user\ndefault_pass = test-pass\n"}}

    def lab(self):
        return scaling.Lab("lab", "https://lab.example", self.runner)

    def test_target_guards_before_writes(self):
        variants = [({}, "http://lab.example"), ({"KUBECONFIG": ""}, "https://lab.example"),
                    ({"KUBECONFIG": os.environ["KUBECONFIG"] + ":other"}, "https://lab.example"),
                    ({}, "https://other.example"), ({}, "https://lab.example/")]
        for env, server in variants:
            with self.subTest(env=env, server=server), patch.dict(os.environ, env):
                with self.assertRaises(ValueError):
                    scaling.Lab("lab", server, self.runner)
        self.runner.config["contexts"][0]["name"] = "wrong"
        with self.assertRaises(ValueError):
            self.lab()
        self.runner.config["contexts"][0]["name"] = "lab"
        self.runner.config["clusters"][0]["cluster"]["insecure-skip-tls-verify"] = True
        with self.assertRaises(ValueError):
            self.lab()
        with patch.dict(os.environ, {"KUBECONFIG": str(Path.home() / ".kube/config")}):
            with self.assertRaises(ValueError):
                self.lab()
        self.assertFalse(self.runner.writes())

    def test_setup_shapes_and_secret_stdin(self):
        output = io.StringIO()
        with contextlib.redirect_stdout(output):
            self.lab().setup()
        self.assertEqual(output.getvalue(), "")
        self.assertEqual(len(self.runner.writes()), 5)
        for args, kwargs in self.runner.writes():
            self.assertNotIn("test-pass", " ".join(args))
            self.assertEqual(json.loads(kwargs["input"])["metadata"]["labels"][scaling.MANAGED], scaling.OWNER)
        secret = self.runner.objects[("secret", scaling.NAME)]
        self.assertEqual(secret["stringData"]["password"], "test-pass")
        scaled = self.runner.objects[("scaledobject", scaling.NAME)]["spec"]
        self.assertEqual([scaled[key] for key in ["pollingInterval", "cooldownPeriod", "minReplicaCount", "maxReplicaCount"]], [15, 60, 1, 12])
        self.assertEqual(scaled["triggers"][0]["metadata"]["value"], "5")
        self.assertEqual(scaled["advanced"]["horizontalPodAutoscalerConfig"]["behavior"]["scaleDown"]["stabilizationWindowSeconds"], 60)
        pool = self.runner.objects[("nodepool", scaling.NAME)]["spec"]
        self.assertEqual(pool["limits"], {"cpu": "8", "memory": "32Gi"})
        self.assertEqual(pool["template"]["spec"]["taints"][0]["key"], scaling.NAME)
        self.assertEqual(pool["disruption"]["consolidateAfter"], "60s")
        self.assertEqual(pool["disruption"]["consolidationPolicy"], "WhenEmptyOrUnderutilized")
        requirements = {item["key"]: item["values"] for item in pool["template"]["spec"]["requirements"]}
        self.assertEqual(requirements["karpenter.azure.com/sku-cpu"], ["2", "4"])
        self.assertEqual(requirements["karpenter.azure.com/sku-family"], ["D"])
        self.assertEqual(requirements["kubernetes.io/arch"], ["amd64"])
        self.assertEqual(requirements["karpenter.sh/capacity-type"], ["on-demand"])
        self.assertEqual(self.runner.objects[("aksnodeclass", scaling.NAME)]["spec"]["imageFamily"], "Ubuntu")
        self.assertEqual(self.runner.objects[("deployment", "virtual-worker")]["spec"], {"replicas": 2})

    def test_preexisting_unowned_objects_block_all_setup_writes(self):
        for kind in ["secret", "triggerauthentication", "scaledobject", "nodepool", "aksnodeclass"]:
            with self.subTest(kind=kind):
                self.runner.objects[(kind, scaling.NAME)] = {"metadata": {"name": scaling.NAME}}
                with self.assertRaises(ValueError):
                    self.lab().setup()
                self.assertFalse(self.runner.writes())
                del self.runner.objects[(kind, scaling.NAME)]

    def test_missing_crd_or_deployment(self):
        for key in [("crd", "nodeclaims.karpenter.sh"), ("deployment", "virtual-worker")]:
            original = self.runner.objects.pop(key)
            with self.assertRaises(ValueError):
                self.lab().setup()
            self.assertFalse(self.runner.writes())
            self.runner.objects[key] = original

    def test_competing_autoscalers(self):
        for kind in ["hpa", "scaledobject"]:
            self.runner.objects[(kind, None)] = {"items": [{"metadata": {"name": "foreign"}, "spec": {"scaleTargetRef": {"name": "virtual-worker"}}}]}
            with self.assertRaises(ValueError):
                self.lab().setup()
            self.assertFalse(self.runner.writes())
            del self.runner.objects[(kind, None)]

    def test_repeat_setup_accepts_only_own_generated_hpa(self):
        lab = self.lab()
        lab.setup()
        scaled = self.runner.objects[("scaledobject", scaling.NAME)]
        self.runner.objects[("scaledobject", None)] = {"items": [scaled]}
        self.runner.objects[("hpa", None)] = {"items": [{"metadata": {"name": "keda-hpa-lab-scaling", "ownerReferences": [
            {"kind": "ScaledObject", "uid": scaled["metadata"]["uid"], "controller": True}]}, "spec": {"scaleTargetRef": {"name": "virtual-worker"}}}]}
        lab.setup()
        self.assertEqual(len(self.runner.writes()), 10)

    def test_credentials_environment_precedence_and_partial_rejection(self):
        with patch.dict(os.environ, {"RABBITMQ_USERNAME": "operator", "RABBITMQ_PASSWORD": "private"}):
            self.assertEqual(self.lab().credentials(), ["operator", "private"])
        with patch.dict(os.environ, {"RABBITMQ_USERNAME": "operator"}):
            with self.assertRaises(ValueError):
                self.lab().credentials()

    def test_complex_config_requires_operator_environment(self):
        for text in ['default_user = ${USER}\ndefault_pass = "complex value"',
                     'default_user = test-user\ndefault_pass = test-pass\ndefault_pass = "complex value"']:
            self.runner.objects[("configmap", "rabbitmq")]["data"]["rabbitmq.conf"] = text
            with self.assertRaises(ValueError):
                self.lab().credentials()

    def test_structured_order_service_refs(self):
        del self.runner.objects[("configmap", "rabbitmq")]
        self.runner.objects[("secret", "order-auth")] = {"data": {key: base64.b64encode(value.encode()).decode() for key, value in [
            ("ORDER_QUEUE_USERNAME", "ref-user"), ("ORDER_QUEUE_PASSWORD", "ref-pass")]}}
        container = {"name": "order-service", "envFrom": [{"secretRef": {"name": "order-auth"}}]}
        self.runner.objects[("deployment", "order-service")] = {"spec": {"template": {"spec": {"containers": [container]}}}}
        self.assertEqual(self.lab().credentials(), ["ref-user", "ref-pass"])
        container["env"] = [{"name": "ORDER_QUEUE_PASSWORD", "valueFrom": {"secretKeyRef": {"name": "order-auth", "key": "ORDER_QUEUE_PASSWORD"}}}]
        self.assertEqual(self.lab().credentials(), ["ref-user", "ref-pass"])

    def test_customer_bounds_and_preserved_baseline(self):
        lab = self.lab()
        for count in [0, 13, -1]:
            with self.assertRaises(ValueError):
                lab.customers(True, count)
        lab.customers(True)
        lab.customers(True, 12)
        lab.customers(False)
        lab.customers(False)
        obj = self.runner.objects[("deployment", "virtual-customer")]
        self.assertEqual(obj["spec"]["replicas"], 2)
        self.assertEqual(obj["metadata"]["annotations"][scaling.BASELINE], "2")
        self.assertEqual([args[0] for args, _ in self.runner.writes()], ["patch", "scale", "scale", "scale", "scale"])

    def test_zero_customer_baseline_and_unrelated_annotations(self):
        obj = self.runner.objects[("deployment", "virtual-customer")]
        obj["spec"]["replicas"] = 0
        obj["metadata"]["annotations"] = {"keep": "unchanged"}
        lab = self.lab()
        lab.customers(True)
        lab.customers(False)
        self.assertEqual(obj["spec"]["replicas"], 0)
        self.assertEqual(obj["metadata"]["annotations"], {"keep": "unchanged", scaling.BASELINE: "0"})

    def test_stop_without_baseline_and_invalid_baseline(self):
        lab = self.lab()
        lab.customers(False)
        self.assertFalse(self.runner.writes())
        self.runner.objects[("deployment", "virtual-customer")]["metadata"]["annotations"] = {scaling.BASELINE: "bad"}
        with self.assertRaises(ValueError):
            lab.customers(False)

    def test_probe_and_safe_cleanup(self):
        lab = self.lab()
        lab.nodes(False)
        self.assertFalse(self.runner.writes())
        lab.setup()
        lab.nodes(True)
        probe = self.runner.objects[("deployment", scaling.PROBE)]["spec"]
        self.assertEqual(probe["replicas"], 2)
        pod = probe["template"]["spec"]
        self.assertEqual(pod["nodeSelector"], {"karpenter.sh/nodepool": scaling.NAME})
        self.assertEqual(pod["containers"][0]["resources"]["requests"], {"cpu": "500m", "memory": "128Mi"})
        self.assertEqual(pod["containers"][0]["resources"]["limits"], {"cpu": "1", "memory": "256Mi"})
        self.assertEqual(pod["containers"][0]["image"], "registry.k8s.io/pause:3.10")
        self.assertEqual(pod["tolerations"], [{"key": scaling.NAME, "operator": "Equal", "value": "true", "effect": "NoSchedule"}])
        lab.nodes(False)
        args, kwargs = self.runner.writes()[-1]
        self.assertIn(scaling.PROBE, args[2])
        self.assertEqual(json.loads(kwargs["input"])["preconditions"], {"uid": "test-uid", "resourceVersion": "2"})
        self.runner.objects[("deployment", scaling.PROBE)]["metadata"]["labels"] = {}
        for start in [True, False]:
            with self.assertRaises(ValueError):
                lab.nodes(start)

    def test_failure_never_echoes_subprocess_secret_output(self):
        lab = self.lab()
        lab.runner = lambda *args, **kwargs: SimpleNamespace(returncode=1, stdout="secret", stderr="secret")
        with self.assertRaisesRegex(RuntimeError, "suppressed") as failure:
            lab.call("create", body={"secret": "private"})
        self.assertNotIn("private", str(failure.exception))

    def test_status_is_read_only(self):
        with contextlib.redirect_stdout(io.StringIO()):
            self.lab().status()
        self.assertFalse(self.runner.writes())
        args = self.runner.calls[-1][0]
        self.assertEqual(args[-5:], ["list_queues", "name", "messages", "messages_ready", "messages_unacknowledged"])


if __name__ == "__main__":
    unittest.main()