import argparse
from contextlib import contextmanager
from copy import deepcopy
from datetime import datetime, timezone
import fcntl
import json
import os
from pathlib import Path
import re
import subprocess
import tempfile
from uuid import uuid4

from scaling import Lab, MANAGED, NAMESPACE, OWNER


WORKLOADS = {
    "deployment": ["store-front", "store-admin", "product-service", "order-service",
                   "makeline-service", "virtual-customer", "virtual-worker"],
    "statefulset": ["rabbitmq", "documentdb"],
}
CATALOG = {
    "service-selector": ("service", "store-front", "Remove storefront Service endpoints"),
    "service-port": ("service", "product-service", "Send product requests to the wrong port"),
    "image-pull": ("deployment", "product-service", "Stall a rollout with an invalid image"),
    "ingress-backend": ("ingress", "store-front", "Reference a nonexistent frontend Service"),
    "gateway-probe": ("ingress", "store-front", "Probe a nonexistent frontend path"),
    "tls-secret": ("ingress", "store-front", "Reference a nonexistent TLS Secret"),
    "readiness": ("deployment", "product-service", "Send readiness checks to a closed port"),
    "scheduling": ("deployment", "product-service", "Require a nonexistent node operating system"),
    "memory-limit": ("deployment", "product-service", "Constrain product memory to 1Mi"),
    "app-config": ("deployment", "order-service", "Reference a nonexistent application ConfigMap"),
    "queue-port": ("service", "rabbitmq", "Send new AMQP connections to a closed port"),
    "database-selector": ("service", "documentdb", "Remove database Service endpoints without restarting data"),
    "database-client": ("deployment", "makeline-service", "Point the database client at a nonexistent DNS name"),
    "worker-down": ("deployment", "virtual-worker", "Stop order completion workers without stopping intake"),
    "keda-auth": ("scaledobject", "lab-scaling", "Reference a nonexistent KEDA authentication object"),
    "keda-queue": ("scaledobject", "lab-scaling", "Read scaling demand from the wrong queue"),
    "nap-capacity": ("nodepool", "lab-scaling", "Limit dedicated NAP capacity below its smallest allowed node"),
    "azure-https-deny": ("azure-nsg", "approved-https-rule", "Deny the explicitly bound public HTTPS NSG rule"),
    "azure-alert-disable": ("azure-alert", "alert-appgw-unhealthy-host", "Disable gateway unhealthy-backend alert evaluation"),
}


def now():
    return datetime.now(timezone.utc).isoformat()


def value_at(obj, pointer):
    value = obj
    for token in pointer.lstrip("/").split("/"):
        token = token.replace("~1", "/").replace("~0", "~")
        value = value[int(token)] if isinstance(value, list) else value[token]
    return deepcopy(value)


