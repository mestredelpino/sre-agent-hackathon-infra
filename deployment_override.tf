resource "azurerm_resource_group" "main" {
  name = var.resource_group_name
}

resource "azurerm_kubernetes_cluster" "main" {
  default_node_pool {
    name           = "system"
    node_count     = 2
    vm_size        = "Standard_D2s_v3"
    vnet_subnet_id = azurerm_subnet.aks.id
    pod_subnet_id  = null
    type           = "VirtualMachineScaleSets"

    upgrade_settings {
      max_surge = "10%"
    }
  }

  network_profile {
    network_plugin      = "azure"
    network_data_plane  = "cilium"
    network_policy      = "cilium"
    network_plugin_mode = "overlay"
    pod_cidr            = "10.244.0.0/16"
    service_cidr        = "10.40.0.0/16"
    dns_service_ip      = "10.40.0.10"

    advanced_networking {
      observability_enabled = true
      security_enabled      = true
    }
  }

  monitor_metrics {
    annotations_allowed = null
    labels_allowed      = null
  }

  node_provisioning_profile {
    mode               = "Auto"
    default_node_pools = "None"
  }

  workload_autoscaler_profile {
    keda_enabled = true
  }

  lifecycle {
    ignore_changes = [microsoft_defender]
  }
}

resource "azurerm_public_ip" "appgw" {
  zones = []

  lifecycle {
    ignore_changes = [ip_tags]
  }
}

resource "azurerm_key_vault" "main" {
  public_network_access_enabled = false
}

resource "azurerm_storage_account" "main" {
  public_network_access_enabled = false
  shared_access_key_enabled     = false
}