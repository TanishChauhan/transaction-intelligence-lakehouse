-- Tile: Top Flagged Customers
-- Suggested visualization: Table — sortable on flagged_txn_count
--
-- Top 20 customers by number of flagged transactions. Per-transaction flags from
-- gold.fraud_signals are aggregated per customer, then joined to gold.dim_customer for
-- name / home_country.
-- flagged_rate is double-cast (avoid integer-division truncation) and nullif-guarded.

with customer_agg as (
    select
        customer_id,
        count(*)                           as total_txn_count,
        sum(cast(is_flagged_fraud as int)) as flagged_txn_count
    from gold.fraud_signals
    group by customer_id
)

select
    c.customer_id,
    dc.customer_name,
    dc.home_country,
    c.total_txn_count,
    c.flagged_txn_count,
    cast(c.flagged_txn_count as double) / nullif(c.total_txn_count, 0) as flagged_rate
from customer_agg c
join gold.dim_customer dc
    on c.customer_id = dc.customer_id
order by c.flagged_txn_count desc, flagged_rate desc
limit 20
