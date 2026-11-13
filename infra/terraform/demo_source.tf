# Optional: the synthetic supporter CRM API (mock_api) as a public Container App,
# so the whole pipeline can be demonstrated in Azure without a real CRM. Off by
# default; turn on with deploy_demo_source = true. Everything it serves is invented.

resource "random_password" "demo_client_secret" {
  count   = var.deploy_demo_source ? 1 : 0
  length  = 32
  special = false
}

resource "azurerm_user_assigned_identity" "demo_source" {
  count               = var.deploy_demo_source ? 1 : 0
  name                = "id-demo-crm-${local.suffix}"
  location            = azurerm_resource_group.this.location
  resource_group_name = azurerm_resource_group.this.name
  tags                = var.tags
}

resource "azurerm_role_assignment" "demo_source_acr_pull" {
  count                = var.deploy_demo_source ? 1 : 0
  scope                = azurerm_container_registry.this.id
  role_definition_name = "AcrPull"
  principal_id         = azurerm_user_assigned_identity.demo_source[0].principal_id
}

# A new role assignment takes a minute or two to apply everywhere; the app pulls
# its image as soon as it is created, so give the assignment time to land.
resource "time_sleep" "demo_source_acr_pull" {
  count           = var.deploy_demo_source ? 1 : 0
  create_duration = "90s"
  depends_on      = [azurerm_role_assignment.demo_source_acr_pull]
}

resource "azurerm_container_app" "demo_source" {
  count                        = var.deploy_demo_source ? 1 : 0
  name                         = "ca-demo-crm-${local.suffix}"
  resource_group_name          = azurerm_resource_group.this.name
  container_app_environment_id = azurerm_container_app_environment.this.id
  revision_mode                = "Single"
  tags                         = var.tags

  identity {
    type         = "UserAssigned"
    identity_ids = [azurerm_user_assigned_identity.demo_source[0].id]
  }

  registry {
    server   = azurerm_container_registry.this.login_server
    identity = azurerm_user_assigned_identity.demo_source[0].id
  }

  secret {
    name  = "client-secret"
    value = random_password.demo_client_secret[0].result
  }

  ingress {
    external_enabled = true
    target_port      = 8000
    transport        = "auto"

    traffic_weight {
      percentage      = 100
      latest_revision = true
    }
  }

  template {
    # One replica: tokens and the rate-limit counter live in memory.
    min_replicas = 1
    max_replicas = 1

    container {
      name   = "mock-crm"
      image  = var.demo_source_image
      cpu    = 0.25
      memory = "0.5Gi"

      env {
        name  = "MOCK_DATA_PATH"
        value = "/app/data/day1/api.json"
      }
      env {
        name  = "MOCK_CLIENT_ID"
        value = "warehouse-loader"
      }
      env {
        name        = "MOCK_CLIENT_SECRET"
        secret_name = "client-secret"
      }
      env {
        name  = "MOCK_RATE_LIMIT_EVERY"
        value = "7"
      }
    }
  }

  # The demo script switches the source between day 1 and day 2 with
  # `az containerapp update`; do not undo that on the next apply.
  lifecycle {
    ignore_changes = [template[0].container[0].env]

    precondition {
      condition     = var.demo_source_image != ""
      error_message = "Set demo_source_image (built from Dockerfile.mock) when deploy_demo_source is true."
    }
  }

  depends_on = [time_sleep.demo_source_acr_pull]
}

locals {
  crm_base_url      = var.deploy_demo_source ? "https://${azurerm_container_app.demo_source[0].ingress[0].fqdn}" : var.crm_api_base_url
  crm_client_id     = var.deploy_demo_source ? "warehouse-loader" : var.crm_client_id
  crm_client_secret = var.deploy_demo_source ? random_password.demo_client_secret[0].result : var.crm_client_secret
}
