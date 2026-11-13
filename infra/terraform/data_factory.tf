resource "azurerm_data_factory" "this" {
  name                   = "adf-${local.base_name}"
  location               = azurerm_resource_group.this.location
  resource_group_name    = azurerm_resource_group.this.name
  public_network_enabled = true
  tags                   = var.tags

  identity {
    type = "SystemAssigned"
  }
}

# Data Factory reads landing files and moves them to the archive.
resource "azurerm_role_assignment" "adf_blob" {
  scope                = azurerm_storage_account.landing.id
  role_definition_name = "Storage Blob Data Contributor"
  principal_id         = azurerm_data_factory.this.identity[0].principal_id
}

# ---- Linked services: managed identity everywhere, no keys or passwords -----------
resource "azurerm_data_factory_linked_service_azure_blob_storage" "landing" {
  name                 = "ls_landing_blob"
  data_factory_id      = azurerm_data_factory.this.id
  use_managed_identity = true
  service_endpoint     = azurerm_storage_account.landing.primary_blob_endpoint
}

resource "azurerm_data_factory_linked_service_azure_sql_database" "warehouse" {
  for_each             = local.environments
  name                 = "ls_sql_${each.key}"
  data_factory_id      = azurerm_data_factory.this.id
  use_managed_identity = true
  connection_string = join(";", [
    "Integrated Security=False",
    "Encrypt=True",
    "Connection Timeout=60",
    "Data Source=${azurerm_mssql_server.this.fully_qualified_domain_name}",
    "Initial Catalog=${azurerm_mssql_database.warehouse[each.key].name}",
  ])
}

# ---- Datasets ----------------------------------------------------------------------
# Defined with raw JSON so that rowDelimiter is left out: Data Factory then
# accepts \r\n, \r or \n on read. The delimited-text resource sends an empty
# rowDelimiter, which Data Factory rejects at run time.
resource "azurerm_data_factory_custom_dataset" "registrations_file" {
  for_each        = local.environments
  name            = "ds_registrations_file_${each.key}"
  data_factory_id = azurerm_data_factory.this.id
  type            = "DelimitedText"

  linked_service {
    name = azurerm_data_factory_linked_service_azure_blob_storage.landing.name
  }

  parameters = {
    file_name = ""
  }

  type_properties_json = jsonencode({
    location = {
      type       = "AzureBlobStorageLocation"
      container  = azurerm_storage_container.landing.name
      folderPath = "${each.key}/incoming/event_registrations"
      fileName   = { value = "@dataset().file_name", type = "Expression" }
    }
    columnDelimiter  = ","
    quoteChar        = "\""
    escapeChar       = "\""
    firstRowAsHeader = true
    encodingName     = "UTF-8"
  })
}

resource "azurerm_data_factory_dataset_binary" "incoming_folder" {
  for_each            = local.environments
  name                = "ds_incoming_folder_${each.key}"
  data_factory_id     = azurerm_data_factory.this.id
  linked_service_name = azurerm_data_factory_linked_service_azure_blob_storage.landing.name

  azure_blob_storage_location {
    container = azurerm_storage_container.landing.name
    path      = "${each.key}/incoming/event_registrations"
  }
}

resource "azurerm_data_factory_dataset_binary" "incoming_file" {
  for_each            = local.environments
  name                = "ds_incoming_file_${each.key}"
  data_factory_id     = azurerm_data_factory.this.id
  linked_service_name = azurerm_data_factory_linked_service_azure_blob_storage.landing.name

  parameters = {
    file_name = ""
  }

  azure_blob_storage_location {
    container                = azurerm_storage_container.landing.name
    path                     = "${each.key}/incoming/event_registrations"
    filename                 = "@dataset().file_name"
    dynamic_filename_enabled = true
  }
}

