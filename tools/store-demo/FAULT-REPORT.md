# Store Fault Injection And Recovery Report

**FACILITATOR ONLY. Do not give this report, the runner, its tests or its private
journal to attendees or index them into investigator-agent knowledge.**

## Status And Scope

The [fault runner](faults.py) implements 19 individually selectable fault cards.
It generates a private JSON incident record with the exact target, original and
faulty field values, UTC timestamps, run ID and operator evidence references.
This document explains what each card changes, likely impact and recovery proof.
It is a design/operation report, not evidence that an incident has occurred.

Targets were checked against the local infra source and the pinned AKS Store
manifest at upstream revision `1d61b202d1e6dc9a78389c15e4bb9d529e5499d3`.
The Store namespace is `aks-store-demo`. The current ingress is hostless HTTPS
by gateway IP, references `store-front-tls`, and routes to `store-front:80`.
Historical HTTP LoadBalancer and `sre.example.test` overlays are not equivalent.
The runner deliberately rejects the old hostname-based Ingress for gateway cards.

**Validation: offline tests only; no Azure/Kubernetes fault has been executed or
live-rehearsed as part of this implementation.** Rehearse baseline, observed
impact, signal availability and recovery in a disposable team environment before
offering a card. API acceptance is not impact; restored configuration is not
end-to-end recovery. Do not describe the cards as live-validated.

## Guardrails

- Use only approved disposable lab environments, synthetic orders and a named
  recovery owner. Keep the agent read-only; the facilitator executes writes.
- One active incident per environment. Use the same private state directory for
  every card, including Azure cards, for that environment. No `break-all`, random
  shell execution, cascading mode, resource deletion or automatic escalation.
- Run on Linux/WSL with Python 3.9+, `kubectl`, a dedicated normal-user
  `KUBECONFIG`, explicit context and the exact HTTPS API endpoint. Azure cards
  additionally require Azure CLI authentication and separately authorized writes.
- Keep state outside this repository, outside shared/synced storage and outside
  attendee access. The CLI creates private files with umask 077; verify effective
  permissions/ACLs, especially on Windows-mounted filesystems. Ignore rules are
  not encryption. Keep the original journal until verified closure.
- `plan` reads the target and displays exact before/after fields; it makes no
  remote writes. `inject` requires namespace confirmation, a healthy/unsettled-free
  nine-workload baseline and a human attestation of external HTTPS/order health.
- Kubernetes writes use server dry-run and atomic JSON Patch tests on UID,
  resourceVersion and each original field. Restores affect only recorded paths.
  An object recreated with a new UID or unexpected controlled-field drift causes
  refusal. Unrelated paths are not restored from an old full manifest.
- Azure writes re-read ETag and controlled fields immediately before a narrowly
  scoped CLI update, but are **not atomic compare-and-swap**. Their validation
  step is read-only, not an Azure server-side dry-run. Stop all competing Azure
  mutations, Terraform applies and policy remediations for the target during the
  operation; cancel the card if that cannot be guaranteed. Resource IDs do not
  provide Kubernetes-style identity across Azure deletion/recreation.
- The journal lock only coordinates processes using that directory on that
  host/filesystem. It is not a distributed lock, credential sandbox, Azure RBAC
  boundary or proof that another journal has no active fault. Assign one operator
  and maintain an environment-wide manual exclusive lock.
- Existing workloads must have desired replicas at least one, current observed
  generation, all desired updated and Ready replicas, and settled StatefulSet
  revisions. Existing zero-customer configurations must be restored to an agreed
  healthy baseline before injection; the runner does not create that baseline.
- Do not delete/restart RabbitMQ or DocumentDB to make a symptom more dramatic.
  The sample data services are ephemeral. StatefulSet naming does not establish
  order persistence, backup or recoverability.

## Operator Workflow

Run from the infra repository root. Start by listing available cards; this
does not require credentials or contact either platform:

```bash
python3 -B tools/store-demo/faults.py list
```

Set these values from the approved environment record, not from a guessed name:

