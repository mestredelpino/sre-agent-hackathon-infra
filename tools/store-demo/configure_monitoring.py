import argparse
import json
import subprocess
from urllib.parse import urlparse

from diagram_panels import build_diagram_panels


GRAFANA_AUDIENCE = "ce34e7e5-485f-4d76-964f-b3d2b16d1e4f"


def grafana_request(endpoint, path, body=None):
    command = [
        "az", "rest", "--method", "POST" if body is not None else "GET",
        "--resource", GRAFANA_AUDIENCE, "--url", endpoint + path,
        "--output", "json", "--only-show-errors",
    ]
    if body is not None:
        command.extend(["--body", json.dumps(body)])
    return json.loads(subprocess.check_output(command, text=True))


def build_component_graph(prometheus_uid, workspace_id):
    components = ["store-front", "store-admin", "product-service", "order-service", "rabbitmq", "makeline-service", "documentdb", "virtual-customer", "virtual-worker"]
    dependencies = [
        ("store-front", "product-service"), ("store-front", "order-service"),
        ("store-admin", "product-service"), ("store-admin", "makeline-service"),
        ("virtual-customer", "order-service"), ("order-service", "rabbitmq"),
        ("makeline-service", "rabbitmq"), ("makeline-service", "documentdb"),
        ("virtual-worker", "makeline-service"),
    ]
    expressions = []
    for component in components:
        selector = f'namespace="aks-store-demo",pod=~"{component}-.*",condition="true"'
        readiness = f'(sum(kube_pod_status_ready{{{selector}}}) / count(kube_pod_status_ready{{{selector}}}))'
        for condition, color in [(f"{readiness} == 1", "green"), (f"{readiness} < 1", "red"), (f"absent({readiness}) * -1", "gray")]:
            expressions.append(f'label_replace(label_replace(label_replace(({condition}), "id", "{component}", "", ""), "title", "{component}", "", ""), "color", "{color}", "", "")')
    edge_rows = ",\n".join(json.dumps(value) for index, (source, target) in enumerate(dependencies) for value in [f"dependency-{index}", source, target])
    return {
        "id": 7, "title": "Component health and dependencies", "type": "nodeGraph",
        "description": "Ready-pod fraction: 1 = all Ready; red = not all Ready; gray/-1 = no metrics. Links are configured dependencies, not measured traffic or end-to-end health.",
        "gridPos": {"x": 0, "y": 0, "w": 24, "h": 14},
        "datasource": {"type": "datasource", "uid": "-- Mixed --"},
        "options": {"layoutAlgorithm": "layered", "nodes": {"mainStatUnit": "percentunit"}},
        "targets": [
            {"refId": "nodes", "datasource": {"type": "prometheus", "uid": prometheus_uid}, "expr": " or ".join(expressions), "instant": True, "range": False, "format": "table"},
            {"refId": "edges", "datasource": {"type": "grafana-azure-monitor-datasource", "uid": "azure-monitor-oob"}, "queryType": "Azure Log Analytics", "azureLogAnalytics": {"query": "datatable(id:string, source:string, target:string)[\n" + edge_rows + "\n]", "resources": [workspace_id], "resultFormat": "table"}},
        ],
        "transformations": [{"id": "organize", "filter": {"id": "byRefId", "options": "nodes"}, "options": {"excludeByName": {"Time": True}, "renameByName": {"Value": "mainstat"}}}],
    }


