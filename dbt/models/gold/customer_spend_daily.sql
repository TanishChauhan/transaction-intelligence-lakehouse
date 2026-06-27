{{ config(materialized="table") }}

-- Daily spend aggregates per customer (one row per customer per calendar day).

select
    customer_id,
    cast(event_timestamp as date) as spend_date,
    count(*)                      as txn_count,
    sum(amount)                   as total_amount,
    avg(amount)                   as avg_amount
from {{ ref('silver_transactions') }}
group by
    customer_id,
    cast(event_timestamp as date)
