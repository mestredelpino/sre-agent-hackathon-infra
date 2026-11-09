variable "availability_test_url" {
  description = "Optional public HTTPS storefront/catalog URL with a publicly trusted certificate. Null disables the paid external probe."
  type        = string
  default     = null

  validation {
    condition     = var.availability_test_url == null ? true : can(regex("^https://[^/?#[:space:]]+", var.availability_test_url))
    error_message = "Provide a public HTTPS URL with a trusted certificate, or null."
  }
}

resource "azurerm_application_insights" "availability" {
  count               = var.availability_test_url == null ? 0 : 1
  name                = "appi-${var.name_prefix}-availability"
  resource_group_name = data.azurerm_resource_group.main.name
  location            = data.azurerm_resource_group.main.location
  workspace_id        = azurerm_log_analytics_workspace.main.id
  application_type    = "web"
  tags                = local.tags
}

resource "azurerm_application_insights_standard_web_test" "storefront" {
  count                   = var.availability_test_url == null ? 0 : 1
  name                    = "probe-${var.name_prefix}-storefront"
  resource_group_name     = data.azurerm_resource_group.main.name
  location                = data.azurerm_resource_group.main.location
  application_insights_id = azurerm_application_insights.availability[0].id
  geo_locations           = ["emea-nl-ams-azr", "emea-gb-db3-azr", "us-ca-sjc-azr"]
  frequency               = 300
  timeout                 = 30
  enabled                 = true
  retry_enabled           = true
  tags                    = local.tags

  request {
    url = var.availability_test_url
  }

  validation_rules {
    expected_status_code        = 200
    ssl_check_enabled           = true
    ssl_cert_remaining_lifetime = 14
  }
}

resource "azurerm_monitor_metric_alert" "storefront_availability" {
  count               = var.availability_test_url == null ? 0 : 1
  name                = "alert-${var.name_prefix}-storefront-availability"
  resource_group_name = data.azurerm_resource_group.main.name
  scopes = [
    azurerm_application_insights_standard_web_test.storefront[0].id,
    azurerm_application_insights.availability[0].id,
  ]
  description   = "HTTPS probe fails from two of three regions, including TLS validity and certificate expiry within 14 days."
  severity      = 1
  frequency     = "PT1M"
  window_size   = "PT5M"
  auto_mitigate = true
  tags          = local.tags

  application_insights_web_test_location_availability_criteria {
    web_test_id           = azurerm_application_insights_standard_web_test.storefront[0].id
    component_id          = azurerm_application_insights.availability[0].id
    failed_location_count = 2
  }

  action { action_group_id = azurerm_monitor_action_group.main.id }
}