mock_provider "azurerm" {
  mock_data "azurerm_client_config" {
    defaults = {
      tenant_id       = "00000000-0000-0000-0000-000000000001"
      object_id       = "00000000-0000-0000-0000-000000000002"
      subscription_id = "00000000-0000-0000-0000-000000000001"
    }
  }

  mock_data "azurerm_resource_group" {
    defaults = {
      name     = "rg-example"
      location = "westeurope"
      id       = "/subscriptions/00000000-0000-0000-0000-000000000001/resourceGroups/rg-example"
    }
  }
}

mock_provider "azapi" {}
mock_provider "random" {}

variables {
  resource_group_name   = "rg-example"
  subscription_id       = "00000000-0000-0000-0000-000000000001"
  location              = "westeurope"
  name_prefix           = "example"
  store_front_public_ip = "192.0.2.1"
}

run "https_only_gateway" {
  command = plan

  override_resource {
    target = azurerm_kubernetes_cluster.main
    values = {
      kubelet_identity = [{
        client_id                 = "00000000-0000-0000-0000-000000000003"
        object_id                 = "00000000-0000-0000-0000-000000000004"
        user_assigned_identity_id = "/subscriptions/00000000-0000-0000-0000-000000000001/resourceGroups/rg-example/providers/Microsoft.ManagedIdentity/userAssignedIdentities/kubelet"
      }]
    }
  }

  assert {
    condition     = alltrue([for listener in azurerm_application_gateway.main.http_listener : listener.protocol == "Https" && listener.ssl_certificate_name == "sre-lab-tls"])
    error_message = "Every bootstrap listener must use HTTPS and the lab certificate."
  }

  assert {
    condition     = alltrue([for port in azurerm_application_gateway.main.frontend_port : port.port == 443])
    error_message = "The gateway must expose only port 443."
  }

  assert {
    condition     = length(azurerm_network_security_rule.store_front_http) == 0
    error_message = "A storefront public IP alone must not enable legacy HTTP access."
  }

  assert {
    condition     = alltrue([for rule in azurerm_network_security_group.appgw.security_rule : rule.source_address_prefix != "Internet" || rule.destination_port_range == "443"])
    error_message = "Gateway rules from the Internet must allow only port 443."
  }
}