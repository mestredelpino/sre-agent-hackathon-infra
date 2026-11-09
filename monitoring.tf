# =============================================================================
# Scenario 3 — Monitoring, alerts, and SRE Agent remediation RBAC
# =============================================================================

resource "azurerm_monitor_action_group" "main" {
  name                = "ag-${var.name_prefix}-aks"
  resource_group_name = data.azurerm_resource_group.main.name
  short_name          = "s3ops"
  tags                = local.tags

  dynamic "email_receiver" {
    for_each = var.alert_email == "" ? [] : [var.alert_email]
    content {
      name                    = "facilitator"
      email_address           = email_receiver.value
      use_common_alert_schema = true
    }
  }
}

# --- Key Vault diagnostic setting -------------------------------------------
resource "azurerm_monitor_diagnostic_setting" "kv" {
  name                       = "diag-kv"
  target_resource_id         = azurerm_key_vault.main.id
  log_analytics_workspace_id = azurerm_log_analytics_workspace.main.id

  enabled_log { category = "AuditEvent" }
  enabled_log { category = "AzurePolicyEvaluationDetails" }
  metric { category = "AllMetrics" }
}

# --- Metric Alerts ----------------------------------------------------------
resource "azurerm_monitor_metric_alert" "appgw_unhealthy" {
  name                = "alert-appgw-unhealthy-host"
  resource_group_name = data.azurerm_resource_group.main.name
  scopes              = [azurerm_application_gateway.main.id]
  description         = "AGIC backend pool has unhealthy hosts"
  severity            = 1
  frequency           = "PT1M"
  window_size         = "PT5M"
  tags                = local.tags

  criteria {
    metric_namespace = "Microsoft.Network/applicationGateways"
    metric_name      = "UnhealthyHostCount"
    aggregation      = "Average"
    operator         = "GreaterThan"
    threshold        = 0
  }

  action { action_group_id = azurerm_monitor_action_group.main.id }
}

resource "azurerm_monitor_metric_alert" "aks_node_cpu" {
  name                = "alert-aks-node-cpu-high"
  resource_group_name = data.azurerm_resource_group.main.name
  scopes              = [azurerm_kubernetes_cluster.main.id]
  description         = "AKS node CPU > 85% for 15 minutes"
  severity            = 2
  frequency           = "PT5M"
  window_size         = "PT15M"
  tags                = local.tags

  criteria {
    metric_namespace = "Microsoft.ContainerService/managedClusters"
    metric_name      = "node_cpu_usage_percentage"
    aggregation      = "Average"
    operator         = "GreaterThan"
    threshold        = 85
  }

  action { action_group_id = azurerm_monitor_action_group.main.id }
}

# --- Log query alerts (Container Insights / kube logs) ----------------------
resource "azurerm_monitor_scheduled_query_rules_alert_v2" "aks_node_not_ready" {
  skip_query_validation = true
  name                  = "alert-aks-node-not-ready"
  resource_group_name   = data.azurerm_resource_group.main.name
  location              = data.azurerm_resource_group.main.location
  evaluation_frequency  = "PT5M"
  window_duration       = "PT10M"
  scopes                = [azurerm_log_analytics_workspace.main.id]
  severity              = 1
  description           = "One or more AKS nodes are NotReady"
  tags                  = local.tags

  criteria {
    query                   = <<-KQL
      KubeNodeInventory
      | where TimeGenerated > ago(10m)
      | summarize arg_max(TimeGenerated, *) by Computer
      | where Status != "Ready"
    KQL
    time_aggregation_method = "Count"
    operator                = "GreaterThan"
    threshold               = 0
  }

  action {
    action_groups = [azurerm_monitor_action_group.main.id]
  }
}
resource "azurerm_monitor_scheduled_query_rules_alert_v2" "aks_pod_restarts" {
  skip_query_validation = true
  name                  = "alert-aks-pod-restarts"
  resource_group_name   = data.azurerm_resource_group.main.name
  location              = data.azurerm_resource_group.main.location
  evaluation_frequency  = "PT5M"
  window_duration       = "PT15M"
  scopes                = [azurerm_log_analytics_workspace.main.id]
  severity              = 2
  description           = "Pods restarting frequently (CrashLoopBackOff)"
  tags                  = local.tags

  criteria {
    query                   = <<-KQL
      KubePodInventory
      | where TimeGenerated > ago(15m)
      | summarize Restarts = max(PodRestartCount) by Namespace, Name
      | where Restarts > 5
    KQL
    time_aggregation_method = "Count"
    operator                = "GreaterThan"
    threshold               = 0
  }

  action {
    action_groups = [azurerm_monitor_action_group.main.id]
  }
}

resource "azurerm_monitor_scheduled_query_rules_alert_v2" "aks_image_pull_failures" {
  skip_query_validation = true
  name                  = "alert-aks-image-pull-failures"
  resource_group_name   = data.azurerm_resource_group.main.name
  location              = data.azurerm_resource_group.main.location
  evaluation_frequency  = "PT5M"
  window_duration       = "PT15M"
  scopes                = [azurerm_log_analytics_workspace.main.id]
  severity              = 2
  description           = "ImagePullBackOff / ErrImagePull events detected"
  tags                  = local.tags

  criteria {
    query                   = <<-KQL
      KubeEvents
      | where TimeGenerated > ago(15m)
      | where Reason has_any ("Failed", "BackOff", "ErrImagePull", "ImagePullBackOff")
      | summarize Events = count() by Namespace, Name, Reason
      | where Events > 2
    KQL
    time_aggregation_method = "Count"
    operator                = "GreaterThan"
    threshold               = 0
  }

  action {
    action_groups = [azurerm_monitor_action_group.main.id]
  }
}

resource "azurerm_monitor_scheduled_query_rules_alert_v2" "kv_failures" {
  skip_query_validation = true
  name                  = "alert-kv-failures"
  resource_group_name   = data.azurerm_resource_group.main.name
  location              = data.azurerm_resource_group.main.location
  evaluation_frequency  = "PT5M"
  window_duration       = "PT5M"
  scopes                = [azurerm_log_analytics_workspace.main.id]
  severity              = 2
  description           = "Key Vault denied or failed requests"
  tags                  = local.tags

  criteria {
    query                   = <<-KQL
      AzureDiagnostics
      | where ResourceProvider == "MICROSOFT.KEYVAULT"
      | where ResultType !in ("Success")
      | summarize Failures = count() by ResultType, OperationName
      | where Failures > 3
    KQL
    time_aggregation_method = "Count"
    operator                = "GreaterThan"
    threshold               = 0
  }

  action {
    action_groups = [azurerm_monitor_action_group.main.id]
  }
}

resource "azurerm_monitor_scheduled_query_rules_alert_v2" "storage_auth_failures" {
  skip_query_validation = true
  name                  = "alert-storage-auth-failures"
  resource_group_name   = data.azurerm_resource_group.main.name
  location              = data.azurerm_resource_group.main.location
  evaluation_frequency  = "PT5M"
  window_duration       = "PT10M"
  scopes                = [azurerm_log_analytics_workspace.main.id]
  severity              = 2
  description           = "Storage authorization failures"
  tags                  = local.tags

  criteria {
    query                   = <<-KQL
      StorageBlobLogs
      | where StatusText has_any ("AuthorizationFailure", "AuthenticationFailed", "AuthorizationPermissionMismatch")
      | summarize Failures = count() by StatusText, OperationName
      | where Failures > 3
    KQL
    time_aggregation_method = "Count"
    operator                = "GreaterThan"
    threshold               = 0
  }

  action {
    action_groups = [azurerm_monitor_action_group.main.id]
  }
}