def build_dashboard(prometheus_uid, workspace_id, diagram_plugins=False):
    prometheus = {"type": "prometheus", "uid": prometheus_uid}
    monitor = {"type": "grafana-azure-monitor-datasource", "uid": "azure-monitor-oob"}
    namespace = "aks-store-demo"
    log_query = (
        "let StoreContainers = KubePodInventory\n"
        '| where TimeGenerated > ago(1h) and Namespace == "aks-store-demo"\n'
        "| distinct ContainerID, PodName=Name;\n"
        "ContainerLog | where $__timeFilter(TimeGenerated)\n"
        "| join kind=inner StoreContainers on ContainerID\n"
        "| project TimeGenerated, PodName, Message=tostring(LogEntry), LogSource=LogEntrySource\n"
    )
    definitions = [
        ("Ready pods", "stat", f'sum(kube_pod_status_ready{{namespace="{namespace}",condition="true"}})', "short"),
        ("Container restarts (15m)", "stat", f'sum(increase(kube_pod_container_status_restarts_total{{namespace="{namespace}"}}[15m]))', "short"),
        ("CPU by pod", "timeseries", f'sum by (pod) (rate(container_cpu_usage_seconds_total{{namespace="{namespace}",container!="",container!="POD"}}[5m]))', "cores"),
        ("Memory working set by pod", "timeseries", f'sum by (pod) (container_memory_working_set_bytes{{namespace="{namespace}",container!="",container!="POD"}})', "bytes"),
    ]
    panels = []
    for panel_id, (title, kind, expression, unit) in enumerate(definitions, 1):
        panels.append({
            "id": panel_id, "title": title, "type": kind, "datasource": prometheus,
            "gridPos": {"x": ((panel_id - 1) % 2) * 12, "y": ((panel_id - 1) // 2) * 8, "w": 12, "h": 8},
            "fieldConfig": {"defaults": {"unit": unit}, "overrides": []},
            "targets": [{"refId": "A", "expr": expression, "legendFormat": "{{pod}}", "range": True, "datasource": prometheus}],
        })
    for panel_id, title, kind, query in [
        (5, "Application logs by pod", "timeseries", log_query + "| summarize Entries=count() by bin(TimeGenerated, 1m), PodName\n| order by TimeGenerated asc"),
        (6, "Recent application logs", "table", log_query + "| order by TimeGenerated desc\n| take 200"),
    ]:
        panels.append({
            "id": panel_id, "title": title, "type": kind, "datasource": monitor,
            "gridPos": {"x": 0, "y": 16 if panel_id == 5 else 24, "w": 24, "h": 8 if panel_id == 5 else 12},
            "targets": [{
                "refId": "A", "queryType": "Azure Log Analytics", "datasource": monitor,
                "azureLogAnalytics": {"query": query, "resources": [workspace_id], "resultFormat": "time_series" if kind == "timeseries" else "table"},
            }],
        })
    if diagram_plugins:
        topology = build_diagram_panels(build_component_graph(prometheus_uid, workspace_id))[-1]
        topology["gridPos"]["y"] = 0
        for panel in panels:
            panel["gridPos"]["y"] += topology["gridPos"]["h"]
        panels.append(topology)
    return {
        "uid": "aks-store-monitoring", "title": "AKS Store Demo / Monitoring",
        "schemaVersion": 39, "version": 0, "editable": True,
        "tags": ["aks-store-demo", "hackathon"], "timezone": "utc",
        "time": {"from": "now-30m", "to": "now"}, "refresh": "1m", "panels": panels,
    }


def verify_queries(endpoint, dashboard):
    for panel in dashboard["panels"]:
        target = dict(panel["targets"][0], intervalMs=60000, maxDataPoints=300)
        if panel["type"] == "stat":
            target.update(instant=True, range=False)
        targets = [dict(item, intervalMs=60000, maxDataPoints=300) for item in panel["targets"]] if panel["type"] == "nodeGraph" else [target]
        result = grafana_request(endpoint, "/api/ds/query", {"from": "now-30m", "to": "now", "queries": targets})
        results = result.get("results", {})
        if not results or any(item.get("error") for item in results.values()):
            raise RuntimeError(f"Grafana query failed for {panel['title']}: {json.dumps(results)}")
        values = [values for item in results.values() for frame in item.get("frames", []) for values in frame.get("data", {}).get("values", [])]
        if not any(any(value is not None for value in column) for column in values):
            raise RuntimeError(f"No live data for {panel['title']}; check collection, permissions and ingestion delay")
        print(f"PASS: {panel['title']}")


def main():
    parser = argparse.ArgumentParser(description="Verify managed Grafana metrics/logs and publish the store dashboard.")
    parser.add_argument("--grafana-endpoint", required=True)
    parser.add_argument("--prometheus-datasource-uid", required=True)
    parser.add_argument("--log-workspace-resource-id", required=True)
    parser.add_argument("--verify-only", action="store_true", help="Query existing data sources without publishing a dashboard")
    parser.add_argument("--diagram-plugins", action="store_true", help="Include the installed Graphviz topology panel")
    arguments = parser.parse_args()
    endpoint = arguments.grafana_endpoint.rstrip("/")
    parsed = urlparse(endpoint)
    if parsed.scheme != "https" or not parsed.hostname or not parsed.hostname.endswith(".grafana.azure.com") or parsed.path or parsed.query or parsed.fragment or parsed.username:
        parser.error("Use the HTTPS endpoint of your approved Azure Managed Grafana instance")
    if "/providers/microsoft.operationalinsights/workspaces/" not in arguments.log_workspace_resource_id.lower():
        parser.error("Provide the Azure resource ID of the approved Log Analytics workspace")
    if arguments.diagram_plugins:
        installed = {plugin["id"] for plugin in grafana_request(endpoint, "/api/plugins")}
        required = {"grafana-graphviz-panel"}
        if not required.issubset(installed):
            parser.error("Wait for plugin installation: " + ", ".join(sorted(required - installed)))
    dashboard = build_dashboard(arguments.prometheus_datasource_uid, arguments.log_workspace_resource_id, arguments.diagram_plugins)
    verify_queries(endpoint, dashboard)
    if not arguments.verify_only:
        response = grafana_request(endpoint, "/api/dashboards/db", {"dashboard": dashboard, "overwrite": True, "message": "Configure AKS Store Demo monitoring"})
        if response.get("status") != "success":
            raise RuntimeError(f"Dashboard publication failed: {response}")
        print("Dashboard: " + endpoint + response["url"])


if __name__ == "__main__":
    main()