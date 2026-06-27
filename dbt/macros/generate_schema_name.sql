{#-
    Use the custom +schema name verbatim (bronze/silver/gold) instead of dbt's default
    behaviour of prefixing it with the target schema. This gives clean medallion schemas.
-#}
{% macro generate_schema_name(custom_schema_name, node) -%}
    {%- if custom_schema_name is none -%}
        {{ target.schema }}
    {%- else -%}
        {{ custom_schema_name | trim }}
    {%- endif -%}
{%- endmacro %}
