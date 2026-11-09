variable "allow_store_front_http" {
  description = "Explicit legacy opt-in for public storefront HTTP; keep false for HTTPS-only deployments."
  type        = bool
  default     = false
}

variable "store_front_public_ip" {
  description = "Current store-front LoadBalancer IPv4 address; null disables public HTTP access at the subnet NSG."
  type        = string
  default     = null
  validation {
    condition     = var.store_front_public_ip == null ? true : can(cidrnetmask("${var.store_front_public_ip}/32"))
    error_message = "Provide a single IPv4 address or null."
  }
}

resource "azurerm_network_security_rule" "store_front_http" {
  count                       = var.allow_store_front_http && var.store_front_public_ip != null ? 1 : 0
  name                        = "AllowStoreFrontHTTP"
  priority                    = 1000
  direction                   = "Inbound"
  access                      = "Allow"
  protocol                    = "Tcp"
  source_port_range           = "*"
  destination_port_range      = "80"
  source_address_prefix       = "Internet"
  destination_address_prefix  = "${var.store_front_public_ip}/32"
  resource_group_name         = data.azurerm_resource_group.main.name
  network_security_group_name = "vnet-${var.name_prefix}-subnet-aks-nsg-${var.location}"
}