def plan(card, obj):
    kind, name, description = CATALOG[card]
    container_path = "/spec/template/spec/containers/0"
    if kind == "deployment" and card != "worker-down":
        containers = value_at(obj, "/spec/template/spec/containers")
        if len(containers) != 1 or containers[0]["name"] != name:
            raise ValueError("Expected the pinned single application container")
    if kind in {"scaledobject", "nodepool"}:
        if obj["metadata"].get("labels", {}).get(MANAGED) != OWNER:
            raise ValueError("Only the dedicated scaling helper's owned objects are eligible")
    if card == "service-selector":
        selector = value_at(obj, "/spec/selector")
        if selector != {"app": "store-front"}:
            raise ValueError("Expected the pinned storefront selector")
        changes = [("/spec/selector", {"app": "sre-fault-no-match"})]
    elif card == "service-port":
        ports = value_at(obj, "/spec/ports")
        if len(ports) != 1 or ports[0]["port"] != 3002 or ports[0]["targetPort"] != 3002:
            raise ValueError("Expected the pinned product Service ports")
        changes = [("/spec/ports/0/targetPort", 3999)]
    elif card == "image-pull":
        changes = [("/spec/template/spec/containers/0/image",
                    "ghcr.io/azure-samples/aks-store-demo/product-service:sre-fault-nonexistent-20261109")]
    elif card in {"ingress-backend", "tls-secret", "gateway-probe"}:
        if (value_at(obj, "/metadata/annotations/kubernetes.io~1ingress.class") != "azure/application-gateway"
                or value_at(obj, "/spec/rules/0/http/paths/0/backend/service/name") != "store-front"
                or value_at(obj, "/spec/tls/0/secretName") != "store-front-tls"
                or len(obj["spec"]["rules"]) != 1 or len(obj["spec"]["tls"]) != 1
                or len(obj["spec"]["rules"][0]["http"]["paths"]) != 1
                or obj["spec"]["rules"][0].get("host")):
            raise ValueError("Expected the current IP-only Store HTTPS Ingress")
        if card == "ingress-backend":
            changes = [("/spec/rules/0/http/paths/0/backend/service/name", "sre-fault-missing-service")]
        elif card == "tls-secret":
            changes = [("/spec/tls/0/secretName", "sre-fault-missing-tls")]
        else:
            changes = [("/metadata/annotations/appgw.ingress.kubernetes.io~1health-probe-path", "/sre-fault-missing-health")]
    elif card == "readiness":
        changes = [(container_path + "/readinessProbe/httpGet/port", 3999)]
    elif card == "scheduling":
        changes = [("/spec/template/spec/nodeSelector/kubernetes.io~1os", "sre-fault-no-os")]
    elif card == "memory-limit":
        resources = value_at(obj, container_path + "/resources")
        resources["requests"]["memory"] = "1Mi"
        resources["limits"]["memory"] = "1Mi"
        changes = [(container_path + "/resources", resources)]
    elif card == "app-config":
        sources = value_at(obj, container_path + "/envFrom")
        index = next(index for index, source in enumerate(sources)
                     if source.get("configMapRef", {}).get("name") == "order-service-configs")
        changes = [(container_path + f"/envFrom/{index}/configMapRef/name", "sre-fault-missing-config")]
    elif card == "queue-port":
        ports = value_at(obj, "/spec/ports")
        index = next(index for index, port in enumerate(ports)
                     if port["port"] == 5672 and port["targetPort"] == 5672)
        changes = [(f"/spec/ports/{index}/targetPort", 5673)]
    elif card == "database-selector":
        if value_at(obj, "/spec/selector") != {"app": "documentdb"}:
            raise ValueError("Expected the pinned database Service selector")
        changes = [("/spec/selector", {"app": "sre-fault-no-match"})]
    elif card == "database-client":
        variables = value_at(obj, container_path + "/env")
        index = next(index for index, variable in enumerate(variables) if variable["name"] == "ORDER_DB_URI")
        original = variables[index]["value"]
        if not original.startswith("mongodb://documentdb:10260/"):
            raise ValueError("Expected the pinned database endpoint")
        changes = [(container_path + f"/env/{index}/value",
                    original.replace("mongodb://documentdb:", "mongodb://sre-fault.invalid:", 1))]
    elif card == "worker-down":
        if value_at(obj, "/spec/replicas") < 1:
            raise ValueError("Workers must be running before injection")
        changes = [("/spec/replicas", 0)]
    elif card in {"keda-auth", "keda-queue"}:
        if (value_at(obj, "/spec/scaleTargetRef/name") != "virtual-worker"
                or len(obj["spec"]["triggers"]) != 1
                or obj["spec"]["triggers"][0]["type"] != "rabbitmq"):
            raise ValueError("Expected the dedicated RabbitMQ worker scaler")
        changes = [("/spec/triggers/0/authenticationRef/name", "sre-fault-missing-auth")] if card == "keda-auth" else [
            ("/spec/triggers/0/metadata/queueName", "sre-fault-missing-queue")]
    elif card == "nap-capacity":
        requirements = value_at(obj, "/spec/template/spec/requirements")
        if not any(item == {"key": "karpenter.azure.com/sku-cpu", "operator": "In", "values": ["2", "4"]}
                   for item in requirements):
            raise ValueError("Expected the dedicated pool's 2/4 CPU restriction")
        changes = [("/spec/limits/cpu", "1")]
    elif card == "azure-https-deny":
        properties = obj["properties"]
        expected = {"access": "Allow", "direction": "Inbound", "protocol": "Tcp",
                    "sourceAddressPrefix": "Internet", "sourcePortRange": "*", "destinationPortRange": "443"}
        if any(properties.get(key) != value for key, value in expected.items()):
            raise ValueError("Only an existing Internet -> TCP/443 Allow rule is eligible")
        if any(properties.get(key) for key in ["sourceAddressPrefixes", "destinationAddressPrefixes",
                                               "sourcePortRanges", "destinationPortRanges",
                                               "sourceApplicationSecurityGroups", "destinationApplicationSecurityGroups"]):
            raise ValueError("Ambiguous NSG rule; plural overrides are not supported")
        changes = [("/properties/access", "Deny")]
    elif card == "azure-alert-disable":
        if obj["properties"].get("enabled") is not True:
            raise ValueError("The dedicated gateway alert must be enabled")
        changes = [("/properties/enabled", False)]
    else:
        raise ValueError("Unknown fault")
    edits = [{"path": path, "before": value_at(obj, path), "after": after}
             for path, after in changes]
    if any(edit["before"] == edit["after"] for edit in edits):
        raise ValueError("Fault value already present")
    return edits


