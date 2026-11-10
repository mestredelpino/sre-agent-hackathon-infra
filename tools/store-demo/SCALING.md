# AKS Store Scaling Lab

This opt-in helper targets the existing `aks-store-demo` namespace. It uses Python 3
standard library and `kubectl`; it does not provision AKS, edit Terraform, change
application resource requests, or change fixed system nodes. Offline tests cover
the commands and safeguards. The live rehearsal below records separately what was
verified in the facilitator deployment.

## Prerequisites

The operator must first provision the approved lab cluster with Azure CNI Overlay,
Cilium, managed KEDA and Node Auto-Provisioning (NAP), with default NAP pools `None`.
The previous dynamic pod subnet configuration is unsupported by NAP. The helper
does not rebuild anything or assume a default NodePool exists. Wait for the KEDA
and NAP CRDs/controllers, application deployments, RabbitMQ management service on
port 15672, and the `orders` queue to be available. Azure quota, SKU availability,
cluster identity permissions and network access still govern successful provisioning.

The authoritative infra root's [deployment override](../../deployment_override.tf)
declares managed KEDA, NAP mode `Auto`, default NAP pools `None`, Cilium and
Overlay pod CIDR `10.244.0.0/16`; the two fixed system nodes remain. These are
platform prerequisites, not a legacy scenario feature-variable toggle. Verify
the actual approved cluster and controller readiness before using this helper.
This procedure does not require or authorize a new Terraform apply. If the live
cluster differs, stop and refer the mismatch to its infrastructure owner. Verify
CIDRs do not overlap connected networks. Do not copy configuration over private
overrides or use new state for an existing cluster; keep its original state and
approved deployment inputs. See the [deployment guide](../../docs/WORKSHOP-DEPLOYMENT.md).

