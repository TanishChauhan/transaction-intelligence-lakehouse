-- Tile: Signal Breakdown (which rules fire most)
-- Suggested visualization: Horizontal bar chart — signal_name on Y, flagged_count on X
--
-- Count of transactions triggering each rule-based boolean flag in gold.fraud_signals.
-- Each flag is aggregated separately, then UNION ALL "unpivots" the wide row into tidy
-- (signal_name, flagged_count) rows so the result charts cleanly as a bar.

with counts as (
    select
        sum(cast(flag_velocity          as int)) as flag_velocity,
        sum(cast(flag_impossible_travel as int)) as flag_impossible_travel,
        sum(cast(flag_amount_anomaly    as int)) as flag_amount_anomaly,
        sum(cast(flag_high_risk_merchant as int)) as flag_high_risk_merchant,
        sum(cast(flag_card_testing      as int)) as flag_card_testing
    from gold.fraud_signals
)

select 'flag_velocity'           as signal_name, flag_velocity           as flagged_count from counts
union all
select 'flag_impossible_travel'  as signal_name, flag_impossible_travel  as flagged_count from counts
union all
select 'flag_amount_anomaly'     as signal_name, flag_amount_anomaly     as flagged_count from counts
union all
select 'flag_high_risk_merchant' as signal_name, flag_high_risk_merchant as flagged_count from counts
union all
select 'flag_card_testing'       as signal_name, flag_card_testing       as flagged_count from counts
order by flagged_count desc
