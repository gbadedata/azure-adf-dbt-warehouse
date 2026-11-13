{#
  Remove throwaway schemas, for example after a CI run or a closed pull request:
    dbt run-operation drop_schemas_with_prefix --args "{prefix: ci_1234}"
  Refuses to touch the deployed schemas or anything outside dev and CI.
#}
{% macro drop_schemas_with_prefix(prefix) %}
    {% if target.name not in ['ci', 'local'] %}
        {{ exceptions.raise_compiler_error("drop_schemas_with_prefix only runs against the ci or local targets") }}
    {% endif %}
    {% if prefix | length < 3 or prefix in ['staging', 'intermediate', 'marts', 'raw', 'ctl', 'analytics', 'dbo'] %}
        {{ exceptions.raise_compiler_error("refusing to drop schemas with prefix '" ~ prefix ~ "'") }}
    {% endif %}
    {% set query %}
        select name from sys.schemas where name like '{{ prefix | replace("_", "[_]") }}%'
    {% endset %}
    {% set schemas = run_query(query).columns[0].values() %}
    {% for schema in schemas %}
        {% do adapter.drop_schema(api.Relation.create(database=target.database, schema=schema)) %}
        {% do log("dropped schema " ~ schema, info=true) %}
    {% endfor %}
    {% if schemas | length == 0 %}
        {% do log("no schemas start with " ~ prefix, info=true) %}
    {% endif %}
{% endmacro %}
