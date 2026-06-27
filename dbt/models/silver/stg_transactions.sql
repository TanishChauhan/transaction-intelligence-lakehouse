{{ config(materialized="view") }}

-- Type-cast + conform raw bronze transactions and deduplicate on transaction_id
-- (keeping the most recently ingested copy). Minimal, no business logic.

with source as (

    select * from {{ source('bronze', 'transactions_raw') }}

),

deduped as (

    select
        *,
        row_number() over (
            partition by transaction_id
            order by _ingested_at desc
        ) as _row_num
    from source

)

select
    transaction_id,
    cast(event_timestamp as timestamp)        as event_timestamp,
    customer_id,
    card_id,
    merchant_id,
    lower(merchant_category)                  as merchant_category,
    cast(amount as decimal(12, 2))            as amount,
    upper(currency)                           as currency,
    txn_country,
    txn_city,
    cast(txn_lat as double)                   as txn_lat,
    cast(txn_lon as double)                   as txn_lon,
    upper(channel)                            as channel,
    device_id,
    is_fraud_label,  -- validation ground truth only; gold detection must NOT read this
    _source_file,
    _ingested_at
from deduped
where _row_num = 1
