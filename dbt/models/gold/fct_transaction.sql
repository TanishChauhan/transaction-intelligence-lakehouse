{{
    config(
        materialized="incremental",
        unique_key="transaction_id",
        incremental_strategy="merge"
    )
}}

-- Transaction-grain fact. Intentionally clean of is_fraud_label (the label is ground
-- truth for validation only and must never leak into serving marts). Incremental on the
-- event_timestamp watermark so re-runs only process newly arrived events.

select
    transaction_id,
    customer_id,
    card_id,
    merchant_id,
    merchant_category,
    channel,
    amount,
    currency,
    event_timestamp,
    cast(event_timestamp as date) as transaction_date
from {{ ref('silver_transactions') }}

{% if is_incremental() %}
where event_timestamp > (select coalesce(max(event_timestamp), '1900-01-01') from {{ this }})
{% endif %}
