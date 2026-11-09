locals {
  incident_metric_alerts = {
    gateway_failed_requests = {
      scope       = azurerm_application_gateway.main.id
      namespace   = "Microsoft.Network/applicationGateways"
      metric      = "FailedRequests"
      aggregation = "Total"
      threshold   = 10
      window      = "PT5M"
      severity    = 1
      per_node    = false
      description = "More than 10 failed gateway requests in five minutes; inspect backend health and gateway access logs."
    }
    gateway_latency = {
      scope       = azurerm_application_gateway.main.id
      namespace   = "Microsoft.Network/applicationGateways"
      metric      = "ApplicationGatewayTotalTime"
      aggregation = "Average"
      threshold   = 2000
      window      = "PT10M"
      severity    = 2
      per_node    = false
      description = "Gateway average request processing time exceeds 2000 ms over ten minutes."
    }
    node_memory = {
      scope       = azurerm_kubernetes_cluster.main.id
      namespace   = "Microsoft.ContainerService/managedClusters"
      metric      = "node_memory_working_set_percentage"
      aggregation = "Average"
      threshold   = 90
      window      = "PT10M"
      severity    = 2
      per_node    = true
      description = "An AKS node averages more than 90 percent working-set memory over ten minutes."
    }
    node_disk = {
      scope       = azurerm_kubernetes_cluster.main.id
      namespace   = "Microsoft.ContainerService/managedClusters"
      metric      = "node_disk_usage_percentage"
      aggregation = "Maximum"
      threshold   = 90
      window      = "PT10M"
      severity    = 2
      per_node    = true
      description = "An AKS node disk exceeds 90 percent usage; inspect image storage, logs and ephemeral storage."
    }
  }

  incident_log_alerts = {
    pending_pods = {
      severity    = 2
      query       = <<-KQL
        KubePodInventory
        | where TimeGenerated > ago(10m)
        | where ClusterId =~ "${azurerm_kubernetes_cluster.main.id}"
        | summarize arg_max(TimeGenerated, *) by Namespace, PodUid, Name
        | where PodStatus =~ "Pending" and PodCreationTimeStamp < ago(10m)
      KQL
      description = "Pods older than ten minutes are still Pending; check scheduling, capacity, PVCs and image pulls."
    }
    oom_killed = {
      severity    = 2
      query       = <<-KQL
        KubePodInventory
        | where TimeGenerated > ago(10m)
        | where ClusterId =~ "${azurerm_kubernetes_cluster.main.id}"
        | where ContainerStatusReason =~ "OOMKilled" or ContainerLastStatus has "OOMKilled"
        | summarize arg_max(TimeGenerated, *) by Namespace, PodUid, ContainerName
      KQL
      description = "OOMKilled container status observed; the last termination reason can remain until a subsequent termination."
    }
    node_pressure = {
      severity    = 1
      query       = <<-KQL
        KubeNodeInventory
        | where TimeGenerated > ago(10m)
        | where ClusterId =~ "${azurerm_kubernetes_cluster.main.id}"
        | summarize arg_max(TimeGenerated, *) by Computer
        | where Status has_any ("MemoryPressure", "DiskPressure", "PIDPressure")
      KQL
      description = "An AKS node reports memory, disk or PID pressure."
    }
    missing_inventory = {
      severity    = 1
      query       = <<-KQL
        KubeNodeInventory
        | where TimeGenerated > ago(15m)
        | where ClusterId =~ "${azurerm_kubernetes_cluster.main.id}"
        | summarize Samples = count()
        | where Samples == 0
      KQL
      description = "No node inventory for this cluster in 15 minutes; check Container Insights collection and ingestion."
    }
    gateway_5xx = {
      severity    = 1
      query       = <<-KQL
        AzureDiagnostics
        | where TimeGenerated > ago(10m)
        | where ResourceId =~ "${azurerm_application_gateway.main.id}"
        | where Category == "ApplicationGatewayAccessLog"
        | summarize Requests = count(), Errors = countif(toint(httpStatus_d) between (500 .. 599))
        | where Requests >= 20 and Errors >= 5
        | where 100.0 * Errors / Requests > 5
      KQL
      description = "Gateway HTTP 5xx exceeds 5 percent with at least 20 requests and five errors in ten minutes."
    }
  }
}

