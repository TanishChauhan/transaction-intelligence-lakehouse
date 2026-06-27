-- Tile: KPI Overview (headline counters)
-- Suggested visualization: Counter tiles (one per metric) — single-row result
--
-- Headline fraud-monitoring KPIs for the whole dataset:
--   total transactions, flagged transactions, flagged rate, total amount, distinct flagged cards.
-- Counts/flags come from gold.fraud_signals (which carries is_flagged_fraud but NOT amount);
-- total transacted amount comes from gold.fct_transaction.
-- Ratio is double-cast to avoid Spark integer division truncation and guarded with nullif.

with signals as (
    select
        count(*)                                                    as total_transactions,
        sum(cast(is_flagged_fraud as int))                          as flagged_transactions,
        count(distinct case when is_flagged_fraud then card_id end) as distinct_flagged_cards
    from gold.fraud_signals
),

amounts as (
    select sum(amount) as total_amount
    from gold.fct_transaction
)

select
    s.total_transactions,
    s.flagged_transactions,
    cast(s.flagged_transactions as double) / nullif(s.total_transactions, 0) as flagged_rate,
    a.total_amount,
    s.distinct_flagged_cards
from signals s
cross join amounts a
