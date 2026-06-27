{{ config(materialized="table") }}

-- Customer dimension: one row per customer, sourced from the conformed staging model.

select
    customer_id,
    customer_name,
    home_country,
    home_city,
    home_lat,
    home_lon,
    account_open_date,
    typical_txn_amount,
    typical_txn_std
from {{ ref('stg_customers') }}
