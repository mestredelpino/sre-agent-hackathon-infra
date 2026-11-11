#!/usr/bin/env python3
"""Deploy an extensible managed Prometheus rule catalog using environment variables."""

import argparse
import json
import os
from pathlib import Path
import re
import subprocess
import tempfile


def az(*arguments):
    result = subprocess.run(['az', *arguments, '--only-show-errors', '-o', 'json'],
                            check=True, capture_output=True, text=True)
    return json.loads(result.stdout) if result.stdout.strip() else None


def required(name):
    value = os.environ.get(name, '').strip()
    if not value:
        raise ValueError(f'Set {name}')
    return value


def build_rules(catalog, namespace, action_group):
    if not re.fullmatch(r'[a-z0-9-]+(?:\|[a-z0-9-]+)*', namespace):
        raise ValueError('ALERT_NAMESPACES must be pipe-separated Kubernetes namespace names')
    rules = []
    names = set()
    for entry in catalog:
        name = entry['alert']
        if name in names or not re.fullmatch(r'[A-Za-z_][A-Za-z0-9_]*', name):
            raise ValueError(f'Duplicate or invalid alert name: {name}')
        names.add(name)
        if entry.get('severity', 2) not in range(5):
            raise ValueError(f'Invalid severity: {name}')
        rules.append({
            'alert': name, 'enabled': True,
            'expression': entry['expression'].replace('${NAMESPACES}', namespace),
            'for': entry.get('for', 'PT5M'), 'severity': entry.get('severity', 2),
            'annotations': {'summary': entry['summary']},
            'actions': [{'actionGroupId': action_group}],
            'resolveConfiguration': {'autoResolved': True, 'timeToResolve': 'PT5M'},
        })
    if not rules:
        raise ValueError('Rule catalog must not be empty')
    return rules


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--apply', action='store_true')
    args = parser.parse_args()
    subscription = required('AZURE_SUBSCRIPTION_ID')
    group = required('AZURE_RESOURCE_GROUP')
    cluster = required('AKS_CLUSTER_NAME')
    workspace = required('AZURE_MONITOR_WORKSPACE_NAME')
    action_name = required('AZURE_ACTION_GROUP_NAME')
    rule_name = required('ALERT_RULE_GROUP_NAME')
    namespace = required('ALERT_NAMESPACES')
    account = az('account', 'show', '--subscription', subscription)
    if account['id'].lower() != subscription.lower():
        raise ValueError('Unexpected Azure subscription')
    def resource(kind, name):
        resource_id = (f'/subscriptions/{subscription}/resourceGroups/{group}'
                       f'/providers/{kind}/{name}')
        return az('resource', 'show', '--ids', resource_id, '--subscription', subscription)

    aks = resource('Microsoft.ContainerService/managedClusters', cluster)
    monitor = resource('Microsoft.Monitor/accounts', workspace)
    action = resource('Microsoft.Insights/actionGroups', action_name)
    action_details = az('resource', 'show', '--ids', action['id'], '--subscription', subscription)
    if not action_details['properties'].get('enabled'):
        raise ValueError('Action group is disabled')
    catalog_path = Path(os.environ.get('ALERT_RULES_FILE', Path(__file__).with_name('alert-rules.json')))
    rules = build_rules(json.loads(catalog_path.read_text()), namespace, action['id'])
    body = {'location': monitor['location'], 'properties': {
        'enabled': True, 'clusterName': aks['name'], 'scopes': [monitor['id']],
        'interval': 'PT1M', 'rules': rules,
    }}
    url = (f'https://management.azure.com/subscriptions/{subscription}/resourceGroups/{group}'
           f'/providers/Microsoft.AlertsManagement/prometheusRuleGroups/{rule_name}?api-version=2023-03-01')
    print(json.dumps({'resource_group': group, 'cluster': cluster, 'rule_group': rule_name,
                      'rules': [rule['alert'] for rule in rules], 'apply': args.apply}, indent=2), flush=True)
    if not args.apply:
        return
    with tempfile.TemporaryDirectory(prefix='azure-alerts-') as directory:
        path = Path(directory) / 'rules.json'
        path.write_text(json.dumps(body))
        az('rest', '--method', 'put', '--url', url, '--body', f'@{path}')
    observed = az('rest', '--method', 'get', '--url', url)['properties']
    if not observed.get('enabled') or observed.get('rules') != rules:
        raise RuntimeError('Azure read-back differs from requested enabled rules; inspect the rule group')
    print(f'PASS: group and all {len(rules)} rules enabled and verified. Notification delivery is not tested.')


if __name__ == '__main__':
    main()