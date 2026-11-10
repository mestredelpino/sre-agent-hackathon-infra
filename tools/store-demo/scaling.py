import argparse
import base64
import json
import os
from pathlib import Path
import re
import subprocess
from urllib.parse import urlparse


NAMESPACE = "aks-store-demo"
NAME = "lab-scaling"
PROBE = "nap-scaling-probe"
MANAGED = "app.kubernetes.io/managed-by"
OWNER = "sre-scaling-lab"
BASELINE = "sre-scaling-lab/original-customers"
CLUSTER_KINDS = {"nodepool", "aksnodeclass", "nodes", "nodeclaims", "crd"}


def resource(api, kind, name, spec=None):
    result = {"apiVersion": api, "kind": kind,
              "metadata": {"name": name, "labels": {MANAGED: OWNER}}}
    if kind not in {"NodePool", "AKSNodeClass"}:
        result["metadata"]["namespace"] = NAMESPACE
    if spec is not None:
        result["spec"] = spec
    return result


class Lab:
    def __init__(self, context, api_server, runner=subprocess.run):
        self.runner = runner
        kubeconfig = os.environ.get("KUBECONFIG", "")
        parsed = urlparse(api_server)
        if (not context or parsed.scheme != "https" or not parsed.hostname
                or parsed.username or parsed.password or parsed.query or parsed.fragment
                or parsed.path not in {"", "/"}):
            raise ValueError("An explicit context and HTTPS API server are required")
        path = Path(kubeconfig).expanduser()
        if (not kubeconfig or os.pathsep in kubeconfig or not path.is_file()
                or path.resolve() == (Path.home() / ".kube/config").resolve()):
            raise ValueError("KUBECONFIG must name one dedicated file, not ~/.kube/config")
        self.command = ["kubectl", "--kubeconfig", str(path), "--context", context]
        config = json.loads(self.call("config", "view", "--minify", "-o", "json"))
        clusters, contexts = config.get("clusters", []), config.get("contexts", [])
        if (len(clusters) != 1 or len(contexts) != 1
                or contexts[0].get("name") != context
                or contexts[0]["context"].get("cluster") != clusters[0].get("name")
                or clusters[0]["cluster"].get("server") != api_server
                or clusters[0]["cluster"].get("insecure-skip-tls-verify")):
            raise ValueError("Kubeconfig context/API server mismatch or insecure TLS")

    def call(self, *args, body=None):
        result = self.runner(self.command + list(args), input=json.dumps(body) if body is not None else None,
                             text=True, capture_output=True)
        if result.returncode:
            raise RuntimeError("kubectl operation failed; output suppressed to protect credentials")
        return result.stdout

    def scope(self, kind):
        return [] if kind in CLUSTER_KINDS else ["--namespace", NAMESPACE]

    def get(self, kind, name=None):
        args = ["get", kind] + ([name, "--ignore-not-found"] if name else [])
        output = self.call(*args, *self.scope(kind), "-o", "json")
        return json.loads(output) if output.strip() else None

    def owned(self, kind, name):
        existing = self.get(kind, name)
        if existing and existing.get("metadata", {}).get("labels", {}).get(MANAGED) != OWNER:
            raise ValueError("Refusing unowned object: " + kind + "/" + name)
        return existing

    def put(self, obj):
        kind, name = obj["kind"].lower(), obj["metadata"]["name"]
        existing = self.owned(kind, name)
        if existing:
            obj["metadata"]["resourceVersion"] = existing["metadata"]["resourceVersion"]
        self.call("replace" if existing else "create", "-f", "-", *self.scope(kind), body=obj)

    def credentials(self):
        values = [os.environ.get("RABBITMQ_USERNAME"), os.environ.get("RABBITMQ_PASSWORD")]
        if any(value is not None for value in values):
            if not all(values):
                raise ValueError("Set both RABBITMQ_USERNAME and RABBITMQ_PASSWORD")
            return values
        config = self.get("configmap", "rabbitmq") or {}
        text = config.get("data", {}).get("rabbitmq.conf", "")
        if text:
            lines = [line for line in text.splitlines() if re.match(r"\s*default_(user|pass)\s*=", line)]
            matches = [re.fullmatch(r"\s*(default_user|default_pass)\s*=\s*([^\s#]+)\s*", line) for line in lines]
            entries = [match.groups() for match in matches if match]
            found = dict(entries)
            if (len(lines) == len(entries) == 2 and len(found) == 2
                    and not any(token in value for value in found.values() for token in ('$', '"', "'", "\\"))):
                return [found["default_user"], found["default_pass"]]
            raise ValueError("Complex RabbitMQ config; set paired RABBITMQ environment variables")
        deployment = self.get("deployment", "order-service") or {}
        containers = deployment.get("spec", {}).get("template", {}).get("spec", {}).get("containers", [])
        container = next((item for item in containers if item.get("name") == "order-service"), {})
        env = {}
        for source in container.get("envFrom", []):
            for ref, kind in [("secretRef", "secret"), ("configMapRef", "configmap")]:
                if ref in source:
                    obj = self.get(kind, source[ref]["name"]) or {}
                    data = obj.get("data", {})
                    env.update({source.get("prefix", "") + key: base64.b64decode(value, validate=True).decode()
                                if kind == "secret" else value for key, value in data.items()})
        for entry in container.get("env", []):
            value = entry.get("value")
            for ref, kind in [("secretKeyRef", "secret"), ("configMapKeyRef", "configmap")]:
                target = entry.get("valueFrom", {}).get(ref)
                if target:
                    value = self.get(kind, target["name"])["data"][target["key"]]
                    if kind == "secret":
                        value = base64.b64decode(value, validate=True).decode()
            env[entry["name"]] = value
        values = [env.get("ORDER_QUEUE_USERNAME"), env.get("ORDER_QUEUE_PASSWORD")]
        if not all(values):
            raise ValueError("Cannot resolve credentials; set paired RABBITMQ environment variables")
        return values

    def conflicts(self):
        for kind, field in [("hpa", "scaleTargetRef"), ("scaledobject", "scaleTargetRef")]:
            for obj in self.get(kind).get("items", []):
                target = obj.get("spec", {}).get(field, {})
                if target.get("name") != "virtual-worker":
                    continue
                if kind == "scaledobject" and obj["metadata"]["name"] == NAME and obj["metadata"].get("labels", {}).get(MANAGED) == OWNER:
                    continue
                owners = obj["metadata"].get("ownerReferences", [])
                own = self.owned("scaledobject", NAME) if kind == "hpa" else None
                if own and any(ref.get("uid") == own["metadata"].get("uid") and ref.get("kind") == "ScaledObject" and ref.get("controller") for ref in owners):
                    continue
                raise ValueError("Competing autoscaler for virtual-worker")

    def setup(self):
        for crd in ["scaledobjects.keda.sh", "triggerauthentications.keda.sh", "nodepools.karpenter.sh", "nodeclaims.karpenter.sh", "aksnodeclasses.karpenter.azure.com"]:
            if not self.get("crd", crd):
                raise ValueError("Missing CRD: " + crd)
        for name in ["virtual-worker", "virtual-customer"]:
            if not self.get("deployment", name):
                raise ValueError("Missing deployment: " + name)
        self.conflicts()
        user, password = self.credentials()
        secret = resource("v1", "Secret", NAME)
        secret.update(type="Opaque", stringData={"username": user, "password": password})
        auth = resource("keda.sh/v1alpha1", "TriggerAuthentication", NAME, {"secretTargetRef": [
            {"parameter": key, "name": NAME, "key": key} for key in ["username", "password"]]})
        scaled = resource("keda.sh/v1alpha1", "ScaledObject", NAME, {
            "scaleTargetRef": {"name": "virtual-worker"}, "pollingInterval": 15, "cooldownPeriod": 60,
            "minReplicaCount": 1, "maxReplicaCount": 12,
            "advanced": {"horizontalPodAutoscalerConfig": {"behavior": {"scaleDown": {"stabilizationWindowSeconds": 60}}}},
            "triggers": [{"type": "rabbitmq", "metadata": {
                "host": "http://rabbitmq.aks-store-demo.svc.cluster.local:15672", "protocol": "http",
                "queueName": "orders", "mode": "QueueLength", "value": "5", "vhostName": "/"},
                "authenticationRef": {"name": NAME}}]})
        nodeclass = resource("karpenter.azure.com/v1beta1", "AKSNodeClass", NAME, {"imageFamily": "Ubuntu"})
        pool = resource("karpenter.sh/v1", "NodePool", NAME, {
            "limits": {"cpu": "8", "memory": "32Gi"},
            "disruption": {"consolidationPolicy": "WhenEmptyOrUnderutilized", "consolidateAfter": "60s"},
            "template": {"metadata": {"labels": {"kubernetes.azure.com/mode": "user", "kubernetes.azure.com/ebpf-dataplane": "cilium"}},
                         "spec": {"nodeClassRef": {"group": "karpenter.azure.com", "kind": "AKSNodeClass", "name": NAME},
                                  "taints": [{"key": NAME, "value": "true", "effect": "NoSchedule"}],
                                  "startupTaints": [{"key": "node.cilium.io/agent-not-ready", "value": "true", "effect": "NoExecute"}],
                                  "requirements": [{"key": key, "operator": "In", "values": values} for key, values in [
                                      ("kubernetes.io/arch", ["amd64"]), ("kubernetes.io/os", ["linux"]),
                                      ("karpenter.sh/capacity-type", ["on-demand"]), ("karpenter.azure.com/sku-family", ["D"]),
                                      ("karpenter.azure.com/sku-cpu", ["2", "4"])]]}}})
        objects = [secret, auth, scaled, nodeclass, pool]
        for obj in objects:
            self.owned(obj["kind"].lower(), obj["metadata"]["name"])
        for obj in objects:
            self.put(obj)

    def customers(self, start, count=4):
        if not 1 <= count <= 12:
            raise ValueError("Customers must be in 1..12")
        obj = self.get("deployment", "virtual-customer")
        annotations = obj["metadata"].get("annotations", {})
        baseline = annotations.get(BASELINE)
        if baseline is not None and (not baseline.isdecimal() or int(baseline) > 2147483647):
            raise ValueError("Invalid saved customer baseline")
        if start and baseline is None:
            annotations = dict(annotations, **{BASELINE: str(obj["spec"].get("replicas", 1))})
            self.call("patch", "deployment", "virtual-customer", *self.scope("deployment"), "--type=json", "-p", json.dumps([
                {"op": "test", "path": "/metadata/resourceVersion", "value": obj["metadata"]["resourceVersion"]},
                {"op": "add", "path": "/metadata/annotations", "value": annotations}]))
        if start or baseline is not None:
            self.call("scale", "deployment", "virtual-customer", *self.scope("deployment"),
                      "--replicas=" + str(count if start else int(baseline)))

    def nodes(self, start):
        existing = self.owned("deployment", PROBE)
        if not start:
            if existing:
                self.call("delete", "--raw", "/apis/apps/v1/namespaces/" + NAMESPACE + "/deployments/" + PROBE, "-f", "-",
                          body={"apiVersion": "v1", "kind": "DeleteOptions", "preconditions": {
                              "uid": existing["metadata"]["uid"], "resourceVersion": existing["metadata"]["resourceVersion"]}})
            return
        for kind in ["nodepool", "aksnodeclass"]:
            if not self.owned(kind, NAME):
                raise ValueError("Run setup before nodes-start")
        self.put(resource("apps/v1", "Deployment", PROBE, {
            "replicas": 2, "selector": {"matchLabels": {"app": PROBE}},
            "template": {"metadata": {"labels": {"app": PROBE, MANAGED: OWNER}}, "spec": {
                "nodeSelector": {"karpenter.sh/nodepool": NAME},
                "tolerations": [{"key": NAME, "operator": "Equal", "value": "true", "effect": "NoSchedule"}],
                "containers": [{"name": "pause", "image": "registry.k8s.io/pause:3.10", "resources": {
                    "requests": {"cpu": "500m", "memory": "128Mi"}, "limits": {"cpu": "1", "memory": "256Mi"}}}]}}}))

    def status(self):
        for kind in ["deployments", "hpa", "scaledobject", "nodes", "nodeclaims"]:
            print(self.call("get", kind, *self.scope(kind), "-o", "wide"), end="")
        print(self.call("exec", "rabbitmq-0", "--namespace", NAMESPACE, "--", "rabbitmqctl",
                        "list_queues", "name", "messages", "messages_ready", "messages_unacknowledged"), end="")


def main(argv=None):
    parser = argparse.ArgumentParser(description="Explicit-target AKS Store KEDA/NAP lab")
    parser.add_argument("action", choices=["setup", "start", "stop", "status", "nodes-start", "nodes-stop"])
    parser.add_argument("--context", required=True)
    parser.add_argument("--api-server", required=True)
    parser.add_argument("--customers", type=int, choices=range(1, 13), default=4)
    args = parser.parse_args(argv)
    try:
        lab = Lab(args.context, args.api_server)
        if args.action in {"start", "stop"}:
            lab.customers(args.action == "start", args.customers)
        elif args.action.startswith("nodes-"):
            lab.nodes(args.action == "nodes-start")
        else:
            getattr(lab, args.action)()
    except (ValueError, RuntimeError, KeyError, TypeError, OSError):
        parser.exit(1, "Scaling action failed; check target, ownership, prerequisites and credential configuration. No secret output is shown.\n")


if __name__ == "__main__":
    main()