# =============================================================================
# Scenario 3 — AKS + AGIC (App Gateway Ingress Controller) + ACR
# Deployable baseline. No deliberate misconfigurations.
# AGIC manages App Gateway backend pool dynamically when an Ingress is created.
# =============================================================================

resource "random_string" "suffix" {
  length  = 6
  special = false
  upper   = false
}

data "azurerm_client_config" "current" {}

locals {
  suffix = random_string.suffix.result
  tags = {
    environment = "hackathon"
    team        = "team3"
    cost-center = "workshop"
    scenario    = "${var.name_prefix}-aks"
  }
}

# --- Resource Group ----------------------------------------------------------
resource "azurerm_resource_group" "main" {
  name     = "rg-${var.name_prefix}-aks"
  location = var.location
  tags     = local.tags
}

# --- Log Analytics -----------------------------------------------------------
resource "azurerm_log_analytics_workspace" "main" {
  name                = "law-${var.name_prefix}-${local.suffix}"
  location            = azurerm_resource_group.main.location
  resource_group_name = azurerm_resource_group.main.name
  sku                 = "PerGB2018"
  retention_in_days   = 90
  tags                = local.tags
}

# --- Key Vault ---------------------------------------------------------------
resource "azurerm_key_vault" "main" {
  name                          = "kv-${var.name_prefix}-${local.suffix}"
  location                      = azurerm_resource_group.main.location
  resource_group_name           = azurerm_resource_group.main.name
  tenant_id                     = data.azurerm_client_config.current.tenant_id
  sku_name                      = "standard"
  soft_delete_retention_days    = 7
  purge_protection_enabled      = false
  rbac_authorization_enabled    = true
  public_network_access_enabled = true

  network_acls {
    default_action = "Deny"
    bypass         = "AzureServices"
  }

  tags = local.tags
}

# --- ACR ---------------------------------------------------------------------
resource "azurerm_container_registry" "main" {
  name                = "acr${replace(var.name_prefix, "-", "")}${local.suffix}"
  resource_group_name = azurerm_resource_group.main.name
  location            = azurerm_resource_group.main.location
  sku                 = "Basic"
  admin_enabled       = false
  tags                = local.tags
}

# --- VNet --------------------------------------------------------------------
resource "azurerm_virtual_network" "main" {
  name                = "vnet-${var.name_prefix}"
  location            = azurerm_resource_group.main.location
  resource_group_name = azurerm_resource_group.main.name
  address_space       = ["10.30.0.0/16"]
  tags                = local.tags
}

resource "azurerm_subnet" "appgw" {
  name                 = "subnet-appgw"
  resource_group_name  = azurerm_resource_group.main.name
  virtual_network_name = azurerm_virtual_network.main.name
  address_prefixes     = ["10.30.0.0/24"]
}

resource "azurerm_subnet" "aks" {
  name                 = "subnet-aks"
  resource_group_name  = azurerm_resource_group.main.name
  virtual_network_name = azurerm_virtual_network.main.name
  address_prefixes     = ["10.30.4.0/22"]
}

resource "azurerm_subnet" "pe" {
  name                 = "subnet-pe"
  resource_group_name  = azurerm_resource_group.main.name
  virtual_network_name = azurerm_virtual_network.main.name
  address_prefixes     = ["10.30.8.0/24"]
}

