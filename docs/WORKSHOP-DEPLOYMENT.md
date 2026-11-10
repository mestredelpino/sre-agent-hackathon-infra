# AKS Workshop Deployment And Recovery

This is the AKS-only infra deployment, recovery and cleanup runbook. All commands
run from the authoritative infra root and use its `scripts/` and `cluster/`
sources. There is no VM lane. The Store tools are documented in
[tools/store-demo](../tools/store-demo/README.md).

## Release And State Boundaries

The helpers and this guide are published together. The original pin
`33cf0ec1a9153f4b679d12da4f716c6f35133d5a` does not contain these tools.
Before attendees clone, the release owner must review and approve an
event-release revision containing the tools and this guide, check
its platform baseline, and give attendees that exact revision. Do not advertise
old-SHA helper URLs or clone an unspecified branch as an approved release.

For fresh team deployments, use a separate nonsynced checkout, protected inputs
and state, and an approved existing empty resource group. Verify subscription,
tenant, region, quota, unique prefix, policy, budget, expiry and cleanup owner.
Never aim fresh state at existing resources. The committed attendee baseline
reads the existing resource group and does not destroy it; verify that behavior
in the event release and its reviewed plan.

For the existing operator deployment, use its original authoritative directory,
state and protected variable files. This local checkout has pre-existing
overrides, including resource-group ownership and gateway differences. Do not
copy attendee configuration over it, initialize another state against its names,
or assume attendee cleanup semantics apply. Compatibility symlinks are not
independent deployments. Coordinate a single state owner and preserve backups.
State, plans, PFX/private keys and kubeconfig are sensitive; ignore rules do not
encrypt them. Do not publish or index them into agent knowledge.

## Deploy Kubernetes Infrastructure And Application

Install Terraform 1.7+, Azure CLI, Bash, jq, curl, OpenSSL, kubectl and Python 3.9+.
Provider registration requires subscription-owner approval. Use separately
approved deployment permissions (Contributor and Role Based Access Control
Administrator at the assigned group as appropriate), read-only investigator
access, and facilitator recovery credentials. Cluster-wide cert-manager changes
need explicit approval. Gateway, AKS, NAT, telemetry and other resources incur
costs while idle; alerts are not spending caps.

In a new per-team directory only, clone the approved event release:

```bash
set -euo pipefail
: "${INFRA_DIR:?New absolute nonsynced checkout path}"
: "${EVENT_RELEASE_REVISION:?Approved published revision containing the migrated tools}"
test ! -e "$INFRA_DIR"
git clone https://github.com/mestredelpino/sre-agent-hackathon-infra.git "$INFRA_DIR"
git -C "$INFRA_DIR" checkout --detach "$EVENT_RELEASE_REVISION"
cd "$INFRA_DIR"
test -f tools/store-demo/configure_monitoring.py
test -f docs/WORKSHOP-DEPLOYMENT.md
```

An existing operator instead starts in the original root, without cloning or
changing state. Set `SUBSCRIPTION_ID`, `AKS_GROUP` and `WORKSHOP_VARS` from the
approved private record; the latter is an absolute protected input-file path.
For a fresh deployment, populate it from the reviewed example with approved
subscription, existing group, region, unique prefix, receiver and optional
Grafana administrator group. Keep `allow_store_front_http = false`. Verify:

```bash
az account show --subscription "$SUBSCRIPTION_ID" \
  --query '{id:id,tenantId:tenantId,name:name}' -o json
test "$(az account show --query id -o tsv)" = "$SUBSCRIPTION_ID"
az group show --subscription "$SUBSCRIPTION_ID" -n "$AKS_GROUP" \
  --query '{id:id,location:location}' -o json
```

For a fresh deployment with no `.certs/`, the bootstrap script creates a
hostname-SAN certificate and sensitive PFX material for initial gateway setup:

```bash
bash scripts/create-lab-certificate.sh sre.example.test
```

Do not run it over existing certificates; it refuses overwrite. This bootstrap
certificate is not the Store's IP-SAN certificate. Preserve the existing
operator's inputs/certificates during recovery. Use the original backend/state:

```bash
terraform init
terraform validate
terraform plan -var-file="$WORKSHOP_VARS" -out=deployment.tfplan
terraform show deployment.tfplan
```

STOP for explicit approval of the complete plan: resource scope/ownership,
networking, identity grants, costs and any replacement/deletion. Confirm the
effective platform includes managed KEDA, NAP `Auto` with pools `None`, Cilium
Overlay, and two system nodes as declared in
[deployment_override.tf](../deployment_override.tf). Never use a legacy scenario
feature toggle. An incompatible existing cluster is not an implicit rebuild
authorization. Do not replay old saved plans or apply unrelated drift.

Only after that approval, execute the reviewed saved plan:

```bash
terraform apply deployment.tfplan
```

Obtain normal-user access into a new dedicated file, not a merged/default config:

```bash
export AKS_NAME="$(terraform output -raw aks_name)"
: "${KUBECONFIG:?New absolute dedicated kubeconfig path}"
export KUBECONFIG
test ! -e "$KUBECONFIG"
az aks get-credentials --subscription "$SUBSCRIPTION_ID" \
  -g "$AKS_GROUP" -n "$AKS_NAME" --file "$KUBECONFIG"
export EXPECTED_CONTEXT="$(kubectl config current-context)"
export AKS_FQDN="$(az aks show --subscription "$SUBSCRIPTION_ID" \
  -g "$AKS_GROUP" -n "$AKS_NAME" --query fqdn -o tsv)"
export API_SERVER="$(kubectl --context "$EXPECTED_CONTEXT" config view --minify \
  -o jsonpath='{.clusters[0].cluster.server}')"
[[ "$API_SERVER" == "https://$AKS_FQDN" || "$API_SERVER" == "https://$AKS_FQDN:443" ]]
export APPGW_PUBLIC_IP="$(terraform output -raw appgw_public_ip)"
kubectl --context "$EXPECTED_CONTEXT" get nodes
kubectl --context "$EXPECTED_CONTEXT" get crd \
  scaledobjects.keda.sh nodepools.karpenter.sh aksnodeclasses.karpenter.azure.com
```

Independently compare context, API, cluster and gateway IP with the approved
environment record. STOP on mismatch/denial; never fall back to `--admin`.
Existing recovery access must be reverified rather than overwritten. Verify
AGIC identity grants, controller health, managed metrics/log collection and
KEDA/NAP readiness; CRD existence alone does not establish controller health.

Review the pinned cert-manager `v1.21.3` manifest and
[cluster/cert-manager](../cluster/cert-manager), then separately approve its
cluster-wide installation and the Store namespace/route changes:

```bash
bash scripts/install-cert-manager.sh --apply
bash scripts/deploy-store-demo.sh --apply
```

The scripts enforce the expected API endpoint and server dry-runs before their
applies. Review [cluster/store-demo](../cluster/store-demo), including its pinned
upstream source, before execution. If optional `sre-web` ingress already exists,
the Store script refuses route takeover: only after owner review use
`--replace-sample-ingress`, which removes that ingress. It is not auto-restored.

The Store baseline is hostless HTTPS at the Application Gateway IP with
`store-front-tls`, issued by cert-manager using a self-signed IP-SAN certificate.
No DNS entry is required. This is not publicly trusted TLS. Export only the
public certificate into approved protected evidence storage and verify its
fingerprint through a trusted channel before distributing it:

```bash
: "${STORE_PUBLIC_CERT:?Approved absolute path for the public certificate}"
umask 077
kubectl --context "$EXPECTED_CONTEXT" -n aks-store-demo get secret store-front-tls \
  -o jsonpath='{.data.tls\.crt}' | base64 --decode > "$STORE_PUBLIC_CERT"
openssl x509 -in "$STORE_PUBLIC_CERT" -noout -fingerprint -sha256 -ext subjectAltName
openssl verify -CAfile "$STORE_PUBLIC_CERT" \
  -verify_ip "$APPGW_PUBLIC_IP" "$STORE_PUBLIC_CERT"
curl --fail --max-time 30 --cacert "$STORE_PUBLIC_CERT" "https://$APPGW_PUBLIC_IP/"
```