```bash
export KUBECONFIG='<absolute-dedicated-kubeconfig-path>'
export EXPECTED_CONTEXT='<approved-context>'
export API_SERVER='<exact-https-api-server-including-port-if-present>'
export FAULT_STATE_DIR='<absolute-private-nonsynced-directory-for-this-environment>'
storefault() {
  python3 -B tools/store-demo/faults.py "$@" \
    --context "$EXPECTED_CONTEXT" --api-server "$API_SERVER" \
    --state-dir "$FAULT_STATE_DIR"
}
```

Preview and review one card. `plan` does not validate customer experience or
guarantee admission by the API; injection performs the Kubernetes server dry-run:

```bash
storefault plan --fault service-selector
```

Independently verify and record trusted external HTTPS, products/images, fresh
order submission and successful order processing. Check monitoring, recovery
access, scope approval and the operator lock. Only then inject:

```bash
storefault inject --fault service-selector \
  --confirm-namespace aks-store-demo --baseline-verified \
  --evidence 'private-ledger:team-a/baseline-001'
storefault status
```

Observe actual symptoms, preserve original agent output and keep human hints
separate. To restore the saved fields, use the same context and state directory:

```bash
storefault revert --confirm-namespace aks-store-demo
storefault status
```

`revert` needs no `--fault`: the journal identifies the active card. If the team
already restored the exact baseline, it records restoration without rewriting it.
It does not automatically close the incident or inject the next fault.

After the card-specific checks below and the complete recovery checklist pass:

```bash
storefault close --confirm-namespace aks-store-demo --recovery-verified \
  --evidence 'private-ledger:team-a/recovery-001'
```

Closure archives `<run-id>.json` and marks `active.json` closed. Injection then
becomes eligible again after the required workshop cooldown and operator approval.
Never delete the active journal to bypass an open incident. Plans and status
are diagnostic, not authorization or permission to introduce another fault.

## Fault Inventory

All Kubernetes targets below are in `aks-store-demo` except cluster-scoped
`NodePool/lab-scaling`. Original values are saved at injection time, not guessed
from this table. For each row, run the common `revert` command above; the last
column specifies what that restore changes and what else must be verified.

