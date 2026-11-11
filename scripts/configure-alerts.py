#!/usr/bin/env python3
"""Plan or apply only the alert rules defined in the authoritative Terraform root."""

import argparse
from contextlib import contextmanager
import json
import os
from pathlib import Path
import subprocess
import tempfile


ROOT = Path(__file__).resolve().parents[1]
TARGETS = (
    'azurerm_monitor_metric_alert',
    'azurerm_monitor_scheduled_query_rules_alert_v2',
    'azurerm_monitor_alert_prometheus_rule_group',
)
ADDRESSES = (
    'azurerm_monitor_metric_alert.appgw_unhealthy',
    'azurerm_monitor_metric_alert.aks_node_cpu',
    'azurerm_monitor_metric_alert.incident',
    'azurerm_monitor_scheduled_query_rules_alert_v2.aks_node_not_ready',
    'azurerm_monitor_scheduled_query_rules_alert_v2.aks_pod_restarts',
    'azurerm_monitor_scheduled_query_rules_alert_v2.aks_image_pull_failures',
    'azurerm_monitor_scheduled_query_rules_alert_v2.kv_failures',
    'azurerm_monitor_scheduled_query_rules_alert_v2.storage_auth_failures',
    'azurerm_monitor_scheduled_query_rules_alert_v2.incident',
    'azurerm_monitor_alert_prometheus_rule_group.workload_availability',
)


def checked_changes(plan):
    changes = []
    for resource in plan.get('resource_changes', []):
        actions = resource['change']['actions']
        if actions in (['no-op'], ['read']):
            continue
        if (resource.get('mode') != 'managed' or resource['type'] not in TARGETS
                or actions not in (['create'], ['update'])):
            raise RuntimeError(f"Refusing non-alert or destructive change: {resource['address']} {actions}")
        changes.append((resource['address'], actions[0]))
    return changes


def terraform(*arguments, capture=False):
    return subprocess.run(
        ['terraform', f'-chdir={ROOT}', *arguments], check=True,
        text=True, capture_output=capture,
    )


@contextmanager
def preserve_dependencies():
    state = json.loads(terraform('show', '-json', capture=True).stdout)
    resources = state.get('values', {}).get('root_module', {}).get('resources', [])
    overrides = {}
    for resource in resources:
        if resource.get('mode') == 'managed' and resource['type'] not in TARGETS:
            overrides.setdefault(resource['type'], {})[resource['name']] = {
                'lifecycle': {'ignore_changes': 'all'},
            }
    descriptor, filename = tempfile.mkstemp(prefix='alerts_guard_', suffix='_override.tf.json', dir=ROOT)
    try:
        with os.fdopen(descriptor, 'w') as stream:
            json.dump({'resource': overrides}, stream)
        yield
    finally:
        Path(filename).unlink(missing_ok=True)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--apply', action='store_true', help='Apply the checked alert-only plan')
    arguments = parser.parse_args()
    os.umask(0o077)
    terraform('validate', '-no-color')
    with preserve_dependencies(), tempfile.TemporaryDirectory(prefix='sre-alerts-') as directory:
        plan_path = str(Path(directory) / 'alerts.tfplan')
        terraform('plan', '-input=false', '-no-color', '-lock-timeout=60s',
                  *[f'-target={address}' for address in ADDRESSES], f'-out={plan_path}')
        plan = json.loads(terraform('show', '-json', plan_path, capture=True).stdout)
        changes = checked_changes(plan)
        for address, action in changes:
            print(f'{action}: {address}', flush=True)
        if not changes:
            print('Alert rules already match configuration.')
        elif arguments.apply:
            terraform('apply', '-input=false', '-no-color', plan_path)
            print('Alert-only plan applied. Firing, receiver delivery and agent intake require separate verification.')
        else:
            print('Plan only. Run again with --apply to apply a freshly checked plan.')


if __name__ == '__main__':
    main()