# --- NSG for App Gateway subnet ---------------------------------------------
resource "azurerm_network_security_group" "appgw" {
  name                = "nsg-${var.name_prefix}-appgw"
  location            = azurerm_resource_group.main.location
  resource_group_name = azurerm_resource_group.main.name
  tags                = local.tags

  security_rule {
    name                       = "AllowGatewayManager"
    priority                   = 100
    direction                  = "Inbound"
    access                     = "Allow"
    protocol                   = "Tcp"
    source_port_range          = "*"
    destination_port_range     = "65200-65535"
    source_address_prefix      = "GatewayManager"
    destination_address_prefix = "*"
  }

  security_rule {
    name                       = "AllowHTTPInbound"
    priority                   = 110
    direction                  = "Inbound"
    access                     = "Allow"
    protocol                   = "Tcp"
    source_port_range          = "*"
    destination_port_range     = "80"
    source_address_prefix      = "Internet"
    destination_address_prefix = "*"
  }

  security_rule {
    name                       = "AllowAzureLoadBalancer"
    priority                   = 120
    direction                  = "Inbound"
    access                     = "Allow"
    protocol                   = "*"
    source_port_range          = "*"
    destination_port_range     = "*"
    source_address_prefix      = "AzureLoadBalancer"
    destination_address_prefix = "*"
  }
}

resource "azurerm_subnet_network_security_group_association" "appgw" {
  subnet_id                 = azurerm_subnet.appgw.id
  network_security_group_id = azurerm_network_security_group.appgw.id
}

# --- App Gateway (managed by AGIC after AKS provisioning) -------------------
resource "azurerm_public_ip" "appgw" {
  name                = "pip-${var.name_prefix}-appgw"
  location            = azurerm_resource_group.main.location
  resource_group_name = azurerm_resource_group.main.name
  allocation_method   = "Static"
  sku                 = "Standard"
  tags                = local.tags
}

resource "azurerm_web_application_firewall_policy" "main" {
  name                = "waf-${var.name_prefix}"
  resource_group_name = azurerm_resource_group.main.name
  location            = azurerm_resource_group.main.location
  tags                = local.tags

  policy_settings {
    enabled                     = true
    mode                        = "Detection"
    request_body_check          = true
    max_request_body_size_in_kb = 128
    file_upload_limit_in_mb     = 100
  }

  managed_rules {
    managed_rule_set {
      type    = "OWASP"
      version = "3.2"
    }
  }
}

locals {
  appgw_name          = "appgw-${var.name_prefix}"
  appgw_fe_ip_config  = "appgw-frontend-ip"
  appgw_fe_port_http  = "port-80"
  appgw_listener_http = "listener-http"
  appgw_backend_pool  = "pool-default"
  appgw_http_settings = "http-settings-default"
  appgw_routing_rule  = "rule-http"
}

resource "azurerm_application_gateway" "main" {
  name                = local.appgw_name
  location            = azurerm_resource_group.main.location
  resource_group_name = azurerm_resource_group.main.name
  firewall_policy_id  = azurerm_web_application_firewall_policy.main.id
  tags                = local.tags

  sku {
    name     = "WAF_v2"
    tier     = "WAF_v2"
    capacity = 1
  }

  gateway_ip_configuration {
    name      = "appgw-ip-config"
    subnet_id = azurerm_subnet.appgw.id
  }

  frontend_port {
    name = local.appgw_fe_port_http
    port = 80
  }

  frontend_ip_configuration {
    name                 = local.appgw_fe_ip_config
    public_ip_address_id = azurerm_public_ip.appgw.id
  }

  backend_address_pool {
    name = local.appgw_backend_pool
  }

  backend_http_settings {
    name                  = local.appgw_http_settings
    cookie_based_affinity = "Disabled"
    port                  = 80
    protocol              = "Http"
    request_timeout       = 30
  }

  http_listener {
    name                           = local.appgw_listener_http
    frontend_ip_configuration_name = local.appgw_fe_ip_config
    frontend_port_name             = local.appgw_fe_port_http
    protocol                       = "Http"
  }

  request_routing_rule {
    name                       = local.appgw_routing_rule
    rule_type                  = "Basic"
    http_listener_name         = local.appgw_listener_http
    backend_address_pool_name  = local.appgw_backend_pool
    backend_http_settings_name = local.appgw_http_settings
    priority                   = 100
  }

  # AGIC rewrites listeners/pools/rules at runtime. Ignore drift.
  lifecycle {
    ignore_changes = [
      backend_address_pool,
      backend_http_settings,
      http_listener,
      probe,
      request_routing_rule,
      url_path_map,
      frontend_port,
      tags,
    ]
  }

  depends_on = [azurerm_subnet_network_security_group_association.appgw]
}

