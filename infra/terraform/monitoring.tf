# Pipeline, activity and trigger runs go to Log Analytics, in resource-specific
# tables (ADFPipelineRun, ADFActivityRun, ADFTriggerRun), for investigation and
# for the "no successful run" alert below.
resource "azurerm_monitor_diagnostic_setting" "adf" {
  name                           = "adf-to-log-analytics"
  target_resource_id             = azurerm_data_factory.this.id
  log_analytics_workspace_id     = azurerm_log_analytics_workspace.this.id
  log_analytics_destination_type = "Dedicated"

  enabled_log {
    category = "PipelineRuns"
  }
  enabled_log {
    category = "ActivityRuns"
  }
  enabled_log {
    category = "TriggerRuns"
  }
}

resource "azurerm_monitor_action_group" "data_team" {
  name                = "ag-data-${local.suffix}"
  resource_group_name = azurerm_resource_group.this.name
  short_name          = "datateam"

  dynamic "email_receiver" {
    for_each = toset(var.alert_emails)
    content {
      name                    = "email-${index(var.alert_emails, email_receiver.value)}"
      email_address           = email_receiver.value
      use_common_alert_schema = true
    }
  }

  tags = var.tags
}

# Any failed pipeline run: a load, a file, or dbt (a failed dbt test fails the job,
# which fails the pipeline).
resource "azurerm_monitor_metric_alert" "pipeline_failed" {
  name                = "alert-adf-pipeline-failed-${local.suffix}"
  resource_group_name = azurerm_resource_group.this.name
  scopes              = [azurerm_data_factory.this.id]
  description         = "A Data Factory pipeline run failed. Start with the run in ADF Monitor, then the runbook."
  severity            = 1
  frequency           = "PT5M"
  window_size         = "PT5M"
  auto_mitigate       = false

  criteria {
    metric_namespace = "Microsoft.DataFactory/factories"
    metric_name      = "PipelineFailedRuns"
    aggregation      = "Total"
    operator         = "GreaterThan"
    threshold        = 0
  }

  action {
    action_group_id = azurerm_monitor_action_group.data_team.id
  }

  tags = var.tags
}

# Silence is a failure too: if the trigger is switched off or never fires, nothing
# fails, so this alert fires when prod has had no successful daily run for a day.
resource "azurerm_monitor_scheduled_query_rules_alert_v2" "no_successful_run" {
  name                 = "alert-no-successful-daily-run-${local.suffix}"
  resource_group_name  = azurerm_resource_group.this.name
  location             = azurerm_resource_group.this.location
  scopes               = [azurerm_log_analytics_workspace.this.id]
  description          = "No successful pl_daily_prod run in the last 26 hours."
  severity             = 2
  evaluation_frequency = "PT1H"
  window_duration      = "P1D"
  enabled              = var.daily_schedule["prod"].enabled

  criteria {
    query                   = <<-KQL
      ADFPipelineRun
      | where TimeGenerated > ago(26h)
      | where PipelineName == "pl_daily_prod" and Status == "Succeeded"
    KQL
    time_aggregation_method = "Count"
    operator                = "LessThan"
    threshold               = 1

    failing_periods {
      minimum_failing_periods_to_trigger_alert = 1
      number_of_evaluation_periods             = 1
    }
  }

  action {
    action_groups = [azurerm_monitor_action_group.data_team.id]
  }

  tags = var.tags
}
