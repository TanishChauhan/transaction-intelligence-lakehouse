-- Singular test: sanity-check that the rule-based fraud signals catch a reasonable share
-- of the injected ground-truth frauds.
--
-- This is the ONLY place in the project permitted to read is_fraud_label. is_fraud_label
-- is ground truth for validation only; detection logic (fraud_signals) must never see it.
--
-- Recall = flagged labelled frauds / total labelled frauds.
-- The test FAILS (returns a row) only when there are labelled frauds AND recall < 0.30.
-- It PASSES when recall >= 0.30 or when there are no labelled frauds at all.

with labelled as (
    select transaction_id
    from {{ ref('silver_transactions') }}
    where is_fraud_label = true
),

recall as (
    select
        count(*)                                                 as total_labelled_frauds,
        sum(cast(coalesce(fs.is_flagged_fraud, false) as int))   as flagged_labelled_frauds
    from labelled l
    left join {{ ref('fraud_signals') }} fs
        on l.transaction_id = fs.transaction_id
)

select
    total_labelled_frauds,
    flagged_labelled_frauds,
    cast(flagged_labelled_frauds as double) / nullif(total_labelled_frauds, 0) as recall
from recall
where total_labelled_frauds > 0
  and cast(flagged_labelled_frauds as double) / nullif(total_labelled_frauds, 0) < 0.30
