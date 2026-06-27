-- Tile: Top Risky Merchants
-- Suggested visualization: Table — sortable on flagged_rate / total_txn_count
--
-- Top 20 merchants by flagged rate (volume as tie-breaker). Daily aggregates in
-- gold.merchant_risk_daily are rolled up across all days per merchant, then joined to
-- gold.dim_merchant for name / category / risk_tier.
-- flagged_rate is double-cast (avoid integer-division truncation) and nullif-guarded.

with merchant_agg as (
    select
        merchant_id,
        sum(txn_count)         as total_txn_count,
        sum(flagged_txn_count) as total_flagged_count,
        cast(sum(flagged_txn_count) as double) / nullif(sum(txn_count), 0) as flagged_rate
    from gold.merchant_risk_daily
    group by merchant_id
)

select
    m.merchant_id,
    dm.merchant_name,
    dm.merchant_category,
    dm.risk_tier,
    m.total_txn_count,
    m.total_flagged_count,
    m.flagged_rate
from merchant_agg m
join gold.dim_merchant dm
    on m.merchant_id = dm.merchant_id
where m.total_txn_count > 0
order by m.flagged_rate desc, m.total_txn_count desc
limit 20
