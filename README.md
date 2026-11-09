# SRE Agent Hackathon Infrastructure

Authoritative local Terraform deployment for an isolated Azure SRE hackathon environment. Deployment identifiers and region are supplied through ignored local variable files. A compatibility symlink from the previous local directory points here; it is not a second state directory.

Terraform source, provider lock file and the baseline workload can be version controlled. Local state, backups, saved plans, kubeconfig, variable files and identity-recovery material are deliberately ignored. Ignored does not mean encrypted: protect this directory with Windows filesystem permissions and do not publish its local artifacts. No Git remote or commit is created automatically.

Run Terraform from this directory using the existing state and local `*.auto.tfvars` files, including the resource-group name input. Never initialize a second deployment against the same Azure names. Review a new plan before any apply; saved plans from before relocation are historical artifacts and should not be applied. Do not copy generic workshop Terraform over these deployed overrides without reviewing resource addresses and networking changes.

The workshop source and store/dashboard helpers are in the sibling `../sre-agent-hackathon` directory. Pending agent access and store/dashboard setup changes still require reconciliation into this deployment and a reviewed plan. The subnet HTTP repair is now deployed through `store-network.tf`; see [recovery verification and remaining data/alerting gaps](RECOVERY.md). The relocation itself did not apply Azure changes.

```bash
terraform validate
terraform plan -input=false
```

The kubeconfig remains local at `./kubeconfig`. Existing processes using the old path continue through the symlink. Coordinate all edits and state operations with other agents/operators; the directory must have only one authoritative state.

## HTTPS-Only Lab Gateway

Generate a 90-day self-signed certificate for the sample hostname before planning:

```bash
bash scripts/create-lab-certificate.sh
terraform fmt -check
terraform validate
terraform plan -input=false
```

The script requires OpenSSL and refuses to overwrite an existing `.certs/` directory. It creates a private key, certificate, password-protected PFX and random password locally; all are ignored by Git. Protect these files with filesystem permissions. Terraform reads the PFX and password and stores certificate material in state and saved plans, so keep those secure too. Never upload keys, passwords, state or saved plans to GitHub. For production, use an approved CA and preferably a Key Vault certificate reference instead of this lab PFX workflow.

The gateway starts with only an HTTPS listener on port 443, using `sre-lab-tls` and a predefined TLS 1.2-or-newer policy. `workload.yaml` references that installed certificate, uses `sre.example.test`, and disables SSL redirect: an HTTP-to-HTTPS redirect would require an HTTP listener. Do not add a `spec.tls` block to this manifest when using the installed-certificate annotation. Other AGIC-managed ingresses must also use HTTPS; any HTTP ingress or redirect can make controller updates fail policy evaluation. The backend Service, pod traffic and probes still use HTTP; this change addresses listener policy, not end-to-end TLS. Additional organizational policies may require an approved issuer or different TLS settings.

For a different hostname, pass it to the certificate script, set `appgw_tls_hostname` in ignored local inputs, and update the ingress host to match. The example `.test` name has no public DNS; map it locally or test with the public gateway IP without changing DNS:

```bash
curl --cacert .certs/appgw.crt --resolve "sre.example.test:443:<gateway-public-ip>" https://sre.example.test/
```

Trust the public certificate explicitly for testing; browsers will otherwise warn. After reviewing and applying Terraform, apply the sample manifest with the correct kubeconfig. Then verify fresh gateway listener configuration, AGIC logs and HTTPS application responses; local validation alone does not prove Azure Policy acceptance.

Existing HTTP gateways need a coordinated migration: the gateway's `ignore_changes` entries deliberately leave frontend ports, listeners and routes to AGIC. Changing this Terraform does not replace those fields on an existing gateway. Update every ingress to HTTPS and reconcile AGIC, then inspect the resulting gateway. If policy blocks that transition, coordinate a controlled migration or recreate with the platform owner; do not remove the ignore list blindly, because Terraform's bootstrap rule would overwrite AGIC routes. New gateways, including a retry after a policy-denied creation, use the HTTPS-only bootstrap directly.

The separate storefront HTTP NSG rule now defaults to disabled, even when `store_front_public_ip` is set. A reviewed apply will remove an existing rule unless `allow_store_front_http` is explicitly enabled, which is not appropriate for HTTPS-only environments. This does not migrate or change an existing Kubernetes LoadBalancer Service: disable its public exposure or route it through a separately configured HTTPS ingress. Historical HTTP recovery evidence in `RECOVERY.md` is not evidence of HTTPS compliance.

## Optional cert-manager Setup