An existing dynamic pod-subnet cluster cannot migrate in place to Overlay.
Terraform may nevertheless display an update: that does not make the Azure
migration supported. Rebuilding requires explicit authorization, a reviewed
replacement plan and app redeployment; ephemeral RabbitMQ/database contents and
the storefront public IP are lost. Preserve external monitoring, role assignments,
the baseline workload and dedicated kubeconfig when planning recovery. Never
disable NAP while it still owns nodes; drain/migrate workloads and follow the
[AKS NAP disable procedure](https://learn.microsoft.com/azure/aks/use-node-auto-provisioning#disable-node-auto-provisioning-nap-on-an-aks-cluster)
before applying a change back to manual provisioning.

Use a dedicated kubeconfig file, not `~/.kube/config` or a merged file list. Every
action requires the context and exact HTTPS API server string from its minified
configuration, and rejects mismatches before writes. Do not disable TLS validation.
Required RBAC includes reading CRDs, deployments, autoscalers, credential sources,
nodes and nodeclaims; creating/updating the lab objects; patching/scaling customers;
deleting the probe; and executing `rabbitmqctl` in `rabbitmq-0`.

## Commands

Run from the infra repository root, replacing the target values with the approved cluster:

```bash
export KUBECONFIG=/absolute/path/to/dedicated-kubeconfig
CONTEXT=approved-context
API_SERVER=https://approved-aks-api-server
python3 tools/store-demo/scaling.py setup --context "$CONTEXT" --api-server "$API_SERVER"
python3 tools/store-demo/scaling.py status --context "$CONTEXT" --api-server "$API_SERVER"
python3 tools/store-demo/scaling.py start --context "$CONTEXT" --api-server "$API_SERVER"
python3 tools/store-demo/scaling.py start --customers 8 --context "$CONTEXT" --api-server "$API_SERVER"
python3 tools/store-demo/scaling.py stop --context "$CONTEXT" --api-server "$API_SERVER"
python3 tools/store-demo/scaling.py nodes-start --context "$CONTEXT" --api-server "$API_SERVER"
python3 tools/store-demo/scaling.py status --context "$CONTEXT" --api-server "$API_SERVER"
python3 tools/store-demo/scaling.py nodes-stop --context "$CONTEXT" --api-server "$API_SERVER"
```

No live mutation is run automatically. Setup refuses same-name objects without
`app.kubernetes.io/managed-by=sre-scaling-lab`, and competing worker HPAs or
ScaledObjects. Repeated setup accepts the HPA controlled by this lab's ScaledObject.
Creates fail on name races; updates use resource versions rather than force takeover.

## Two Separate Demonstrations

**KEDA pod scaling:** setup creates the `lab-scaling` Secret, TriggerAuthentication
and ScaledObject. The RabbitMQ HTTP management endpoint is
`http://rabbitmq.aks-store-demo.svc.cluster.local:15672`, queue `orders`, vhost `/`.
QueueLength target is 5, polling 15 seconds, workers 1 through 12, cooldown 60
seconds and HPA scale-down stabilization 60 seconds. With minimum replicas 1,
HPA behavior controls ordinary scale-down; cooldown is not a promise of an exact
scale-down time. `start` defaults to 4 customers, bounded to 1 through 12. Before
the first scale, it saves the original count in annotation
`sre-scaling-lab/original-customers`. Repeated starts/stops preserve that baseline;
`stop` restores it and never forces worker replicas. Queue growth depends on actual
producer/consumer throughput, so pod scale-out is not guaranteed by customer count.

**Deterministic NAP demand:** setup also creates a dedicated `lab-scaling` NodePool
and Ubuntu AKSNodeClass. The pool selects Linux amd64, on-demand D-family VMs with
2 or 4 CPUs, has aggregate limits 8 CPUs / 32Gi, and a
`lab-scaling=true:NoSchedule` taint. Only the synthetic probe is given that
toleration by this helper. It includes Cilium startup taint/labels, and consolidates
WhenEmptyOrUnderutilized after 60 seconds. `nodes-start` creates two pause:3.10 pods,
each requesting 500m / 128Mi and limited to 1 CPU / 256Mi. Their selector requires
`karpenter.sh/nodepool=lab-scaling`, excluding spare system capacity. An initially
empty pool therefore needs at least one new node if provisioning succeeds; a
repeat run may reuse an existing lab node. Quota, controller health or policy can
still leave pods Pending. KEDA workers retain their original placement and requests.

`status` prints deployments, HPAs, ScaledObjects, nodes, nodeclaims and queue counts
using `kubectl exec rabbitmq-0 -- rabbitmqctl list_queues name messages
messages_ready messages_unacknowledged`; it does not print credentials.

## Authentication

There are no embedded credential values. Nonempty paired `RABBITMQ_USERNAME` and
`RABBITMQ_PASSWORD` environment variables take precedence; a partial pair fails.
Otherwise the helper reads simple `default_user` / `default_pass` assignments in
ConfigMap `rabbitmq`, key `rabbitmq.conf`. Complex expressions, quoting or duplicate
assignments require operator environment variables. Without that config, it resolves
the deployed `order-service` container's structured `envFrom` and `env` references
for `ORDER_QUEUE_USERNAME` / `ORDER_QUEUE_PASSWORD`, including Secret/ConfigMap refs.
The pinned sample uses Secret refs, so its deployed values are read, never hardcoded.
Provide operator variables securely without shell tracing or recording secrets in
command history. RabbitMQ must already have the matching account and management
access. The helper does not create broker accounts. HTTP is cluster-internal, not
TLS; this is a lab configuration. Secret JSON is sent through stdin; kubectl output
and errors from writes are captured and never echoed. No secret manifest is saved.

## Costs And Cleanup

The pool's CPU/memory constraints are approximate capacity guardrails, **not a hard
budget**. Concurrent provisioning can overshoot limits; VM runtime, disks, existing
system nodes and other Azure resources continue to incur costs. Watch nodeclaims,
nodes and Azure costs, and stop promptly. No hourly cost estimate is guaranteed.

Run `stop`, then `nodes-stop`; the latter deletes only an ownership-checked probe
using UID/resource-version preconditions. Allow consolidation to remove empty lab
nodes and inspect `status`. These actions intentionally retain the KEDA objects,
NodePool/AKSNodeClass and saved customer baseline for reruns. To fully remove the
lab, an operator must verify the ownership label on each `lab-scaling` object,
then explicitly delete the ScaledObject (its HPA is controller-owned),
TriggerAuthentication and Secret in `aks-store-demo`, and the cluster-scoped
NodePool and AKSNodeClass. Deleting a NodePool can terminate its nodes: ensure the
probe is gone, inspect all remaining pods and wait for nodeclaims to disappear
before removing its class. Never delete unrelated pools, broker resources or the
namespace. Once finished, remove only the saved baseline annotation if a future
session should capture a new baseline. Full cluster cleanup remains with Terraform.

## Live Rehearsal: 2026-11-05

The facilitator cluster `aks-srehackk8s-y2p5u5` was rebuilt with authorization
using Terraform, because its dynamic pod-subnet IPAM cannot migrate to Overlay.
The authoritative configuration and state remain in the protected operator
directory outside this repository. Azure initially failed control-plane creation
with an MSI credential error; a Terraform AzAPI reconciliation succeeded, then
the cluster was imported back into the authoritative AzureRM state. Its identity
grants, AGIC bindings and monitoring associations were restored through Terraform.
The original API endpoint and storefront IP changed. No original sample orders
or ephemeral database contents were retained.

Verified managed KEDA enabled, NAP mode `Auto`, default NAP pools `None`, Cilium
Overlay, and two Ready system nodes. `start --customers 4` increased producers;
KEDA scaled workers above one with RabbitMQ external metrics and a Ready
ScaledObject. During initial database recovery, its unavailable consumer also
contributed to the backlog: the initial replica increase is not evidence of
healthy end-to-end processing. The pinned sample's 25m database CPU cap caused
repeated startup-probe failures. The overlay now requests 100m, allows 500m CPU
and gives startup up to 600 seconds. Recreating the stuck unready database pod
adopted that template; all nine store workloads became Ready and makeline logs
confirmed successful order updates. After `stop`, the queue drained to zero and
workers and customers returned to one.

`nodes-start` created two Ready probe pods on a new NAP-provisioned
`Standard_D2als_v6` node. After `nodes-stop`, consolidation removed that node and
its NodeClaim; only the two system nodes remained. The pool and scaler remain
installed for reruns, with the original customer baseline saved.

That rehearsal used a historical ingress baseline; it does not validate the
current IP-only HTTPS path. Today the Store uses a hostless AGIC Ingress and
self-signed IP-SAN certificate in `store-front-tls`. Do not reuse historical
public IPs or enable a direct HTTP LoadBalancer. Follow the
[current deployment and recovery guide](../../docs/WORKSHOP-DEPLOYMENT.md)
and verify trusted external HTTPS, products/images and a newly processed order.
Autoscaling success does not prove public availability or database recovery;
this sample has no persistent-volume-backed database recovery guarantee.

To rerun from the infra repository root with the approved environment binding:

```bash
export KUBECONFIG='<absolute-dedicated-kubeconfig-path>'
CONTEXT='<approved-context>'
API_SERVER='<exact-approved-https-api-server>'
scaling() {
	python3 tools/store-demo/scaling.py "$@" --context "$CONTEXT" --api-server "$API_SERVER"
}
scaling start --customers 4
scaling nodes-start
scaling status
scaling stop
scaling nodes-stop
```

## Offline Tests

```bash
python3 -B -W error -m unittest discover -s tools/store-demo -p test_scaling.py -v
```

Run that command from the repository root. Tests mock all kubectl calls and do not
contact Kubernetes or Azure.