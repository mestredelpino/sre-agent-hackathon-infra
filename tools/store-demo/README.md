# Store Demo Operations

Run these helpers from the authoritative infra repository root on Linux/WSL.
They use Python standard library only. Live monitoring requires Azure CLI;
scaling/fault operations require `kubectl` and separately approved permissions.
They do not provision infrastructure or authorize Terraform changes.
Deployment and recovery are covered in the
[AKS deployment guide](../../docs/WORKSHOP-DEPLOYMENT.md).

## Release Gate

These tools are published with the deployment guide. Revision
`33cf0ec1a9153f4b679d12da4f716c6f35133d5a` predates these tools.
Before attendees clone, the release owner must review and pin an approved
event-release revision containing `tools/store-demo` and the deployment guide.
Publication alone does not approve an event release. Do not construct helper
URLs at the old SHA. Keep fault material facilitator-only;
do not index the whole repository into attendee or investigator knowledge.

## Scoped Execution

Use only the approved disposable environment with a named recovery owner and
synthetic orders. Verify subscription, tenant, resource group and cluster against
the private environment record. Use a single dedicated normal-user kubeconfig,
not the default/merged config, `--admin` or disabled TLS verification:

```bash
export KUBECONFIG='<absolute-dedicated-kubeconfig-path>'
export EXPECTED_CONTEXT='<approved-context>'
export API_SERVER='<exact-approved-https-api-server-including-port-if-present>'
kubectl --context "$EXPECTED_CONTEXT" config view --minify \
  -o jsonpath='{.clusters[0].cluster.server}'
```

Compare the result exactly with `API_SERVER` and the approved Azure AKS FQDN.
Scaling/fault helpers require explicit context and exact API URL and refuse
mismatches before writes. Monitoring instead binds to the approved Grafana
endpoint and data-source/workspace IDs; it does not use this kubeconfig guard.
Normal-user credentials are not inherently read-only. Keep investigators
read-only and give facilitators only separately authorized write access.

## Managed Grafana Monitoring

Use [configure_monitoring.py](configure_monitoring.py) with an authenticated
Azure CLI identity authorized on the approved Managed Grafana instance. Verify
the Grafana identity's read access to managed Prometheus and Log Analytics,
collection readiness and ingestion first. Obtain the actual Prometheus data
source UID from that Grafana instance, not its display name or query endpoint.
The Log Analytics value is the full Azure resource ID, not its workspace GUID.

```bash
export GRAFANA_ENDPOINT='<https://approved-instance.grafana.azure.com>'
export PROMETHEUS_DATASOURCE_UID='<existing-approved-datasource-uid>'
export LOG_WORKSPACE_RESOURCE_ID='<approved-log-analytics-resource-id>'
python3 -B tools/store-demo/configure_monitoring.py \
  --grafana-endpoint "$GRAFANA_ENDPOINT" \
  --prometheus-datasource-uid "$PROMETHEUS_DATASOURCE_UID" \
  --log-workspace-resource-id "$LOG_WORKSPACE_RESOURCE_ID" --verify-only
```

These are all current CLI flags: the three required values above,
`--verify-only` and optional `--diagram-plugins` (plus `--help`). Verification
queries live data but does not publish a dashboard. After review and explicit
publication approval, repeat without `--verify-only`; it overwrites dashboard
UID `aks-store-monitoring`. It neither creates nor installs data sources.
`--diagram-plugins` requires the existing `grafana-graphviz-panel` plugin; the
helper checks installation and does not install it. Without that flag, no
optional plugin is needed.

Metrics/log queries failing or returning no live data are failures, not proof
of health. The helper uses `ContainerLog` joined to `KubePodInventory`; confirm
those tables actually receive Store data. Ready-pod graphs and configured
dependency links do not prove measured traffic, order completion, alert delivery
or external availability. Test the real receiver and agent intake separately.

## Scaling

Follow [SCALING.md](SCALING.md) for all commands, ownership checks, credentials,
bounded customer load, dedicated NAP probe and cleanup. The current infra
[deployment override](../../deployment_override.tf) declares managed KEDA,
NAP `Auto` with default pools `None`, and Cilium Overlay; verify the live CRDs,
controllers and approved target before `setup`. Do not run a new Terraform apply
to use these helpers. Stop and ask the platform owner if prerequisites differ.
Use `stop` and `nodes-stop`, then verify consolidation and residual costs;
neither command destroys the cluster or removes all scaling objects.

## Facilitator-Only Faults

The [fault report](FAULT-REPORT.md), [runner](faults.py), its tests and private
journal are facilitator-only, not attendee or investigator-agent material.
Read the complete report before planning a card. `list` is offline:

```bash
python3 -B tools/store-demo/faults.py list
```

Keep one private, nonsynced journal outside this repository per environment;
verify filesystem permissions/ACLs. One active fault, an exclusive operator,
healthy baseline, scope approval and recovery access are mandatory. Field guards,
UID/resource-version tests, ownership checks and Azure ETag checks must not be
bypassed. Azure writes are not atomic compare-and-swap. No fault is live-validated
by the offline suites; rehearse independently and retain the journal through
verified recovery and closure. Never restart ephemeral data services as a reset.

## Current HTTPS Baseline

The Store uses the Application Gateway public IP over HTTPS with a hostless AGIC
Ingress, `store-front-tls` and a cert-manager-issued self-signed certificate with
that exact IP SAN. No DNS entry, direct public Store LoadBalancer or HTTP install
path is part of this baseline. Keep admin, queue and database services private.
Trust only the verified public certificate; never distribute its private key or
use `curl -k`. Recheck HTTPS-only listeners, healthy backends, products/images and
a newly processed synthetic order from the attendee network. Reissue/reverify
the certificate if the gateway IP changes. See the
[deployment recovery checks](../../docs/WORKSHOP-DEPLOYMENT.md#recovery).

## Offline Tests

From the infra root, run each suite independently so one failure does not hide
the others. No Azure/Kubernetes calls are made:

```bash
python3 -B -W error -m unittest discover -s tools/store-demo -p test_faults.py -v
python3 -B -W error -m unittest discover -s tools/store-demo -p test_scaling.py -v
python3 -B -W error -m unittest discover -s tools/store-demo -p test_monitoring.py -v
```

Report failures faithfully. Offline success does not establish live deployment,
query compatibility, alert delivery, fault impact or end-to-end recovery.