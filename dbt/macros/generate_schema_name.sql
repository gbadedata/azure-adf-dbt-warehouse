{#
  Deployed environments (dev, prod) have a database each, so models go to plain
  schema names: staging, intermediate, marts.
  Everyone else (a developer, a CI run) shares the dev database, so their models
  are prefixed with their own schema: dbt_alex_marts, ci_1234_marts. Two people
  can work at once without overwriting each other or the deployed models.
#}
{% macro generate_schema_name(custom_schema_name, node) -%}
    {%- if custom_schema_name is none -%}
        {{ target.schema }}
    {%- elif target.name in ['dev', 'prod'] -%}
        {{ custom_schema_name | trim }}
    {%- else -%}
        {{ target.schema }}_{{ custom_schema_name | trim }}
    {%- endif -%}
{%- endmacro %}
