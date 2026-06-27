-- Tile: Fraud Score Distribution
-- Suggested visualization: Bar chart / histogram — score_bucket on X, txn_count on Y
--
-- Histogram of the composite rule-based fraud_score from gold.fraud_signals.
-- fraud_score is a weighted sum of the boolean flags (range 0.00 - 1.00); transactions are
-- assigned to fixed-width buckets so the distribution renders as ordered histogram bars.

with bucketed as (
    select
        case
            when fraud_score < 0.25 then '0.00-0.24 (clear)'
            when fraud_score < 0.50 then '0.25-0.49 (low)'
            when fraud_score < 0.75 then '0.50-0.74 (medium)'
            else                          '0.75-1.00 (high)'
        end as score_bucket,
        case
            when fraud_score < 0.25 then 1
            when fraud_score < 0.50 then 2
            when fraud_score < 0.75 then 3
            else                          4
        end as bucket_ord
    from gold.fraud_signals
)

select
    score_bucket,
    count(*) as txn_count
from bucketed
group by score_bucket, bucket_ord
order by bucket_ord
