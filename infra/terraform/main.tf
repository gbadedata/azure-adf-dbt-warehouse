resource "random_string" "suffix" {
  length  = 5
  upper   = false
  special = false
}

locals {
  suffix       = random_string.suffix.result
  environments = toset(["dev", "prod"])
  base_name    = "${var.name_prefix}-${local.suffix}"
}

resource "azurerm_resource_group" "this" {
  name     = "rg-${local.base_name}"
  location = var.location
  tags     = var.tags
}

resource "azurerm_log_analytics_workspace" "this" {
  name                = "log-${local.base_name}"
  location            = azurerm_resource_group.this.location
  resource_group_name = azurerm_resource_group.this.name
  sku                 = "PerGB2018"
  retention_in_days   = 30
  tags                = var.tags
}

# ---- Landing storage for file-based sources ------------------------------------
resource "azurerm_storage_account" "landing" {
  name                            = "st${var.name_prefix}${local.suffix}"
  location                        = azurerm_resource_group.this.location
  resource_group_name             = azurerm_resource_group.this.name
  account_tier                    = "Standard"
  account_replication_type        = "LRS"
  min_tls_version                 = "TLS1_2"
  allow_nested_items_to_be_public = false
  shared_access_key_enabled       = false
  tags                            = var.tags
}

resource "azurerm_storage_container" "landing" {
  name                  = "landing"
  storage_account_id    = azurerm_storage_account.landing.id
  container_access_type = "private"
}

# A marker so each environment's incoming folder exists before the first file arrives.
resource "azurerm_storage_blob" "incoming_marker" {
  for_each               = local.environments
  name                   = "${each.key}/incoming/event_registrations/.keep"
  storage_account_name   = azurerm_storage_account.landing.name
  storage_container_name = azurerm_storage_container.landing.name
  type                   = "Block"
  source_content         = "Drop event-registration CSV exports in this folder."
}

# ---- Azure SQL: one serverless database per environment --------------------------
resource "azurerm_mssql_server" "this" {
  name                          = "sql-${local.base_name}"
  location                      = azurerm_resource_group.this.location
  resource_group_name           = azurerm_resource_group.this.name
  version                       = "12.0"
  minimum_tls_version           = "1.2"
  public_network_access_enabled = true

  azuread_administrator {
    login_username              = var.sql_admin_login
    object_id                   = var.sql_admin_object_id
    azuread_authentication_only = true
  }

  tags = var.tags
}

resource "azurerm_mssql_database" "warehouse" {
  for_each                    = local.environments
  name                        = "sqldb-warehouse-${each.key}"
  server_id                   = azurerm_mssql_server.this.id
  sku_name                    = "GP_S_Gen5_1"
  min_capacity                = 0.5
  auto_pause_delay_in_minutes = 60
  max_size_gb                 = 5
  zone_redundant              = false
  storage_account_type        = "Local"
  tags                        = merge(var.tags, { environment = each.key })
}

# Data Factory and Container Apps reach the server over Azure's network. A private
# endpoint is the next step for a stricter set-up (see docs/decisions.md).
resource "azurerm_mssql_firewall_rule" "azure_services" {
  name             = "AllowAzureServices"
  server_id        = azurerm_mssql_server.this.id
  start_ip_address = "0.0.0.0"
  end_ip_address   = "0.0.0.0"
}

resource "azurerm_mssql_firewall_rule" "clients" {
  for_each         = toset(var.client_ip_addresses)
  name             = "client-${replace(each.key, ".", "-")}"
  server_id        = azurerm_mssql_server.this.id
  start_ip_address = each.key
  end_ip_address   = each.key
}
