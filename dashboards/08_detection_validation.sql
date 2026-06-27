-- Tile: Detection Validation (confusion matrix + precision / recall)
-- Suggested visualization: Counter tiles + small table (single-row result)
--
-- VALIDATION ONLY. This is the ONLY dashboard query permitted to read is_fraud_label.
-- It is serving/validation analytics (mirroring the singular recall-sanity dbt test): the
-- rule-based detection in gold.fraud_signals NEVER sees is_fraud_label; here we join the
-- predictions to the ground-truth label in silver.silver_transactions purely to score them.
--
-- Confusion matrix:
--   prediction = is_flagged_fraud, truth = is_fraud_label
-- precision = TP / (TP + FP), recall = TP / (TP + FN), flag_rate = flagged / total.
-- All ratios are double-cast (Spark integer division truncates) and nullif-guarded.

with joined as (
    select
        s.transaction_id,
        s.is_flagged_fraud,
        t.is_fraud_label
    from gold.fraud_signals s
    join silver.silver_transactions t
        on s.transaction_id = t.transaction_id
),

matrix as (
    select
        count(*)                                                                              as total_transactions,
        sum(case when is_flagged_fraud and is_fraud_label          then 1 else 0 end)         as true_positives,
        sum(case when is_flagged_fraud and not is_fraud_label      then 1 else 0 end)         as false_positives,
        sum(case when not is_flagged_fraud and is_fraud_label      then 1 else 0 end)         as false_negatives,
        sum(case when not is_flagged_fraud and not is_fraud_label  then 1 else 0 end)         as true_negatives,
        sum(cast(is_flagged_fraud as int))                                                    as total_flagged,
        sum(cast(is_fraud_label as int))                                                      as total_labelled_frauds
    from joined
)

select
    total_transactions,
    true_positives,
    false_positives,
    false_negatives,
    true_negatives,
    cast(true_positives as double) / nullif(total_flagged, 0)          as precision,
    cast(true_positives as double) / nullif(total_labelled_frauds, 0)  as recall,
    cast(total_flagged as double)  / nullif(total_transactions, 0)     as flag_rate
from matrix
