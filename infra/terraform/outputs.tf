output "resource_group" {
  value = azurerm_resource_group.this.name
}

output "sql_server" {
  value = azurerm_mssql_server.this.fully_qualified_domain_name
}

output "databases" {
  value = { for env, db in azurerm_mssql_database.warehouse : env => db.name }
}

output "data_factory" {
  value = azurerm_data_factory.this.name
}

output "container_registry" {
  value = azurerm_container_registry.this.login_server
}

output "container_registry_name" {
  value = azurerm_container_registry.this.name
}

output "subscription_id" {
  value = var.subscription_id
}

output "demo_source" {
  value = var.deploy_demo_source ? { app = azurerm_container_app.demo_source[0].name, url = local.crm_base_url } : null
}

output "jobs" {
  value = { for key, job in azurerm_container_app_job.this : key => job.name }
}

output "landing_storage_account" {
  value = azurerm_storage_account.landing.name
}

# Database users for the managed identities. Terraform cannot run T-SQL here, so
# this renders the script; the runbook says when and how to run it.
output "sql_grants" {
  value = {
    for env in local.environments : env => templatefile("${path.module}/templates/grants.sql.tftpl", {
      env           = env
      server        = azurerm_mssql_server.this.fully_qualified_domain_name
      database      = azurerm_mssql_database.warehouse[env].name
      adf_name      = azurerm_data_factory.this.name
      jobs_identity = azurerm_user_assigned_identity.jobs[env].name
    })
  }
}
