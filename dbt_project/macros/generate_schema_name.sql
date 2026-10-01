{#
    Use the +schema from dbt_project.yml as-is (staging, mart) instead of dbt's
    default "<target_schema>_<custom_schema>" (staging_staging, staging_mart),
    so the API and Power BI can query mart.fund_performance directly.
#}
{% macro generate_schema_name(custom_schema_name, node) -%}
    {%- if custom_schema_name is none -%}
        {{ target.schema }}
    {%- else -%}
        {{ custom_schema_name | trim }}
    {%- endif -%}
{%- endmacro %}