resource "azurerm_data_factory_dataset_binary" "archive_file" {
  for_each            = local.environments
  name                = "ds_archive_file_${each.key}"
  data_factory_id     = azurerm_data_factory.this.id
  linked_service_name = azurerm_data_factory_linked_service_azure_blob_storage.landing.name

  parameters = {
    folder    = ""
    file_name = ""
  }

  azure_blob_storage_location {
    container                = azurerm_storage_container.landing.name
    path                     = "@dataset().folder"
    filename                 = "@dataset().file_name"
    dynamic_path_enabled     = true
    dynamic_filename_enabled = true
  }
}

resource "azurerm_data_factory_dataset_azure_sql_table" "registrations" {
  for_each          = local.environments
  name              = "ds_sql_event_registrations_${each.key}"
  data_factory_id   = azurerm_data_factory.this.id
  linked_service_id = azurerm_data_factory_linked_service_azure_sql_database.warehouse[each.key].id
  schema            = "raw"
  table             = "event_registrations"
}

# ---- Pipelines ---------------------------------------------------------------------
locals {
  adf_dir = "${path.module}/../../adf/pipelines"
}

# Starts a Container Apps job, waits for it, and fails if the job fails.
resource "azurerm_data_factory_pipeline" "run_job" {
  for_each        = local.environments
  name            = "pl_run_job_${each.key}"
  data_factory_id = azurerm_data_factory.this.id
  description     = "Start a Container Apps job and wait for its result."
  folder          = each.key

  parameters = {
    job_name = ""
  }

  variables = {
    execution_name = ""
    status         = "Running"
  }

  activities_json = templatefile("${local.adf_dir}/run_job.json.tftpl", {
    subscription_id = var.subscription_id
    resource_group  = azurerm_resource_group.this.name
  })
}

# Upserts each new CSV export into raw.event_registrations, then archives it.
resource "azurerm_data_factory_pipeline" "load_event_files" {
  for_each        = local.environments
  name            = "pl_load_event_files_${each.key}"
  data_factory_id = azurerm_data_factory.this.id
  description     = "Upsert event-registration exports on registration_id, then move each file to the archive."
  folder          = each.key

  activities_json = templatefile("${local.adf_dir}/load_event_files.json.tftpl", {
    env = each.key
  })

  depends_on = [
    azurerm_data_factory_custom_dataset.registrations_file,
    azurerm_data_factory_dataset_binary.incoming_folder,
    azurerm_data_factory_dataset_binary.incoming_file,
    azurerm_data_factory_dataset_binary.archive_file,
    azurerm_data_factory_dataset_azure_sql_table.registrations,
  ]
}

# The daily run: API and files in parallel, then dbt only if both loads succeeded.
resource "azurerm_data_factory_pipeline" "daily" {
  for_each        = local.environments
  name            = "pl_daily_${each.key}"
  data_factory_id = azurerm_data_factory.this.id
  description     = "Daily load: CRM API and event files in parallel, then dbt source freshness and dbt build."
  folder          = each.key

  activities_json = templatefile("${local.adf_dir}/daily.json.tftpl", {
    env        = each.key
    ingest_job = azurerm_container_app_job.this["ingest-${each.key}"].name
    dbt_job    = azurerm_container_app_job.this["dbt-${each.key}"].name
  })

  depends_on = [
    azurerm_data_factory_pipeline.run_job,
    azurerm_data_factory_pipeline.load_event_files,
  ]
}

resource "azurerm_data_factory_trigger_schedule" "daily" {
  for_each        = local.environments
  name            = "tr_daily_${each.key}"
  data_factory_id = azurerm_data_factory.this.id
  activated       = var.daily_schedule[each.key].enabled
  frequency       = "Day"
  interval        = 1
  time_zone       = "UTC"

  schedule {
    hours   = [var.daily_schedule[each.key].hour]
    minutes = [var.daily_schedule[each.key].minute]
  }

  pipeline {
    name = azurerm_data_factory_pipeline.daily[each.key].name
  }
}