class KubeDriver(Lab):
    def binding(self):
        namespace = self.get("namespace", NAMESPACE)
        if not namespace:
            raise ValueError("Store namespace is missing")
        config = json.loads(self.call("config", "view", "--minify", "-o", "json"))
        return {"command": self.command, "namespace_uid": namespace["metadata"]["uid"],
                "api_server": config["clusters"][0]["cluster"]["server"]}

    def healthy(self):
        for kind, names in WORKLOADS.items():
            for name in names:
                obj = self.get(kind, name)
                desired = obj["spec"].get("replicas", 1)
                status = obj.get("status", {})
                if (desired < 1 or status.get("observedGeneration", 0) < obj["metadata"]["generation"]
                        or status.get("readyReplicas", 0) != desired
                        or status.get("updatedReplicas", 0) != desired
                        or (kind == "statefulset" and status.get("currentRevision") != status.get("updateRevision"))):
                    raise ValueError("Unhealthy or unsettled baseline: " + kind + "/" + name)

    def prerequisites(self, card):
        if card == "worker-down":
            for kind in ["hpa", "scaledobject"]:
                objects = self.get(kind).get("items", [])
                if any(obj.get("spec", {}).get("scaleTargetRef", {}).get("name") == "virtual-worker" for obj in objects):
                    raise ValueError("worker-down requires no HPA/ScaledObject controlling virtual-worker")
        if card == "nap-capacity":
            for kind in ["nodes", "nodeclaims"]:
                if any(obj["metadata"].get("labels", {}).get("karpenter.sh/nodepool") == "lab-scaling"
                       for obj in self.get(kind).get("items", [])):
                    raise ValueError("Stop the NAP probe and wait for dedicated pool capacity to disappear first")

    def patch(self, kind, name, obj, edits, reverse=False, dry_run=False):
        patch = [{"op": "test", "path": "/metadata/uid", "value": obj["metadata"]["uid"]},
                 {"op": "test", "path": "/metadata/resourceVersion", "value": obj["metadata"]["resourceVersion"]}]
        for edit in edits:
            patch.append({"op": "test", "path": edit["path"],
                          "value": edit["after" if reverse else "before"]})
            patch.append({"op": "replace", "path": edit["path"],
                          "value": edit["before" if reverse else "after"]})
        args = ["patch", kind, name, *self.scope(kind), "--type=json", "-p", json.dumps(patch), "-o", "json"]
        if dry_run:
            args.append("--dry-run=server")
        self.call(*args)


