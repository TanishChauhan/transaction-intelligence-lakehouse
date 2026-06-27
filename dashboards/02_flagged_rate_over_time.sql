-- Tile: Flagged Rate Over Time
-- Suggested visualization: Combo chart — bars for txn_count + flagged_count, line for flagged_rate
--
-- Daily transaction volume vs flagged volume and the resulting flagged rate, aggregated
-- straight from the per-transaction gold.fraud_signals model by calendar day.
-- flagged_rate is double-cast (Spark integer division truncates) and nullif-guarded.

select
    cast(event_timestamp as date)                                              as activity_date,
    count(*)                                                                   as txn_count,
    sum(cast(is_flagged_fraud as int))                                         as flagged_count,
    cast(sum(cast(is_flagged_fraud as int)) as double) / nullif(count(*), 0)   as flagged_rate
from gold.fraud_signals
group by cast(event_timestamp as date)
order by activity_date