| Card | Exact target and mutation | Expected symptom and useful evidence | Reversion and specific recovery proof |
| --- | --- | --- | --- |
| `service-selector` | Service `store-front`: selector `{app: store-front}` -> `{app: sre-fault-no-match}` | Ready frontend pods but empty ready Service EndpointSlices; gateway requests may fail after AGIC convergence | Restore saved selector; endpoints repopulate, gateway backends and external HTTPS recover |
| `service-port` | Service `product-service`: sole port 3002 targetPort `3002` -> `3999` | Frontend HTML may load while product calls fail; pods/endpoints can remain Ready | Restore targetPort; fetch products/images and correlate successful product requests |
| `ingress-backend` | Ingress `store-front`: first backend Service name -> `sre-fault-missing-service` | AGIC reports invalid/missing backend; route may fail or last valid config may continue serving | Restore saved backend name; verify AGIC reconciles `store-front:80` and real HTTPS route |
| `gateway-probe` | Ingress `store-front`: `appgw.ingress.kubernetes.io/health-probe-path` -> `/sre-fault-missing-health` | Backend can become Unhealthy while Service/pods remain healthy; gateway 502 after convergence | Restore saved annotation; wait for healthy gateway probes and repeated HTTPS success |
| `tls-secret` | Ingress `store-front`: `spec.tls[0].secretName` -> `sre-fault-missing-tls` | AGIC TLS configuration error; existing listener/certificate may remain active | Restore `store-front-tls` reference; inspect live certificate/IP SAN and trusted TLS handshake |
| `image-pull` | Deployment `product-service`: sole container image -> `ghcr.io/azure-samples/aks-store-demo/product-service:sre-fault-nonexistent-20261109` | ErrImagePull/ImagePullBackOff, stalled new ReplicaSet; old healthy replicas may still serve | Restore saved image; all desired replicas updated/Ready and no continuing pull failures |
| `readiness` | Deployment `product-service`: container readiness HTTP port -> `3999`; liveness unchanged | New pod Running but not Ready; old replicas may hide customer impact | Restore saved readiness port; current generation completes and ready endpoints reflect current replicas |
| `scheduling` | Deployment `product-service`: nodeSelector `kubernetes.io/os` -> `sre-fault-no-os` | Pending new pods, FailedScheduling/no matching nodes; adding ordinary capacity cannot satisfy the selector | Restore saved OS selector; rollout completes and no fault-created pending replicas remain |
| `memory-limit` | Deployment `product-service`: memory request and limit -> `1Mi`, CPU and other resource fields preserved | New container may fail runtime startup or OOM; old replicas can still serve | Restore saved container resources; new replicas settle, memory headroom returns and restart counts stop increasing |
| `app-config` | Deployment `order-service`: `envFrom` reference `order-service-configs` -> `sre-fault-missing-config` | CreateContainerConfigError on new replica; ConfigMap still exists under its correct name | Restore saved reference; rollout completes and a new order reaches processing |
| `queue-port` | Service `rabbitmq`: AMQP port 5672 targetPort `5672` -> `5673`; management 15672 unchanged | New AMQP connections fail while RabbitMQ management and existing connections can remain healthy | Restore AMQP targetPort; prove fresh intake and processing, not just management-port success |
| `database-selector` | Service `documentdb`: selector -> `{app: sre-fault-no-match}`; StatefulSet untouched | New database connections fail; database pod stays healthy and existing connections may survive | Restore saved selector; endpoints return and a newly submitted order is persisted/processed through the app |
| `database-client` | Deployment `makeline-service`: `ORDER_DB_URI` hostname `documentdb` -> `sre-fault.invalid`, port/options unchanged | DNS/connection errors in new makeline replica; database itself stays healthy; old replica may retain service | Restore the saved literal URI value; current makeline rollout and fresh order processing succeed |
| `worker-down` | Deployment `virtual-worker`: saved positive replicas -> `0` | Order completion simulation stops while intake/catalog can remain healthy; RabbitMQ need not accumulate because makeline consumes it | Restore exact saved replica count; prove orders progress to completion, not just accepted status |
| `keda-auth` | ScaledObject `lab-scaling`: RabbitMQ trigger `authenticationRef.name` -> `sre-fault-missing-auth` | Scaler error/unknown external metric; existing workers may still process orders | Restore saved auth reference; ScaledObject/HPA metrics healthy and bounded demand produces observed scaling |
| `keda-queue` | ScaledObject `lab-scaling`: trigger queueName -> `sre-fault-missing-queue` | Queue lookup error or missing/zero demand depending on scaler behavior; real orders can keep arriving | Restore saved queue name; compare actual `orders` demand with metric/HPA response |
| `nap-capacity` | NodePool `lab-scaling`: CPU limit -> `1`, with eligible nodes still restricted to 2/4 CPU | Dedicated NAP probe stays Pending; pool cannot add a qualifying node within its limit | Restore saved CPU limit; existing approved probe obtains a Ready node, then remove probe and verify consolidation |
| `azure-https-deny` | Explicit existing public HTTPS NSG rule: access `Allow` -> `Deny` only | New external TCP/443 connections can time out while private app health and gateway backend probes remain healthy | Restore saved `Allow`; verify from attendee network with fresh TLS connections and expected content |
| `azure-alert-disable` | Azure metric alert `alert-appgw-unhealthy-host`: `properties.enabled` true -> false | Application remains healthy but this alert rule no longer evaluates; other rules/incident sources remain independent | Restore saved true; confirm enabled configuration and separately rehearse rule evaluation, notification and agent intake |

## Interpretation And Eligibility

### Gateway And TLS

Rehearse that `/sre-fault-missing-health` is actually non-successful for the
deployed frontend. Single-page applications can return 200 for unknown paths.
Do not register `gateway-probe` as an outage card if this happens.

AGIC can preserve last-known-good Azure configuration when an Ingress references
a missing Service or TLS Secret. These cards are controller/reconciliation
investigations unless an outage has actually been observed. Never force impact
by removing listener/route ignore rules, deleting the gateway or disabling TLS
verification. Do not distribute TLS private keys or dump Secret YAML.

### Rollouts And Resources