After Terraform creates AKS and an operator has verified the dedicated kubeconfig, install cert-manager with the explicit post-deployment script. It is not run by `terraform apply`. The script pins the upstream static manifest to v1.21.3, including CRDs, controllers, admission webhooks and cluster-wide RBAC. Check the [supported Kubernetes versions](https://cert-manager.io/docs/releases/) and review the [installation manifest](https://github.com/cert-manager/cert-manager/releases/download/v1.21.3/cert-manager.yaml) before installation. Internet access to GitHub and the controller image registry is required. Do not run over an existing Helm, GitOps or platform-managed cert-manager installation; coordinate with its owner instead.

Installation requires Kubernetes permission to manage namespaces, CRDs, ClusterRoles, ClusterRoleBindings, webhook configurations and Deployments. Azure Grafana roles or permission to download a kubeconfig do not grant these Kubernetes permissions. Obtain approved cluster setup authorization; do not bypass denied access with shared admin credentials.

```bash
export KUBECONFIG="<dedicated-kubeconfig-path>"
export EXPECTED_CONTEXT="<approved-context>"
export AKS_FQDN="<approved-aks-api-hostname>"
kubectl kustomize cluster/cert-manager
bash scripts/install-cert-manager.sh --apply
kubectl --context "$EXPECTED_CONTEXT" -n cert-manager-lab get issuers,certificates
```

The installer checks the selected API server against `AKS_FQDN` before writes, performs server dry-runs, waits for controllers, and verifies issuance. If the webhook is not yet ready for the configuration dry-run, investigate controller readiness and retry; it does not bypass admission failures. Installation is not atomic; a failure can leave controllers or the lab namespace installed.

The lab configuration uses a namespaced `lab-selfsigned` Issuer in `cert-manager-lab`. Its `sre-example` Certificate issues `sre.example.test` into the `sre-example-tls` Secret, valid for 90 days and renewed 15 days before expiry with key rotation. This is a self-signed test certificate, not a trusted CA; browser trust and hostname resolution remain separate requirements. No ACME account, DNS permissions or port-80 challenge listener is configured. For public trust, use an approved real domain and an approved issuer; DNS-01 is compatible with the HTTPS-only listener restriction, while HTTP-01 requires port 80.

This setup deliberately does not modify `workload.yaml`, the workshop storefront ingress, the bootstrap `.certs/` files or Terraform's installed `sre-lab-tls` gateway certificate. Cert-manager renews only its Kubernetes Secret. To serve a cert-manager Secret through AGIC later, create the Issuer/Certificate in the application's namespace and coordinate replacing the installed-certificate annotation with `spec.tls.secretName`; do not enable HTTP redirects. Validate the installed AGIC version produces HTTPS-only listeners and accepts Secret renewal before migrating. Avoid overlapping host/path routes between the NGINX and storefront ingresses. No automatic Secret-to-PFX/Key Vault synchronization is provided here.

Do not export TLS Secrets or private keys to Git. Back up needed Secrets securely. Before uninstalling cert-manager, inventory dependent Certificates and issuers across all namespaces, coordinate with their owners, and follow the [upstream uninstall procedure](https://cert-manager.io/docs/installation/kubectl/#uninstalling); deleting CRDs removes their custom resources and can disrupt unrelated certificates. Terraform destroy does not manage or uninstall this post-deployment addon.

## Deploy Store Demo Without DNS

After installing cert-manager, use [scripts/deploy-store-demo.sh](scripts/deploy-store-demo.sh) to deploy the pinned AKS Store Demo with internal frontend Services and an AGIC TLS ingress. Run from this repository in Bash, including Azure Cloud Shell. Keep `KUBECONFIG`, `EXPECTED_CONTEXT` and `AKS_FQDN` set as above, and provide the gateway's actual public IPv4 address from Azure:

```bash
export APPGW_PUBLIC_IP="<application-gateway-public-ip>"
bash scripts/deploy-store-demo.sh --apply --replace-sample-ingress
```

The optional `--replace-sample-ingress` explicitly authorizes deleting only `sre-hackathon/sre-web` to transfer its route to Store Demo; omit it if the sample ingress is absent. Review all other ingresses on the shared gateway before deploying: this hostless `/` route is a catch-all and must not conflict with another application's catch-all route. The script checks the API endpoint before writes, renders the pinned workload once, performs server dry-runs, and waits for certificate issuance and the frontend rollout before replacing the sample route. It needs `kubectl`, `jq`, Kubernetes deployment permissions and access to GitHub/container registries. It does not install cert-manager or AGIC. Installation is not atomic; inspect partial resources before retrying.

Open `https://<application-gateway-public-ip>/` directly. No DNS or hosts-file entry is required. The script creates an IP-SAN self-signed Certificate and its `store-front-tls` Secret in `aks-store-demo`; AGIC uses `spec.tls.secretName`, not the installed gateway PFX. Browsers will still warn because the issuer is not trusted. If the gateway IP changes, rerun with the new IP. After AGIC reconciles, verify the live gateway has HTTPS-only listeners and check application health and certificate renewal; offline tests cannot establish Azure Policy acceptance. No HTTP redirect or ACME HTTP challenge is enabled.

The lab's database data is not durable; do not use it for production or important data. Cleanup is `kubectl --context "$EXPECTED_CONTEXT" delete namespace aks-store-demo`, which deletes all workloads and TLS Secrets in that namespace. If the script removed the NGINX sample ingress, restore it separately when needed.

## Existing Resource Group

Terraform 1.7 or newer is required. The resource group must already exist in the configured subscription. `data.azurerm_resource_group.main` reads its name, location and ID using the local `resource_group_name` input; this configuration does not create the group or manage its tags. Keep the local `location` input consistent with the existing environment because the storefront rule uses it to identify an externally managed NSG.

For state created by the previous configuration, the `removed` block declares that `azurerm_resource_group.main` must leave Terraform management without being destroyed. No manual `terraform state rm` is needed. Back up the authoritative state securely, generate a fresh plan, and verify that the group is reported as no longer managed rather than destroyed. Review all other changes before applying; the migration does not automatically approve unrelated drift. The state change takes effect only after a reviewed apply, and the group remains outside Terraform ownership afterward, including during destroy.

For a new state, the data source looks up the existing group; it does not import or adopt existing child resources. Restore the authoritative state or explicitly import those resources before planning against an existing deployment.

## Git and Local Files

Commit the Terraform source, `.terraform.lock.hcl`, `.gitignore`, workload manifest, documentation and `terraform.tfvars.example`. The example contains placeholders only; it is not loaded automatically. Never overwrite the existing local variable files with it.

Saved `*.tfplan` files and their JSON exports are disposable and ignored. Generate a fresh plan whenever needed. Keep local state, state backups, variable files, kubeconfig and `identity-recovery/` out of Git and preserve them for deployment management and recovery. The `.terraform/` directory is a disposable provider cache; `terraform init` can recreate it using the committed lock file.

A fresh clone does not contain the deployed environment's state or inputs. Restore the authoritative state and approved inputs securely before planning against existing Azure resources; do not apply from an empty state. This configuration includes deployment-specific overrides and is not a generic new-environment template.

Before the first commit, validate and review exactly what Git will include:

```bash
terraform fmt -check
terraform validate
git add .
git diff --cached --check
git diff --cached --stat
git diff --cached
```

Ignored files are not encrypted or backed up by Git. Keep secure backups of deployment state and recovery material separately.# sre-agent-hackathon-infra

## Incident Alert Coverage

[monitoring.tf](monitoring.tf) retains the seven baseline alerts. Restart detection now measures observed per-container counter increases within 15 minutes instead of lifetime restarts, image-pull events are filtered by message, and cluster/dependency queries are resource-scoped. The restart delta can miss restarts before the first sample or across counter resets; it is not an exact event count. CPU alerts split by node, and Ready is treated as one of the node's reported conditions.

[incident-alerts.tf](incident-alerts.tf) adds 13 rules routed to the existing action group:

- Gateway failed requests: more than 10 in five minutes; average request latency: more than 2000 ms over ten minutes.
- Gateway 5xx: more than 5 percent in ten minutes, with at least 20 requests and five errors to reduce low-volume noise.
- Node working-set memory above 90 percent and node/device disk usage above 90 percent over ten minutes.
- Pods older than ten minutes still Pending, observed OOMKilled status, and node memory/disk/PID pressure.
- Missing Container Insights node inventory for 15 minutes.
- Deployment unavailable replicas and StatefulSet unready current replicas for ten minutes, including Store Demo database pods. These detect readiness failures, not application/database correctness; the StatefulSet check does not detect every missing desired replica.
- Persistent-volume usage above 85 percent for ten minutes and missing Kubernetes Prometheus metrics for 15 minutes.

Log rules require Container Insights inventory/events and the existing gateway access diagnostic logs in `AzureDiagnostics`. New log rules validate their queries during deployment; confirm tables exist and contain this cluster's data before applying. Kubernetes inventory is eventually consistent: transient rollout conditions can be delayed, and OOM last-status values can persist after recovery. Prometheus rules use the default kube-state and kubelet metric collection already configured in this repo; custom ingestion filters must retain those series. PVC alerts only apply to persistent volumes that expose capacity metrics, not the lab's ephemeral database storage. Missing-data alerts intentionally fire if their collection pipeline is disabled. Alert evaluations, log ingestion, metric dimensions and synthetic checks can add Azure Monitor costs.

[availability-alerts.tf](availability-alerts.tf) optionally adds one public HTTPS test and one availability alert, including TLS validation and a 14-day certificate-expiry check. Set `availability_test_url` in ignored local Terraform inputs to a publicly reachable storefront or catalog endpoint returning HTTP 200 with a publicly trusted certificate. It probes every five minutes from three locations and alerts when two locations fail. The default is `null`, so no Application Insights/probe resources are created. The current self-signed IP-only endpoint is not suitable for this probe; do not disable certificate validation to make it pass. For that lab, run an approved external probe that trusts the lab public certificate and reports failures to the monitoring platform. No cert-manager metrics scraping or separate certificate-exporter deployment is added here.

Review thresholds against actual workshop traffic and scaling behavior. Check the existing action group's notification receivers separately; adding rules does not add a recipient or change SRE Agent configuration. After a reviewed plan/apply, exercise controlled failures, inspect alert dimensions/context and verify automatic resolution after recovery. No Azure changes or live KQL/PromQL executions are performed by local tests.

Offline checks:

```bash
terraform fmt -check monitoring.tf incident-alerts.tf availability-alerts.tf
python3 -m unittest discover -s tests -p 'test_monitoring_configuration.py' -v
```

The provider-schema test uses installed providers from `terraform init` and temporary non-secret fixture IDs, without Azure credentials or cluster access.