# --- AKS ---------------------------------------------------------------------
resource "azurerm_kubernetes_cluster" "main" {
  depends_on          = [azurerm_role_assignment.aks_subnet, azurerm_role_assignment.aks_pods]
  name                = "aks-${var.name_prefix}-${local.suffix}"
  location            = azurerm_resource_group.main.location
  resource_group_name = azurerm_resource_group.main.name
  dns_prefix          = "aks-${var.name_prefix}-${local.suffix}"
  tags                = local.tags

  default_node_pool {
    name           = "system"
    node_count     = 2
    vm_size        = "Standard_D2s_v3"
    vnet_subnet_id = azurerm_subnet.aks.id
    type           = "VirtualMachineScaleSets"
  }

  identity {
    type         = "UserAssigned"
    identity_ids = [azurerm_user_assigned_identity.aks.id]
  }

  network_profile {
    network_plugin = "azure"
    service_cidr   = "10.40.0.0/16"
    dns_service_ip = "10.40.0.10"
  }

  oms_agent {
    log_analytics_workspace_id = azurerm_log_analytics_workspace.main.id
  }

  ingress_application_gateway {
    gateway_id = azurerm_application_gateway.main.id
  }

  role_based_access_control_enabled = true
}

# --- AcrPull for kubelet identity -------------------------------------------
resource "azurerm_role_assignment" "kubelet_acrpull" {
  scope                = azurerm_container_registry.main.id
  role_definition_name = "AcrPull"
  principal_id         = azurerm_kubernetes_cluster.main.kubelet_identity[0].object_id
}

# --- Contributor for AGIC identity over App Gateway RG ----------------------
resource "azurerm_role_assignment" "agic_contributor" {
  scope                = azurerm_application_gateway.main.id
  role_definition_name = "Contributor"
  principal_id         = azurerm_kubernetes_cluster.main.ingress_application_gateway[0].ingress_application_gateway_identity[0].object_id
}

# --- Diagnostic Settings -----------------------------------------------------
resource "azurerm_monitor_diagnostic_setting" "appgw" {
  name                       = "diag-appgw"
  target_resource_id         = azurerm_application_gateway.main.id
  log_analytics_workspace_id = azurerm_log_analytics_workspace.main.id

  enabled_log {
    category = "ApplicationGatewayAccessLog"
  }
  enabled_log {
    category = "ApplicationGatewayFirewallLog"
  }

  metric {
    category = "AllMetrics"
  }
}

resource "azurerm_monitor_diagnostic_setting" "aks" {
  name                       = "diag-aks"
  target_resource_id         = azurerm_kubernetes_cluster.main.id
  log_analytics_workspace_id = azurerm_log_analytics_workspace.main.id

  enabled_log {
    category = "kube-apiserver"
  }
  enabled_log {
    category = "kube-audit"
  }
  enabled_log {
    category = "kube-controller-manager"
  }

  metric {
    category = "AllMetrics"
  }
}

# --- Storage Account (workload artifacts/data bucket) -----------------------
resource "azurerm_storage_account" "main" {
  name                            = "st${var.name_prefix}${local.suffix}"
  location                        = azurerm_resource_group.main.location
  resource_group_name             = azurerm_resource_group.main.name
  account_tier                    = "Standard"
  account_replication_type        = "LRS"
  min_tls_version                 = "TLS1_2"
  allow_nested_items_to_be_public = false
  public_network_access_enabled   = true
  shared_access_key_enabled       = true

  network_rules {
    default_action = "Deny"
    bypass         = ["AzureServices"]
  }

  tags = local.tags
}

