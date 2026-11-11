# Environment-Driven Alerts

Run from the infrastructure repository:

```bash
source scripts/alerts.env
python3 scripts/create-alert-rules.py
python3 scripts/create-alert-rules.py --apply
```

The first invocation prints a plan; the second replaces the named managed
Prometheus rule group's complete rule list and verifies enablement by read-back.
It does not modify workloads, NSGs, notification receivers, or other alert groups.
Azure CLI must already be authenticated with permission to read the configured
resources and write the rule group. Azure Monitor alerts can incur charges.

Edit `alerts.env` for another environment. `ALERT_NAMESPACES` is a pipe-separated
namespace list. Set `ALERT_RULES_FILE` to an absolute JSON catalog path to use a
different catalog; otherwise the adjacent `alert-rules.json` is used.

To add rules, append entries with `alert`, `expression`, `summary`, optional `for`
(ISO 8601 duration, default PT5M), and optional `severity` (0-4, default 2).
Expressions can use `${NAMESPACES}` for the namespace regex. Each alert name must
be unique. All catalog rules are explicitly enabled and use the existing action
group. Use a different `ALERT_RULE_GROUP_NAME` for an independent additional group.

The supplied environment updates the existing workload-availability group, which
is also tracked by Terraform. A later Terraform apply can replace this catalog
and disable rules until the Terraform definition is reconciled. Use this script
as the rule-group writer in the meantime, not the older configure-alerts wrapper.

Deployment and StatefulSet availability, Pending pods, restart bursts, container
waiting failures, PVC space, and missing node metrics are included. The respective
Prometheus series must actually be collected; missing series do not mean healthy.
PVC rules do not cover ephemeral database storage. Conditions must persist for
the configured duration plus collection/evaluation delay before alerts fire.

Existing infrastructure metric/log alerts remain unchanged. NSG or NetworkPolicy
blocks can leave pods Ready and will not necessarily fire workload alerts. The
gateway backend-health alert covers only traffic through the running gateway;
the directly exposed store needs a separate end-to-end availability probe.
Enabled rules and action bindings do not prove email delivery or SRE Agent intake.