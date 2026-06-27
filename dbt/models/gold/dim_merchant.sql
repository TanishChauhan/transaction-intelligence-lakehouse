{{ config(materialized="table") }}

-- Merchant dimension: one row per merchant, sourced from the conformed staging model.

select
    merchant_id,
    merchant_name,
    merchant_category,
    merchant_country,
    merchant_city,
    merchant_lat,
    merchant_lon,
    risk_tier
from {{ ref('stg_merchants') }}
