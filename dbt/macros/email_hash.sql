{#
  SHA-256 of a normalised email, as 64 hex characters. Every model that matches
  people by email uses this one definition, so the keys always agree.
#}
{% macro email_hash(column) -%}
    convert(char(64), hashbytes('SHA2_256', cast(lower(trim({{ column }})) as nvarchar(320))), 2)
{%- endmacro %}