resource "azurerm_monitor_metric_alert" "incident" {
  for_each            = local.incident_metric_alerts
  name                = "alert-${var.name_prefix}-${replace(each.key, "_", "-")}"
  resource_group_name = data.azurerm_resource_group.main.name
  scopes              = [each.value.scope]
  description         = each.value.description
  severity            = each.value.severity
  frequency           = "PT1M"
  window_size         = each.value.window
  auto_mitigate       = true
  tags                = local.tags

  criteria {
    metric_namespace = each.value.namespace
    metric_name      = each.value.metric
    aggregation      = each.value.aggregation
    operator         = "GreaterThan"
    threshold        = each.value.threshold

    dynamic "dimension" {
      for_each = each.key == "node_disk" ? ["node", "device"] : (each.value.per_node ? ["node"] : [])
      content {
        name     = dimension.value
        operator = "Include"
        values   = ["*"]
      }
    }
  }

  action { action_group_id = azurerm_monitor_action_group.main.id }
}

resource "azurerm_monitor_scheduled_query_rules_alert_v2" "incident" {
  for_each                = local.incident_log_alerts
  name                    = "alert-${var.name_prefix}-${replace(each.key, "_", "-")}"
  resource_group_name     = data.azurerm_resource_group.main.name
  location                = data.azurerm_resource_group.main.location
  scopes                  = [azurerm_log_analytics_workspace.main.id]
  description             = each.value.description
  severity                = each.value.severity
  evaluation_frequency    = "PT5M"
  window_duration         = "PT15M"
  auto_mitigation_enabled = true
  skip_query_validation   = false
  tags                    = local.tags

  criteria {
    query                   = each.value.query
    time_aggregation_method = "Count"
    operator                = "GreaterThan"
    threshold               = 0
  }

  action { action_groups = [azurerm_monitor_action_group.main.id] }
}

resource "azurerm_monitor_alert_prometheus_rule_group" "workload_availability" {
  name                = "alerts-${var.name_prefix}-workload-availability"
  resource_group_name = data.azurerm_resource_group.main.name
  location            = data.azurerm_resource_group.main.location
  scopes              = [azurerm_monitor_workspace.prometheus.id]
  cluster_name        = azurerm_kubernetes_cluster.main.name
  interval            = "PT1M"
  tags                = local.tags

  rule {
    alert      = "DeploymentReplicasUnavailable"
    expression = "(kube_deployment_spec_replicas > 0) and on(cluster, namespace, deployment) (kube_deployment_status_replicas_available < kube_deployment_spec_replicas)"
    for        = "PT10M"
    severity   = 1
    annotations = {
      summary = "Deployment {{ $labels.namespace }}/{{ $labels.deployment }} has fewer available replicas than requested for ten minutes."
    }
    action { action_group_id = azurerm_monitor_action_group.main.id }
    alert_resolution {
      auto_resolved   = true
      time_to_resolve = "PT5M"
    }
  }

  rule {
    alert      = "StatefulSetReplicasUnavailable"
    expression = "(kube_statefulset_status_replicas > 0) and on(cluster, namespace, statefulset) (kube_statefulset_status_replicas_ready < kube_statefulset_status_replicas)"
    for        = "PT10M"
    severity   = 1
    annotations = {
      summary = "StatefulSet {{ $labels.namespace }}/{{ $labels.statefulset }} has unready current replicas for ten minutes; inspect database health and storage."
    }
    action { action_group_id = azurerm_monitor_action_group.main.id }
    alert_resolution {
      auto_resolved   = true
      time_to_resolve = "PT5M"
    }
  }

  rule {
    alert      = "PersistentVolumeSpaceLow"
    expression = "(100 * kubelet_volume_stats_used_bytes / kubelet_volume_stats_capacity_bytes > 85) and (kubelet_volume_stats_capacity_bytes > 0)"
    for        = "PT10M"
    severity   = 2
    annotations = {
      summary = "PVC {{ $labels.namespace }}/{{ $labels.persistentvolumeclaim }} exceeds 85 percent capacity for ten minutes."
    }
    action { action_group_id = azurerm_monitor_action_group.main.id }
    alert_resolution {
      auto_resolved   = true
      time_to_resolve = "PT5M"
    }
  }

  rule {
    alert      = "KubernetesMetricsMissing"
    expression = "absent(kube_node_info)"
    for        = "PT15M"
    severity   = 1
    annotations = {
      summary = "No kube_node_info series for this cluster for 15 minutes; inspect managed Prometheus collection."
    }
    action { action_group_id = azurerm_monitor_action_group.main.id }
    alert_resolution {
      auto_resolved   = true
      time_to_resolve = "PT5M"
    }
  }
}