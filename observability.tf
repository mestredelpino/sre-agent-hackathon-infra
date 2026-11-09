resource "azurerm_subnet" "pods" {
  name                 = "subnet-pods"
  resource_group_name  = data.azurerm_resource_group.main.name
  virtual_network_name = azurerm_virtual_network.main.name
  address_prefixes     = ["10.30.12.0/22"]

  delegation {
    name = "aks-pods"
    service_delegation {
      name    = "Microsoft.ContainerService/managedClusters"
      actions = ["Microsoft.Network/virtualNetworks/subnets/join/action"]
    }
  }
}

resource "azurerm_role_assignment" "aks_pods" {
  scope                = azurerm_subnet.pods.id
  role_definition_name = "Network Contributor"
  principal_id         = azurerm_user_assigned_identity.aks.principal_id
}

resource "azurerm_monitor_workspace" "prometheus" {
  name                = "amw-${var.name_prefix}-${local.suffix}"
  resource_group_name = data.azurerm_resource_group.main.name
  location            = data.azurerm_resource_group.main.location
  tags                = local.tags
}

resource "azurerm_dashboard_grafana" "main" {
  name                  = "graf-${var.name_prefix}-${local.suffix}"
  resource_group_name   = data.azurerm_resource_group.main.name
  location              = data.azurerm_resource_group.main.location
  grafana_major_version = "12"
  sku                   = "Standard"
  tags                  = local.tags

  identity {
    type = "SystemAssigned"
  }

  azure_monitor_workspace_integrations {
    resource_id = azurerm_monitor_workspace.prometheus.id
  }
}

resource "azurerm_role_assignment" "grafana_metrics_reader" {
  scope                = azurerm_monitor_workspace.prometheus.id
  role_definition_name = "Monitoring Data Reader"
  principal_id         = azurerm_dashboard_grafana.main.identity[0].principal_id
}

resource "azurerm_role_assignment" "grafana_monitoring_reader" {
  scope                = data.azurerm_resource_group.main.id
  role_definition_name = "Monitoring Reader"
  principal_id         = azurerm_dashboard_grafana.main.identity[0].principal_id
}

resource "azurerm_role_assignment" "grafana_admin" {
  scope                = azurerm_dashboard_grafana.main.id
  role_definition_name = "Grafana Admin"
  principal_id         = var.grafana_admin_group_object_id != null ? var.grafana_admin_group_object_id : data.azurerm_client_config.current.object_id
  principal_type       = var.grafana_admin_group_object_id != null ? "Group" : null
}

resource "azurerm_monitor_data_collection_endpoint" "prometheus" {
  name                = "dce-${var.name_prefix}"
  resource_group_name = data.azurerm_resource_group.main.name
  location            = data.azurerm_resource_group.main.location
  kind                = "Linux"
  tags                = local.tags
}

resource "azurerm_monitor_data_collection_rule" "prometheus" {
  name                        = "dcr-${var.name_prefix}-prometheus"
  resource_group_name         = data.azurerm_resource_group.main.name
  location                    = data.azurerm_resource_group.main.location
  data_collection_endpoint_id = azurerm_monitor_data_collection_endpoint.prometheus.id
  kind                        = "Linux"
  tags                        = local.tags

  destinations {
    monitor_account {
      name               = "prometheus"
      monitor_account_id = azurerm_monitor_workspace.prometheus.id
    }
  }

  data_sources {
    prometheus_forwarder {
      name    = "prometheus"
      streams = ["Microsoft-PrometheusMetrics"]
    }
  }

  data_flow {
    streams      = ["Microsoft-PrometheusMetrics"]
    destinations = ["prometheus"]
  }
}

resource "azurerm_monitor_data_collection_rule_association" "prometheus" {
  name                    = "prometheus"
  target_resource_id      = azurerm_kubernetes_cluster.main.id
  data_collection_rule_id = azurerm_monitor_data_collection_rule.prometheus.id
}

resource "azurerm_monitor_data_collection_rule_association" "prometheus_endpoint" {
  target_resource_id          = azurerm_kubernetes_cluster.main.id
  data_collection_endpoint_id = azurerm_monitor_data_collection_endpoint.prometheus.id
}

output "grafana_endpoint" {
  value = azurerm_dashboard_grafana.main.endpoint
}

output "prometheus_query_endpoint" {
  value = azurerm_monitor_workspace.prometheus.query_endpoint
}