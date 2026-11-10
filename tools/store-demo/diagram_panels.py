import json


def build_diagram_panels(graph):
    components = ["store-front", "store-admin", "product-service", "order-service", "rabbitmq", "makeline-service", "documentdb", "virtual-customer", "virtual-worker"]
    cards = "".join(f'<article data-component="{component}" data-state="unknown"><span class="signal"></span><strong>{component}</strong><span class="status">No metrics</span></article>' for component in components)
    html_panel = {
        "id": 8, "title": "Store health / HTML Graphics", "type": "gapit-htmlgraphics-panel",
        "description": graph["description"], "gridPos": {"x": 0, "y": 0, "w": 24, "h": 12},
        "datasource": graph["targets"][0]["datasource"], "targets": [graph["targets"][0]],
        "options": {
            "html": '<section class="store-health">' + cards + '</section>',
            "css": '.store-health{display:grid;grid-template-columns:repeat(3,minmax(0,1fr));gap:16px;padding:20px;font-family:inherit}article{display:grid;grid-template-columns:12px minmax(0,1fr);gap:10px;align-items:center;padding:18px;border:1px solid #718096;border-left:4px solid var(--health,#889099);border-radius:4px;min-width:0}strong{font-size:15px;overflow-wrap:anywhere}.status{grid-column:2;font-size:13px;opacity:.85}.signal{width:9px;height:9px;border-radius:50%;background:var(--health,#889099)}[data-state=ready]{--health:#299c63}[data-state=degraded]{--health:#e05260}@media(max-width:600px){.store-health{grid-template-columns:repeat(2,minmax(0,1fr));padding:8px;gap:8px}article{padding:10px}strong{font-size:12px}}',
            "onRender": '''const states = new Map();
const readValue = (field, index) => typeof field.values.get === 'function' ? field.values.get(index) : field.values[index];
for (const series of htmlGraphics.data.series) {
  const identity = series.fields.find(field => field.name === 'id');
  const metric = series.fields.find(field => field.type === 'number' && field.name !== 'Time');
  if (identity && metric) {
    for (let index = 0; index < identity.values.length; index++) {
      const value = readValue(metric, index);
      if (Number.isFinite(value)) states.set(readValue(identity, index), value);
    }
  }
  for (const field of series.fields) {
    if (!field.labels || !field.labels.id || field.type !== 'number') continue;
    const values = field.values;
    const value = typeof values.get === 'function' ? values.get(values.length - 1) : values[values.length - 1];
    if (Number.isFinite(value)) states.set(field.labels.id, value);
  }
}
for (const element of htmlGraphics.htmlNode.querySelectorAll('[data-component]')) {
  const value = states.get(element.dataset.component);
  const unknown = value === undefined || value < 0 || htmlGraphics.data.state === 'Error';
  element.dataset.state = unknown ? 'unknown' : value >= 1 ? 'ready' : 'degraded';
  element.querySelector('.status').textContent = unknown ? 'No metrics' : value >= 1 ? 'All pods Ready' : Math.round(value * 100) + '% pods Ready';
}''',
            "onInit": "", "renderOnMount": True, "panelupdateOnMount": True,
            "dynamicData": True, "dynamicHtmlGraphics": True, "add100Percentage": True,
            "centerAlignContent": False, "overflow": "auto", "calcsMutation": "none",
            "rootCSS": "*{box-sizing:border-box} .store-health{width:100%;height:auto} article{min-height:90px;padding:12px!important}",
        },
    }
    rows = ",".join(json.dumps(component) for component in components)
    query = (
        'let Expected = datatable(Component:string)[' + rows + '];\n'
        'let Health = KubePodInventory | where TimeGenerated > ago(5m) and Namespace == "aks-store-demo"\n'
        '| summarize arg_max(TimeGenerated, *) by PodUid\n'
        '| extend Component=replace_regex(Name, @"-[a-z0-9]+-[a-z0-9]+$|-\\d+$", "")\n'
        '| summarize Pods=count(), Running=countif(PodStatus =~ "Running") by Component;\n'
      'let Replicas = InsightsMetrics | where TimeGenerated > ago(5m) and Name == "kube_deployment_status_replicas_ready"\n'
      '| extend T=todynamic(Tags) | where tostring(T.k8sNamespace) == "aks-store-demo"\n'
      '| extend Component=tostring(T.deployment) | summarize arg_max(TimeGenerated, *) by Component\n'
      '| project Component, Ready=toint(Val), Desired=toint(T.spec_replicas);\n'
      'let Nodes = Expected | join kind=leftouter Health on Component | join kind=leftouter Replicas on Component\n'
      '| extend State=case(isnotnull(Desired), strcat(Ready, " / ", Desired, " pods Ready"), isnull(Pods), "No recent inventory", strcat(Running, " pods Running; desired ?")), Color=case(isnotnull(Desired) and Ready < Desired, "#a7364b", isnotnull(Desired) and Desired == 0, "#586578", isnotnull(Desired), "#16745b", isnull(Pods), "#586578", Running < Pods, "#a7364b", "#425a70"), Shape=case(Component == "documentdb", "cylinder", Component == "rabbitmq", "component", Component startswith "virtual-", "ellipse", Component startswith "store-", "tab", "box")\n'
      '| extend Line=strcat("\\\"", Component, "\\\" [shape=", Shape, " label=\\\"", Component, "\\\\n", State, "\\\" fillcolor=\\\"", Color, "\\\"]; ")\n'
        '| summarize Lines=strcat_array(make_list(Line), " ");\n'
      'let Entry = datatable(ServiceName:string)["store-front"] | join kind=leftouter (KubeServices | where TimeGenerated > ago(5m) and Namespace == "aks-store-demo" and ServiceName == "store-front" | summarize arg_max(TimeGenerated, *) by ServiceName | project ServiceName, ServiceType) on ServiceName\n'
      '| extend EntryState=iff(isempty(ServiceType), "No recent service inventory", strcat(ServiceType, " / inventory present"))\n'
      '| project EntryLine=strcat(\'"public-entry" [shape=hexagon fillcolor="#425a70" label="PUBLIC ENTRY\\\\nstore-front / \', EntryState, \'\\\\nPublic HTTP: not monitored"]; \');\n'
      'let Edges = datatable(source:string, target:string, interaction:string, color:string, pattern:string)[\n'
      '"public-entry", "store-front", "HTTP :80 / service routing", "#e3b45b", "solid",\n'
      '"store-front", "product-service", "HTTP / catalog", "#63b3ed", "solid",\n'
      '"store-front", "order-service", "HTTP / orders", "#63b3ed", "solid",\n'
      '"store-admin", "product-service", "HTTP / catalog", "#63b3ed", "solid",\n'
      '"store-admin", "makeline-service", "HTTP / fulfillment", "#63b3ed", "solid",\n'
      '"virtual-customer", "order-service", "HTTP / synthetic orders", "#63b3ed", "dashed",\n'
      '"order-service", "rabbitmq", "AMQP / publish", "#e3b45b", "solid",\n'
      '"makeline-service", "rabbitmq", "AMQP / consume", "#e3b45b", "dashed",\n'
      '"makeline-service", "documentdb", "MongoDB / persistence", "#9ccf94", "solid",\n'
      '"virtual-worker", "makeline-service", "HTTP / synthetic worker", "#63b3ed", "dashed"\n'
      '] | extend Line=strcat("\\\"", source, "\\\" -> \\\"", target, "\\\" [label=\\\"", interaction, "\\\" color=\\\"", color, "\\\" fontcolor=\\\"", color, "\\\" style=", pattern, "]; ") | summarize Links=strcat_array(make_list(Line), " ");\n'
      'Nodes | extend JoinKey=1 | join kind=inner (Edges | extend JoinKey=1) on JoinKey | join kind=inner (Entry | extend JoinKey=1) on JoinKey\n'
      '| project dot=strcat(\'digraph Store { graph [bgcolor="transparent" rankdir="LR" nodesep="0.6" ranksep="1.1" pad="0.3" fontname="Helvetica" fontsize="12" fontcolor="#aebdcb" labelloc="b" label="Live replica samples: last 5 min | Links: configured request types, not measured traffic | Request rates unavailable"]; node [style="rounded,filled" fontname="Helvetica" fontsize="13" fontcolor="white" color="#78909c" penwidth="1.3" margin="0.22,0.16"]; edge [fontname="Helvetica" fontsize="10" arrowsize="0.8" penwidth="1.6"]; subgraph cluster_clients { label="CLIENTS"; color="#425466"; style="rounded,dashed"; "store-front"; "store-admin"; "virtual-customer"; "virtual-worker"; } subgraph cluster_services { label="SERVICES"; color="#425466"; style="rounded,dashed"; "product-service"; "order-service"; "makeline-service"; } subgraph cluster_data { label="MESSAGING & DATA"; color="#425466"; style="rounded,dashed"; "rabbitmq"; "documentdb"; } \', Lines, Links, "}")'
    )
    query = query.replace('Lines, Links, "}")', 'Lines, EntryLine, Links, "}")')
    graphviz = {
        "id": 9, "title": "Store topology / Graphviz", "type": "grafana-graphviz-panel",
        "description": "Public entry is the store-front Service, not a Kubernetes Ingress. Service inventory is not public HTTP reachability; no continuous external probe is collected. Deployment Ready / desired counts from InsightsMetrics; StatefulSet desired counts are unknown when not collected. Inventory and replica samples use a five-minute freshness window. Colored links describe configured interactions, not observed traffic; request rates are unavailable. Readiness is not end-to-end health.",
        "gridPos": {"x": 0, "y": 12, "w": 24, "h": 16},
        "datasource": graph["targets"][1]["datasource"],
        "targets": [dict(graph["targets"][1], refId="topology", azureLogAnalytics=dict(graph["targets"][1]["azureLogAnalytics"], query=query))],
        "options": {"inputMode": "query", "dotDiagram": "", "dotQueryConfig": {"fieldName": "dot"}, "layoutEngine": "dot", "rankDirection": "LR", "splineType": "polyline", "namedThresholds": [], "edgeOverrides": [], "nodeOverrides": []},
    }
    return [html_panel, graphviz]