class AzureDriver:
    def __init__(self, kube, aks_id, resource_id, runner=subprocess.run):
        pattern = r"/subscriptions/([0-9a-fA-F-]{36})/resourceGroups/([^/]+)/providers/([^/]+)/(.+)"
        aks = re.fullmatch(pattern, aks_id or "", re.IGNORECASE)
        target = re.fullmatch(pattern, resource_id or "", re.IGNORECASE)
        if (not aks or not target or aks.group(1).lower() != target.group(1).lower()
                or aks.group(2).lower() != target.group(2).lower()
                or aks.group(3).lower() != "microsoft.containerservice"
                or not re.fullmatch(r"managedClusters/[^/]+", aks.group(4), re.IGNORECASE)):
            raise ValueError("Exact AKS and fault resource IDs must belong to the same subscription and resource group")
        if (target.group(3).lower() == "microsoft.network"
                and re.fullmatch(r"networkSecurityGroups/[^/]+/securityRules/[^/]+", target.group(4), re.IGNORECASE)):
            self.kind = "azure-nsg"
        elif (target.group(3).lower() == "microsoft.insights"
              and target.group(4).lower() == "metricalerts/alert-appgw-unhealthy-host"):
            self.kind = "azure-alert"
        else:
            raise ValueError("Only a specific NSG rule or alert-appgw-unhealthy-host is supported")
        self.kube, self.aks_id, self.resource_id = kube, aks_id, resource_id
        self.runner, self.subscription = runner, aks.group(1)

    def az(self, *args):
        result = self.runner(["az", *args, "--subscription", self.subscription, "--only-show-errors", "-o", "json"],
                             text=True, capture_output=True)
        if result.returncode:
            raise RuntimeError("Azure operation failed; output suppressed")
        return json.loads(result.stdout) if result.stdout.strip() else {}

    def binding(self):
        result = self.kube.binding()
        cluster = self.az("resource", "show", "--ids", self.aks_id, "--api-version", "2024-10-01")
        properties = cluster["properties"]
        expected = json.loads(self.kube.call("config", "view", "--minify", "-o", "json"))["clusters"][0]["cluster"]["server"]
        hosts = [properties.get("fqdn"), properties.get("privateFQDN")]
        if (cluster.get("tags", {}).get("environment") != "hackathon"
                or expected not in {"https://" + host + suffix for host in hosts if host for suffix in ["", ":443"]}):
            raise ValueError("Azure AKS identity, hackathon tag or endpoint mismatch")
        return dict(result, aks_id=self.aks_id, azure_resource_id=self.resource_id)

    def healthy(self):
        self.kube.healthy()

    def prerequisites(self, card):
        if CATALOG[card][0] != self.kind:
            raise ValueError("Azure resource type does not match the fault")
        self.binding()

    def get(self, kind, name):
        if kind != self.kind:
            raise ValueError("Unexpected Azure resource type")
        version = "2024-05-01" if kind == "azure-nsg" else "2018-03-01"
        obj = self.az("resource", "show", "--ids", self.resource_id, "--api-version", version)
        if not obj.get("etag"):
            raise ValueError("Azure ETag is required for drift detection")
        obj["metadata"] = {"uid": obj["id"], "resourceVersion": obj["etag"]}
        return obj

    def patch(self, kind, name, obj, edits, reverse=False, dry_run=False):
        current = self.get(kind, name)
        if current["metadata"] != obj["metadata"]:
            raise ValueError("Azure resource changed; STOP")
        for edit in edits:
            if value_at(current, edit["path"]) != edit["after" if reverse else "before"]:
                raise ValueError("Azure field changed; STOP")
        if dry_run:
            return
        desired = edits[0]["before" if reverse else "after"]
        if kind == "azure-nsg":
            self.az("network", "nsg", "rule", "update", "--ids", self.resource_id, "--access", desired)
        else:
            self.az("monitor", "metrics", "alert", "update", "--ids", self.resource_id,
                    "--enabled", "true" if desired else "false")
        fresh = self.get(kind, name)
        if any(value_at(fresh, edit["path"]) != edit["before" if reverse else "after"] for edit in edits):
            raise ValueError("Azure field acceptance not confirmed")


