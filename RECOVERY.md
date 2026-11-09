# Post-Recreate Recovery Verification

Verified on 2026-11-05. Two independent ingress paths must be checked: the AGIC-managed Application Gateway and the store-front Service's public LoadBalancer. Healthy pods or gateway probes alone do not prove external storefront connectivity.

## AGIC Authorization

`agic.tf` already declares `azurerm_role_assignment.agic_subnet`: Network Contributor scoped only to `subnet-appgw`, with its principal derived from the current AKS AGIC add-on identity. The deployed grant matches the current add-on principal and is tracked in the authoritative Terraform state. The same resource exists in the sibling workshop scenario. Do not hardcode the previous identity after recreating AKS. Permission propagation and controller reconciliation still require checking fresh backend health and actual application responses after a recreate.

## Public Storefront

The separate storefront was still inaccessible after AGIC recovery. Its node NSG allowed Internet traffic to the LoadBalancer IP, but the subnet NSG had no matching allow. `store-network.tf` now manages one inbound TCP/80 rule, restricted to the configured storefront public IPv4 /32. It does not expose the database, queue, administration service or arbitrary node ports. The target subnet NSG remains externally managed; only this standalone rule is owned here.

The repair plan was checked to create only `azurerm_network_security_rule.store_front_http[0]`, then applied. The public homepage returned HTTP 200; browser verification rendered all ten products and loaded all ten product images. A subsequent full plan showed only preexisting unrelated storage diagnostic drift, with no pending rule or AGIC changes. If the Service public IP changes, update the ignored local `store.auto.tfvars` and review/apply a fresh plan. Never replay a historical saved plan.

## Data Durability

The current DocumentDB StatefulSet has no declared volumes or volumeClaimTemplates. Its database data is not protected by a persistent volume in this sample. The catalog renders, but that is not evidence that pre-recreate order history survived or was restored. Do not claim historical data recovery without a separate database query and baseline comparison. Add durable storage and a tested backup/restore procedure before requiring persistence; changing this disposable sample's storage is not part of this network repair.

## Alerting Gap

An Azure Monitor metric alert already exists and is enabled: `alert-appgw-unhealthy-host`, Average `UnhealthyHostCount > 0`, severity 1, evaluated every minute over five minutes. It is defined in `monitoring.tf`; a duplicate Grafana-managed rule is not required for this metric to alert. No new Grafana-managed alert rule was created during this repair.

However, its action group currently has no email, webhook or Function receiver. Configure the existing `alert_email` input with an approved recipient, or explicitly implement an approved integration, then review/apply and test notification delivery. No recipient was guessed or test notification sent. That gateway metric does not cover the separate store LoadBalancer NSG outage: add an externally executed synthetic storefront/catalog check for that path. Pod Ready and Running panels remain workload signals, not end-to-end availability checks.