Deployment-template cards use normal RollingUpdate behavior. The runner does not
alter strategy, delete old replicas or force a rollout outage. Existing replicas
can preserve customer availability for `image-pull`, `readiness`, `scheduling`,
`memory-limit`, `app-config` and `database-client`. Score degraded deployment
correctly rather than demanding an outage diagnosis. Rehearse the nonexistent
image tag: registry authentication/network denial is a different root cause.

Memory symptoms depend on the container runtime and image; a low limit does not
guarantee the literal status `OOMKilled`. CPU starvation/database startup was a
previous observed issue, but this runner deliberately does not recreate it by
rolling the ephemeral DocumentDB StatefulSet.

### Messaging And Data

Service changes generally affect new connections, not established conntrack/TCP
sessions. A queue/database Service fault may initially show only configuration
and endpoint evidence; measure actual impact rather than restarting data pods.
Keep the management port private. Neither queue depth nor a homepage response
proves that an order was successfully completed.

`worker-down` is refused if an HPA or ScaledObject targets `virtual-worker`.
Use it before scaling setup or use a different approved card; do not fight the
autoscaler or delete its objects to make injection succeed. KEDA cards require
the helper-owned `lab-scaling` object, not an arbitrary production scaler.

### Capacity

`nap-capacity` requires the helper-owned NodePool and no existing lab-pool nodes
or NodeClaims. Stop the probe and wait for consolidation before injection.
After injection, the facilitator can start only the existing bounded NAP probe
using the [scaling procedure](SCALING.md). That start is a separately approved
action and is not included in the fault patch. Remove the probe after recovery.
No synthetic traffic or node pressure is generated by the fault runner itself.

The Store workers often fit on system nodes; KEDA scale-out does not inherently
exercise NAP. A backlog can also reflect an unhealthy dependency rather than
insufficient worker capacity. Do not raise limits or quotas without diagnosis.

## Optional Azure Fault Binding

Kubernetes card names are fixed. Azure names vary with prefix/suffix and policy,
so the runner accepts exact resource IDs instead of embedding one environment's
names or reading Terraform state. Both Azure resources must be in the same
subscription/resource group as the bound AKS cluster, whose `environment` tag
must be `hackathon` and whose actual API FQDN must match the kubeconfig endpoint.
This is a mismatch guard, not a substitute for scope/ownership approval.

For `azure-https-deny`, select the existing rule on the NSG that actually controls
the gateway client path. Validate the gateway subnet association, rule priority,
destination and effective rules independently. The runner accepts only a
singular Inbound/Tcp rule from `Internet`, source port `*`, destination port
`443`, access `Allow`, with no plural/ASG overrides. It does not create an NSG,
take over a policy-owned rule or infer that the chosen rule is effective.
Do not use a shared NSG or a management/control-plane rule.

The inspected local infra root still contains an HTTP-era `AllowHTTPInbound`
rule on port 80. That is not an eligible HTTPS fault. If no approved existing
443 rule exists, omit this card; do not create or broaden a rule just to break it.

Obtain IDs privately from approved Azure inventory. Select one card and retain
the same bindings for status, revert and close:

```bash
export AKS_RESOURCE_ID='<exact-approved-aks-resource-id>'
export AZURE_FAULT_ID='<exact-approved-nsg-security-rule-or-metric-alert-id>'
azurefault() {
  storefault "$@" --aks-resource-id "$AKS_RESOURCE_ID" \
    --azure-resource-id "$AZURE_FAULT_ID"
}
azurefault plan --fault azure-alert-disable
```

For a network card use `--fault azure-https-deny` instead, with its approved rule
ID. The alert card accepts only `Microsoft.Insights/metricAlerts/alert-appgw-unhealthy-host`.
It does not remove receivers, alter scopes or mutate Grafana/agent configuration.

After scope review, operator lock, healthy baseline and explicit approval:

```bash
azurefault inject --fault azure-alert-disable \
  --confirm-namespace aks-store-demo \
  --confirm-azure-resource-id "$AZURE_FAULT_ID" \
  --baseline-verified --evidence 'private-ledger:team-a/azure-baseline-001'
azurefault status
```

Restore only that recorded Azure field, independently verify it and then close:

