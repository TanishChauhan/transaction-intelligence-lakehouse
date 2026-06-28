{#-
    Force every model to materialize in the project catalog (TIL_CATALOG env var, default
    txn_intelligence) regardless of the connection profile's catalog. On Databricks the
    managed dbt task auto-generates a profile whose catalog is not our project catalog, so
    overriding generate_database_name keeps all relations fully qualified to the right UC
    catalog.
-#}
{% macro generate_database_name(custom_database_name=none, node=none) -%}
    {%- if custom_database_name is not none -%}
        {{ custom_database_name | trim }}
    {%- else -%}
        {{ env_var('TIL_CATALOG', 'txn_intelligence') }}
    {%- endif -%}
{%- endmacro %}