class Journal:
    def __init__(self, directory):
        self.directory = Path(directory).expanduser().absolute()
        repo = Path(__file__).resolve().parents[2]
        if self.directory.is_symlink() or self.directory.resolve().is_relative_to(repo):
            raise ValueError("Keep the private journal outside the workshop repository; no symlink directory")
        self.directory.mkdir(mode=0o700, parents=True, exist_ok=True)
        self.path = self.directory / "active.json"

    @contextmanager
    def lock(self):
        descriptor = os.open(self.directory / ".lock", os.O_CREAT | os.O_RDWR | os.O_NOFOLLOW, 0o600)
        with os.fdopen(descriptor, "w") as handle:
            try:
                fcntl.flock(handle, fcntl.LOCK_EX | fcntl.LOCK_NB)
            except BlockingIOError:
                raise ValueError("Another fault operation holds this journal lock") from None
            yield

    def read(self):
        if self.path.is_symlink():
            raise ValueError("Refusing symlink journal")
        return json.loads(self.path.read_text()) if self.path.exists() else None

    def write(self, entry):
        if self.path.is_symlink():
            raise ValueError("Refusing symlink journal")
        descriptor, temporary = tempfile.mkstemp(dir=self.directory)
        try:
            with os.fdopen(descriptor, "w") as handle:
                json.dump(entry, handle, indent=2)
                handle.flush()
                os.fsync(handle.fileno())
            os.replace(temporary, self.path)
            directory_fd = os.open(self.directory, os.O_RDONLY | os.O_DIRECTORY)
            try:
                os.fsync(directory_fd)
            finally:
                os.close(directory_fd)
        finally:
            if os.path.exists(temporary):
                os.unlink(temporary)

    def archive(self, entry):
        with (self.directory / (entry["run_id"] + ".json")).open("x") as handle:
            json.dump(entry, handle, indent=2)


class FaultRunner:
    def __init__(self, driver, journal):
        self.driver = driver
        self.journal = journal

    def preview(self, card):
        self.driver.prerequisites(card)
        kind, name, description = CATALOG[card]
        obj = self.driver.get(kind, name)
        if not obj:
            raise ValueError("Missing prerequisite: " + kind + "/" + name)
        edits = plan(card, obj)
        return obj, {"fault": card, "kind": kind, "name": name, "description": description,
                     "uid": obj["metadata"]["uid"], "edits": edits}

    def inject(self, card, evidence):
        previous = self.journal.read()
        if previous and previous["phase"] != "closed":
            raise ValueError("An incident is open; revert and verify recovery before closing it")
        self.driver.healthy()
        obj, entry = self.preview(card)
        self.driver.patch(entry["kind"], entry["name"], obj, entry["edits"], dry_run=True)
        entry.update(binding=self.driver.binding(), run_id=str(uuid4()), phase="prepared",
                     created_utc=now(), baseline_evidence=evidence)
        self.journal.write(entry)
        self.driver.patch(entry["kind"], entry["name"], obj, entry["edits"])
        if self.inspect()[2] != "fault":
            raise ValueError("Fault field acceptance not confirmed; keep prepared journal for recovery")
        entry.update(phase="applied", accepted_utc=now())
        self.journal.write(entry)
        return entry

    def inspect(self):
        entry = self.journal.read()
        if not entry:
            raise ValueError("No recorded incident")
        if entry["binding"] != self.driver.binding():
            raise ValueError("Journal target/context/namespace mismatch")
        obj = self.driver.get(entry["kind"], entry["name"])
        if not obj or obj["metadata"]["uid"] != entry["uid"]:
            raise ValueError("Target was deleted or recreated; STOP for owner review")
        before = all(value_at(obj, edit["path"]) == edit["before"] for edit in entry["edits"])
        after = all(value_at(obj, edit["path"]) == edit["after"] for edit in entry["edits"])
        return obj, entry, "baseline" if before else "fault" if after else "drift"

    def revert(self):
        obj, entry, state = self.inspect()
        if entry["phase"] == "closed":
            raise ValueError("Incident is already closed")
        if state == "drift":
            raise ValueError("Controlled fields changed unexpectedly; STOP, do not overwrite")
        if state == "fault":
            self.driver.patch(entry["kind"], entry["name"], obj, entry["edits"], reverse=True, dry_run=True)
            self.driver.patch(entry["kind"], entry["name"], obj, entry["edits"], reverse=True)
        if self.inspect()[2] != "baseline":
            raise ValueError("Restore not confirmed; keep incident open")
        entry.update(phase="reverted", restored_utc=now())
        self.journal.write(entry)
        return entry

    def close(self, evidence):
        obj, entry, state = self.inspect()
        if entry["phase"] != "reverted" or state != "baseline":
            raise ValueError("Run revert and independently verify recovery before closing")
        self.driver.healthy()
        entry.update(phase="closed", closed_utc=now(), recovery_evidence=evidence)
        self.journal.archive(entry)
        self.journal.write(entry)
        return entry


