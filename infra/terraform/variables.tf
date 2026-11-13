variable "subscription_id" {
  description = "Azure subscription to deploy into."
  type        = string
}

variable "location" {
  description = "Azure region."
  type        = string
  default     = "uksouth"
}

variable "name_prefix" {
  description = "Short prefix for resource names (lowercase letters and digits)."
  type        = string
  default     = "wh"

  validation {
    condition     = can(regex("^[a-z][a-z0-9]{1,7}$", var.name_prefix))
    error_message = "name_prefix must be 2 to 8 lowercase letters or digits, starting with a letter."
  }
}

variable "sql_admin_login" {
  description = "Display name of the Microsoft Entra user or group that administers the SQL server."
  type        = string
}

variable "sql_admin_object_id" {
  description = "Object ID of that Entra user or group. SQL logins are disabled; Entra authentication only."
  type        = string
}

variable "client_ip_addresses" {
  description = "Public IPs allowed through the SQL firewall, for analysts and administrators."
  type        = list(string)
  default     = []
}

variable "alert_email" {
  description = "Where failure and staleness alerts are sent."
  type        = string
}

variable "image" {
  description = "Image for the first deployment only. Until the release pipeline pushes the real image, jobs use this placeholder and will fail if started."
  type        = string
  default     = "mcr.microsoft.com/k8se/quickstart-jobs:latest"
}

variable "crm_api_base_url" {
  description = "Base URL of the supporter CRM API."
  type        = string
}

variable "crm_client_id" {
  description = "OAuth client ID for the CRM API."
  type        = string
}

variable "crm_client_secret" {
  description = "OAuth client secret for the CRM API. Supply through TF_VAR_crm_client_secret, never a committed file."
  type        = string
  sensitive   = true
}

variable "daily_schedule" {
  description = "UTC time the daily load starts, per environment. Dev is off by default to save cost."
  type = map(object({
    hour    = number
    minute  = number
    enabled = bool
  }))
  default = {
    dev  = { hour = 5, minute = 10, enabled = false }
    prod = { hour = 6, minute = 10, enabled = true }
  }
}

variable "tags" {
  description = "Tags applied to every resource."
  type        = map(string)
  default = {
    workload = "warehouse"
    owner    = "data-analytics"
  }
}