```bash
azurefault revert --confirm-namespace aks-store-demo \
  --confirm-azure-resource-id "$AZURE_FAULT_ID"
# Perform independent full recovery verification before the next command.
azurefault close --confirm-namespace aks-store-demo \
  --confirm-azure-resource-id "$AZURE_FAULT_ID" \
  --recovery-verified --evidence 'private-ledger:team-a/azure-recovery-001'
```

Disabling a rule cannot prove an undelivered alert was otherwise deliverable.
Require a known working receiver and rehearsed evaluation path beforehand.
Use an approved explicit manual incident handoff for a silent-detection exercise;
do not combine another live fault just to stimulate the disabled rule.

## Recovery Checklist

1. `status` reports the recorded controlled fields at `baseline` after `revert`.
   Inspect the restored live configuration, not just the command exit code.
2. All nine Store workloads have current, settled Ready/updated desired replicas.
   Inspect ReplicaSets, pod events and ready EndpointSlices. Historical restart
   counters/events may remain; confirm no continuing new failures.
3. AGIC has reconciled the intended hostless route and gateway backends are
   healthy. From the attendee network, repeat HTTPS requests with the supplied
   public Store certificate and IP SAN verification; never use `curl -k`.
4. Verify products/images, submit a new synthetic order and prove its processing
   and expected completion via approved telemetry/private administration. Close
   loopback-only administration port-forwards afterwards.
5. For scaling cards, restore the signal, observe the relevant bounded scaling
   sequence and stop approved load/probes. Record residual nodes/claims and wait
   for accepted baseline. Configuration alone is not scaling evidence.
6. For the alert card, verify enabled state plus the independently rehearsed
   monitoring path. Preserve alert/notification/intake gaps honestly.
7. Record UTC, evidence reference, actual executor, agent conclusions, human help,
   observed impact and independent verifier. Then run `close` and honor cooldown.

`close` checks restored fields and workload readiness; the `--recovery-verified`
flag is an operator attestation, not an automated external probe or order test.

## Failed Or Interrupted Operations

The runner writes `phase: prepared` before the actual mutation. If the process or
connection fails afterwards, the server may or may not have accepted the write.
Keep the journal, stop further injection, run `status`, then `revert` with the
same bindings. A prepared record already at baseline can be marked reverted
without an unnecessary write. An exact fault value can be reversed normally.
The script deliberately does not blindly auto-rollback an uncertain response.

Unexpected controlled fields, a recreated Kubernetes object, mismatched target,
missing journal, inaccessible recovery credentials or a concurrent Azure update
require STOP and named-owner review. Preserve before/after evidence and original
state. Do not force a new value, delete the journal, replay an old plan, run
`rollout undo`, bulk-apply manifests/Terraform or restart data services as a reset.
If the journal is lost, reconstruct only a reviewed minimal change from the
approved baseline and audit evidence; there is no generic guaranteed reset.

## Coverage And Deliberate Exclusions

Covered: Azure client ingress and alert detection; gateway routing/probing/TLS;
Kubernetes Service routing, scheduling, resources and rollout health; app config;
AMQP/database connectivity; order completion workers; KEDA signal/auth and NAP
capacity policy. The script does not read Terraform state or store Secret values.

Not automated: cluster rebuilds, shared CoreDNS/control-plane disruption, subnet
deletion, broad RBAC revocation, storage deletion, registry-wide outages, agent
tool/knowledge corruption, distributed tracing failures, or fabricated backup
recovery. These require separately isolated environments and rehearsed recovery.
NSG faults do not change app/data exposure, and the runner never opens queue,
database or administration ports to the Internet.

## Offline Verification

```bash
python3 -B -W error -m unittest discover \
  -s tools/store-demo -p test_faults.py -v
python3 -B -W error -m unittest discover \
  -s tools/store-demo -p test_scaling.py -v
```

Tests cover all 19 card plan/inject/revert/close round trips with mocked objects,
unrelated-field preservation, journal locking, prepared/uncertain writes, human
repair, drift/recreation rejection, CLI confirmation, scaling ownership/conflicts,
patch UID/resourceVersion tests and Azure scope/ETag/field-only update behavior.
They cannot prove Azure CLI/provider compatibility, live NSG effect, admission
policy, registry responses, AGIC convergence, scaler behavior, alert delivery,
agent investigation quality or end-to-end live recovery.