def main(argv=None):
    parser = argparse.ArgumentParser(description="Facilitator-only, reversible AKS Store faults; no live writes by default")
    parser.add_argument("action", choices=["list", "plan", "inject", "status", "revert", "close"])
    parser.add_argument("--fault", choices=sorted(CATALOG))
    parser.add_argument("--context")
    parser.add_argument("--api-server")
    parser.add_argument("--state-dir")
    parser.add_argument("--aks-resource-id", help="Required for Azure faults: exact lab AKS resource ID")
    parser.add_argument("--azure-resource-id", help="Required for Azure faults: exact approved alert or NSG rule ID")
    parser.add_argument("--confirm-azure-resource-id")
    parser.add_argument("--confirm-namespace")
    parser.add_argument("--baseline-verified", action="store_true")
    parser.add_argument("--recovery-verified", action="store_true")
    parser.add_argument("--evidence", help="Private evidence reference, never credentials")
    args = parser.parse_args(argv)
    if args.action == "list":
        for card, (kind, name, description) in CATALOG.items():
            print(f"{card}: {kind}/{name}: {description}")
        return
    if not all([args.context, args.api_server, args.state_dir]):
        parser.error("--context, --api-server and --state-dir are required")
    if args.action in {"plan", "inject"} and not args.fault:
        parser.error("--fault is required")
    if args.action in {"inject", "revert", "close"} and args.confirm_namespace != NAMESPACE:
        parser.error("Writes require --confirm-namespace aks-store-demo")
    if args.action == "inject" and not (args.baseline_verified and args.evidence):
        parser.error("Injection requires --baseline-verified and --evidence")
    if args.action == "close" and not (args.recovery_verified and args.evidence):
        parser.error("Closure requires --recovery-verified and --evidence")
    azure = bool(args.azure_resource_id or args.aks_resource_id or (args.fault or "").startswith("azure-"))
    if azure and not (args.azure_resource_id and args.aks_resource_id):
        parser.error("Azure faults require both --aks-resource-id and --azure-resource-id")
    if azure and args.action in {"inject", "revert", "close"} and args.confirm_azure_resource_id != args.azure_resource_id:
        parser.error("Azure writes require --confirm-azure-resource-id matching --azure-resource-id exactly")
    try:
        os.umask(0o077)
        driver = KubeDriver(args.context, args.api_server)
        if azure:
            driver = AzureDriver(driver, args.aks_resource_id, args.azure_resource_id)
        runner = FaultRunner(driver, Journal(args.state_dir))
        with runner.journal.lock():
            if args.action == "plan":
                result = runner.preview(args.fault)[1]
            elif args.action == "inject":
                result = runner.inject(args.fault, args.evidence)
            elif args.action == "status":
                obj, entry, state = runner.inspect()
                result = {"fault": entry["fault"], "phase": entry["phase"], "field_state": state}
            elif args.action == "revert":
                result = runner.revert()
            else:
                result = runner.close(args.evidence)
            print(json.dumps(result, indent=2))
            if args.action in {"inject", "revert"}:
                print("Field acceptance only. Independently verify impact/recovery; incident remains open.")
    except (ValueError, RuntimeError, KeyError, IndexError, StopIteration, TypeError, OSError, subprocess.SubprocessError):
        parser.exit(1, "Fault operation refused or failed. Check prerequisites, target, private journal and drift. "
                       "No automatic rollback was attempted; use status then revert with the same journal. "
                       "Command output is suppressed to protect credentials.\n")


if __name__ == "__main__":
    main()