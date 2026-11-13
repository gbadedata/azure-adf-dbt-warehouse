# The ingest code and the dbt project run as Container Apps jobs: one image, two
# commands, one pair of jobs per environment. Data Factory starts them and waits
# for the result, so all scheduling and alerting lives in one place.

resource "azurerm_container_registry" "this" {
  name                = "acr${var.name_prefix}${local.suffix}"
  location            = azurerm_resource_group.this.location
  resource_group_name = azurerm_resource_group.this.name
  sku                 = "Basic"
  admin_enabled       = false
  tags                = var.tags
}

resource "azurerm_container_app_environment" "this" {
  name                       = "cae-${local.base_name}"
  location                   = azurerm_resource_group.this.location
  resource_group_name        = azurerm_resource_group.this.name
  log_analytics_workspace_id = azurerm_log_analytics_workspace.this.id
  tags                       = var.tags
}

# One identity per environment: it pulls the image and signs in to that
# environment's database only (grants in outputs.sql_grants).
resource "azurerm_user_assigned_identity" "jobs" {
  for_each            = local.environments
  name                = "id-jobs-${each.key}-${local.suffix}"
  location            = azurerm_resource_group.this.location
  resource_group_name = azurerm_resource_group.this.name
  tags                = merge(var.tags, { environment = each.key })
}

resource "azurerm_role_assignment" "jobs_acr_pull" {
  for_each             = local.environments
  scope                = azurerm_container_registry.this.id
  role_definition_name = "AcrPull"
  principal_id         = azurerm_user_assigned_identity.jobs[each.key].principal_id
}

locals {
  common_env = {
    for env in local.environments : env => {
      SQL_SERVER      = azurerm_mssql_server.this.fully_qualified_domain_name
      SQL_DATABASE    = azurerm_mssql_database.warehouse[env].name
      SQL_AUTH        = "msi"
      AZURE_CLIENT_ID = azurerm_user_assigned_identity.jobs[env].client_id
      LOG_LEVEL       = "INFO"
    }
  }

  jobs = {
    for pair in setproduct(tolist(local.environments), ["ingest", "dbt"]) :
    "${pair[1]}-${pair[0]}" => {
      env  = pair[0]
      kind = pair[1]
      command = pair[1] == "ingest" ? ["python", "-m", "ingest", "api"] : [
        "sh", "-c",
        "dbt source freshness --project-dir /app/dbt && dbt build --project-dir /app/dbt"
      ]
      extra_env = pair[1] == "ingest" ? {
        CRM_API_BASE_URL        = local.crm_base_url
        CRM_API_CLIENT_ID       = local.crm_client_id
        INGEST_LOOKBACK_MINUTES = "30"
        } : {
        DBT_TARGET       = pair[0]
        DBT_PROFILES_DIR = "/app/dbt"
      }
    }
  }
}

resource "azurerm_container_app_job" "this" {
  for_each                     = local.jobs
  name                         = "caj-${each.key}-${local.suffix}"
  location                     = azurerm_resource_group.this.location
  resource_group_name          = azurerm_resource_group.this.name
  container_app_environment_id = azurerm_container_app_environment.this.id
  replica_timeout_in_seconds   = 3600
  replica_retry_limit          = 0

  manual_trigger_config {
    parallelism              = 1
    replica_completion_count = 1
  }

  identity {
    type         = "UserAssigned"
    identity_ids = [azurerm_user_assigned_identity.jobs[each.value.env].id]
  }

  registry {
    server   = azurerm_container_registry.this.login_server
    identity = azurerm_user_assigned_identity.jobs[each.value.env].id
  }

  dynamic "secret" {
    for_each = each.value.kind == "ingest" ? [1] : []
    content {
      name  = "crm-client-secret"
      value = local.crm_client_secret
    }
  }

  template {
    container {
      name    = each.value.kind
      image   = var.image
      cpu     = 0.5
      memory  = "1Gi"
      command = each.value.command

      dynamic "env" {
        for_each = merge(local.common_env[each.value.env], each.value.extra_env)
        content {
          name  = env.key
          value = env.value
        }
      }

      dynamic "env" {
        for_each = each.value.kind == "ingest" ? [1] : []
        content {
          name        = "CRM_API_CLIENT_SECRET"
          secret_name = "crm-client-secret"
        }
      }
    }
  }

  tags = merge(var.tags, { environment = each.value.env })

  # After the first deployment the release pipeline owns the image tag
  # (az containerapp job update), so Terraform does not roll it back.
  lifecycle {
    ignore_changes = [template[0].container[0].image]

    precondition {
      condition     = var.deploy_demo_source || (var.crm_api_base_url != "" && var.crm_client_id != "" && var.crm_client_secret != "")
      error_message = "Set crm_api_base_url, crm_client_id and crm_client_secret, or deploy_demo_source = true."
    }
  }

  depends_on = [azurerm_role_assignment.jobs_acr_pull]
}

# Data Factory may start these jobs and read their executions, and nothing else.
resource "azurerm_role_assignment" "adf_runs_jobs" {
  for_each             = local.jobs
  scope                = azurerm_container_app_job.this[each.key].id
  role_definition_name = "Contributor"
  principal_id         = azurerm_data_factory.this.identity[0].principal_id
}
