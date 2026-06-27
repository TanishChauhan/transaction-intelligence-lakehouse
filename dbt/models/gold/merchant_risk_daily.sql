{{ config(materialized="table") }}

-- Daily merchant risk aggregates: transaction volume, flagged-transaction volume and the
-- resulting flagged rate per merchant per calendar day, driven by the rule-based signals.

with signals as (
    select
        merchant_id,
        cast(event_timestamp as date) as activity_date,
        is_flagged_fraud
    from {{ ref('fraud_signals') }}
)

select
    merchant_id,
    activity_date,
    count(*)                                            as txn_count,
    sum(cast(is_flagged_fraud as int))                  as flagged_txn_count,
    cast(sum(cast(is_flagged_fraud as int)) as double) / count(*) as flagged_rate
from signals
group by
    merchant_id,
    activity_date
