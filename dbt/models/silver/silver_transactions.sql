{{ config(materialized="table") }}

-- Conformed, analytics-ready transaction grain: each clean transaction enriched with
-- the customer spend baseline / home geo and the merchant risk tier / geo, so gold can
-- derive fraud signals without re-joining. is_fraud_label is carried for validation only.

with txn as (
    select * from {{ ref('stg_transactions') }}
),

cust as (
    select * from {{ ref('stg_customers') }}
),

merch as (
    select * from {{ ref('stg_merchants') }}
)

select
    txn.transaction_id,
    txn.event_timestamp,
    txn.customer_id,
    txn.card_id,
    txn.merchant_id,
    txn.merchant_category,
    txn.amount,
    txn.currency,
    txn.txn_country,
    txn.txn_city,
    txn.txn_lat,
    txn.txn_lon,
    txn.channel,
    txn.device_id,

    -- customer baseline + home geo (for amount-anomaly + impossible-travel signals)
    cust.home_country,
    cust.home_lat,
    cust.home_lon,
    cust.typical_txn_amount,
    cust.typical_txn_std,

    -- merchant risk + geo (for high-risk-merchant signal)
    merch.merchant_country,
    merch.merchant_city,
    merch.merchant_lat,
    merch.merchant_lon,
    merch.risk_tier                       as merchant_risk_tier,

    txn.is_fraud_label,  -- VALIDATION ONLY (gold fraud_signals must not reference it)
    txn._source_file,
    txn._ingested_at
from txn
left join cust on txn.customer_id = cust.customer_id
left join merch on txn.merchant_id = merch.merchant_id