Never export `tls.key`, dump the Secret, or use `curl -k`. Confirm HTTPS-only
gateway listeners, healthy backends, all nine settled Ready Store workloads,
products/images and successful fresh synthetic order processing from the attendee
network. Keep admin, database and queue services private. Direct public HTTP
Store installation is not an alternative. Check alert receiver delivery and
agent intake under their actual identities before accepting a fault baseline.

## Optional Nginx Fault Baseline

[workload.yaml](../workload.yaml) is the separately approved
`sre-hackathon/sre-web` nginx/AGIC target, not a Store dependency. Review its
namespace/resources and server dry-run against the verified API before applying.
It uses hostname `sre.example.test` and the installed bootstrap certificate.
Verify two Ready replicas, EndpointSlices, healthy gateway backends and HTTPS
with `.certs/appgw.crt` and a hostname mapping. Store's hostless catch-all must
not conflict: only an owner-approved remove/restore of the appropriate ingress
may switch targets. Nginx fault helpers do not target Store or restore removed
ingresses. Do not expose HTTP or remove gateway ignore rules to pass a check.

## Managed Grafana Monitoring

Follow the [tools monitoring procedure](../tools/store-demo/README.md#managed-grafana-monitoring)
for the current CLI flags, approved endpoint/data-source/workspace binding,
`--verify-only` queries and separately approved dashboard publication. Verify
ingestion, actual data and permissions before relying on panels. Missing data,
receivers or agent incident access remain explicit gaps, not detection evidence.

## Recovery

Stop further mutation, preserve original state/journals and establish a named
recovery owner. For an active Store fault, follow the
[facilitator-only report](../tools/store-demo/FAULT-REPORT.md) using the same
bindings and private journal: status, field-scoped revert, independent recovery
proof, then close. Never delete a journal to bypass an active fault or use bulk
Terraform/manifests as a generic reset. Unexpected drift, recreated objects or
lost recovery access require owner review, not force takeover.

For platform recovery, inspect a fresh plan in the original state with original
protected inputs; approve only the intended recovery. Rebuilds require explicit
replacement approval and app redeployment, not new state or implicit applies.
Reverify the new AGIC identity/grants, context/API, gateway IP, certificate IP SAN,
HTTPS listeners/backends, metrics/logs, receiver and fresh processed order. If
the IP changes, reissue/reverify the Store certificate via the reviewed script.
Never infer recovery from Ready pods or gateway probes alone. Ephemeral RabbitMQ
and DocumentDB data has no guaranteed backup/persistence; catalog availability
does not prove historical order recovery. Historical operator notes in
[RECOVERY.md](../RECOVERY.md) describe an earlier HTTP path, not this HTTPS baseline.

## Cleanup

Stop approved load/probes using the [scaling cleanup](../tools/store-demo/SCALING.md#costs-and-cleanup),
verify consolidation and residual nodes/claims, and revert/verify/close any active
fault before disposal. Stop local port-forwards. Do not delete unrelated pools,
namespaces or shared infrastructure. Full cleanup is a separate approved action
in the original authoritative state with its original inputs:

```bash
terraform plan -destroy -var-file="$WORKSHOP_VARS" -out=cleanup.tfplan
terraform show cleanup.tfplan
```

STOP for owner review of every deletion, retained resource and cost. In the
committed attendee baseline the existing resource group is not owned; in a local
operator override it may be, so reject unexpected group/shared-resource deletion.
Only after explicit approval:

```bash
terraform apply cleanup.tfplan
```

Verify disposal against scoped Azure inventory, including node resources and
billable residuals. Retain protected evidence/state until verified disposal;
then handle state, saved plans, kubeconfig and private certificates under the
approved retention/credential policy. Record unresolved resources, cost exposure,
UTC and cleanup owner. Never delete state first or delete an entire existing
resource group as a shortcut.