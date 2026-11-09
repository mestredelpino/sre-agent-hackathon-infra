variable "resource_group_name" {
  description = "Existing deployment resource-group name, supplied through ignored local inputs."
  type        = string
  nullable    = false
  validation {
    condition     = length(trimspace(var.resource_group_name)) > 0
    error_message = "Provide the existing deployment resource-group name."
  }
}

variable "subscription_id" {
  type = string
  validation {
    condition     = can(regex("^[0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{12}$", var.subscription_id))
    error_message = "Provide the intended isolated lab subscription GUID."
  }
}

variable "location" {
  type = string
  validation {
    condition     = can(regex("^[a-z][a-z0-9]+$", var.location))
    error_message = "Provide an explicit Azure region name such as westeurope."
  }
}

variable "name_prefix" {
  description = "Fresh prefix unique to this deployment; verify the resource group is absent."
  type        = string
  validation {
    condition     = can(regex("^[a-z][a-z0-9]{2,9}$", var.name_prefix)) && !startswith(var.name_prefix, "aiops")
    error_message = "Use a fresh 3-10 character lowercase alphanumeric prefix starting with a letter; original aiops names are prohibited."
  }
}

variable "alert_email" {
  description = "Email address to receive alert notifications. Leave empty to skip the email receiver."
  type        = string
  default     = ""
}