resource "azapi_resource" "data" {
  type      = "Microsoft.Storage/storageAccounts/blobServices/containers@2023-05-01"
  name      = "data"
  parent_id = "${azurerm_storage_account.main.id}/blobServices/default"
  body      = { properties = { publicAccess = "None" } }
}

resource "azurerm_monitor_diagnostic_setting" "storage_blob" {
  log_analytics_destination_type = "Dedicated"
  name                           = "diag-storage-blob"
  target_resource_id             = "${azurerm_storage_account.main.id}/blobServices/default"
  log_analytics_workspace_id     = azurerm_log_analytics_workspace.main.id

  enabled_log { category = "StorageRead" }
  enabled_log { category = "StorageWrite" }
  enabled_log { category = "StorageDelete" }
  metric { category = "Transaction" }
}

# --- Private DNS Zones -------------------------------------------------------
resource "azurerm_private_dns_zone" "kv" {
  name                = "privatelink.vaultcore.azure.net"
  resource_group_name = azurerm_resource_group.main.name
  tags                = local.tags
}

resource "azurerm_private_dns_zone_virtual_network_link" "kv" {
  name                  = "kv-link"
  resource_group_name   = azurerm_resource_group.main.name
  private_dns_zone_name = azurerm_private_dns_zone.kv.name
  virtual_network_id    = azurerm_virtual_network.main.id
  registration_enabled  = false
  tags                  = local.tags
}

resource "azurerm_private_dns_zone" "blob" {
  name                = "privatelink.blob.core.windows.net"
  resource_group_name = azurerm_resource_group.main.name
  tags                = local.tags
}

resource "azurerm_private_dns_zone_virtual_network_link" "blob" {
  name                  = "blob-link"
  resource_group_name   = azurerm_resource_group.main.name
  private_dns_zone_name = azurerm_private_dns_zone.blob.name
  virtual_network_id    = azurerm_virtual_network.main.id
  registration_enabled  = false
  tags                  = local.tags
}

# --- Private Endpoints -------------------------------------------------------
resource "azurerm_private_endpoint" "kv" {
  name                = "pe-${var.name_prefix}-kv"
  location            = azurerm_resource_group.main.location
  resource_group_name = azurerm_resource_group.main.name
  subnet_id           = azurerm_subnet.pe.id
  tags                = local.tags

  private_service_connection {
    name                           = "psc-kv"
    private_connection_resource_id = azurerm_key_vault.main.id
    is_manual_connection           = false
    subresource_names              = ["vault"]
  }

  private_dns_zone_group {
    name                 = "kv-dns-zone-group"
    private_dns_zone_ids = [azurerm_private_dns_zone.kv.id]
  }
}

resource "azurerm_private_endpoint" "storage" {
  name                = "pe-${var.name_prefix}-storage"
  location            = azurerm_resource_group.main.location
  resource_group_name = azurerm_resource_group.main.name
  subnet_id           = azurerm_subnet.pe.id
  tags                = local.tags

  private_service_connection {
    name                           = "psc-storage"
    private_connection_resource_id = azurerm_storage_account.main.id
    is_manual_connection           = false
    subresource_names              = ["blob"]
  }

  private_dns_zone_group {
    name                 = "blob-dns-zone-group"
    private_dns_zone_ids = [azurerm_private_dns_zone.blob.id]
  }
}

# --- Outputs -----------------------------------------------------------------
output "resource_group" {
  value = azurerm_resource_group.main.name
}

output "aks_name" {
  value = azurerm_kubernetes_cluster.main.name
}

output "appgw_public_ip" {
  value = azurerm_public_ip.appgw.ip_address
}

output "acr_login_server" {
  value = azurerm_container_registry.main.login_server
}

output "get_credentials_cmd" {
  value = "az aks get-credentials -g ${azurerm_resource_group.main.name} -n ${azurerm_kubernetes_cluster.main.name}"
}

output "storage_account" {
  value = azurerm_storage_account.main.name
}
