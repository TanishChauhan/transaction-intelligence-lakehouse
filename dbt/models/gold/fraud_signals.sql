{{
    config(
        materialized="incremental",
        unique_key="transaction_id",
        incremental_strategy="merge"
    )
}}

-- Rule-based, per-transaction fraud features + composite score.
--
-- BOUNDARY: this model must NEVER reference is_fraud_label. Detection logic is derived
-- purely from observable features (velocity, geo/speed, amount z-score, merchant risk).
-- The label is ground truth reserved for the singular recall-sanity test only.
--
-- Window functions need full per-card history to compute lag()/rolling counts correctly,
-- so features are computed over the complete silver_transactions set in CTEs and the
-- incremental watermark is applied only at the very end (so re-runs merge just the new
-- rows while still seeing prior context for the window calculations).

with base as (
    select
        transaction_id,
        customer_id,
        card_id,
        merchant_id,
        event_timestamp,
        amount,
        txn_lat,
        txn_lon,
        typical_txn_amount,
        typical_txn_std,
        merchant_risk_tier
    from {{ ref('silver_transactions') }}
),

features as (
    select
        transaction_id,
        customer_id,
        card_id,
        merchant_id,
        event_timestamp,
        amount,

        -- velocity: number of txns on this card in the trailing 5-minute window (incl. self)
        count(*) over (
            partition by card_id
            order by event_timestamp
            range between interval 5 minutes preceding and current row
        ) as velocity_count_5m,

        -- card-testing look-back: tiny (amount < 2) txns on this card in the trailing 10 minutes
        sum(case when amount < 2 then 1 else 0 end) over (
            partition by card_id
            order by event_timestamp
            range between interval 10 minutes preceding and current row
        ) as small_txn_count_10m,

        -- seconds elapsed since the previous txn on this card
        unix_timestamp(event_timestamp)
            - unix_timestamp(lag(event_timestamp) over (
                partition by card_id order by event_timestamp
            )) as seconds_since_prev_txn,

        -- great-circle (haversine) distance in km from the previous txn on this card
        (
            2 * 6371 * asin(
                sqrt(least(1.0,
                    pow(sin(radians(
                        txn_lat - lag(txn_lat) over (
                            partition by card_id order by event_timestamp
                        )
                    ) / 2), 2)
                    + cos(radians(lag(txn_lat) over (
                        partition by card_id order by event_timestamp
                    )))
                    * cos(radians(txn_lat))
                    * pow(sin(radians(
                        txn_lon - lag(txn_lon) over (
                            partition by card_id order by event_timestamp
                        )
                    ) / 2), 2)
                ))
            )
        ) as km_from_prev_txn,

        -- amount anomaly vs the customer's spend baseline
        (amount - typical_txn_amount) / nullif(typical_txn_std, 0) as amount_zscore,

        -- high-risk merchant flag
        (merchant_risk_tier = 'high') as is_high_risk_merchant
    from base
),

scored as (
    select
        transaction_id,
        customer_id,
        card_id,
        merchant_id,
        event_timestamp,
        velocity_count_5m,
        small_txn_count_10m,
        seconds_since_prev_txn,
        km_from_prev_txn,

        -- implied travel speed between consecutive txns (km/h)
        km_from_prev_txn / nullif(seconds_since_prev_txn / 3600.0, 0) as speed_kmh,

        amount_zscore,
        is_high_risk_merchant,
        amount,

        -- 5+ txns on a card inside 5 minutes is unusually rapid
        (velocity_count_5m >= 5) as flag_velocity,

        -- impossible travel: > 900 km/h between consecutive txns (faster than a jet)
        (
            km_from_prev_txn / nullif(seconds_since_prev_txn / 3600.0, 0) > 900
            and seconds_since_prev_txn is not null
        ) as flag_impossible_travel,

        -- amount far above the customer's baseline (>= 6 std devs)
        (amount_zscore >= 6) as flag_amount_anomaly,

        -- transaction at a high-risk merchant
        is_high_risk_merchant as flag_high_risk_merchant,

        -- card testing: several tiny probe txns on this card followed by a large charge
        (amount >= 100 and small_txn_count_10m >= 3) as flag_card_testing
    from features
),

final as (
    select
        transaction_id,
        customer_id,
        card_id,
        merchant_id,
        event_timestamp,
        velocity_count_5m,
        seconds_since_prev_txn,
        km_from_prev_txn,
        speed_kmh,
        amount_zscore,
        is_high_risk_merchant,
        flag_velocity,
        flag_impossible_travel,
        flag_amount_anomaly,
        flag_high_risk_merchant,
        flag_card_testing,

        -- composite weighted score over the boolean flags (cast to int)
        round(
            cast(flag_velocity as int) * 0.25
            + cast(flag_impossible_travel as int) * 0.30
            + cast(flag_amount_anomaly as int) * 0.20
            + cast(flag_high_risk_merchant as int) * 0.10
            + cast(flag_card_testing as int) * 0.15,
            2
        ) as fraud_score,

        -- flagged when any meaningful signal fires
        round(
            cast(flag_velocity as int) * 0.25
            + cast(flag_impossible_travel as int) * 0.30
            + cast(flag_amount_anomaly as int) * 0.20
            + cast(flag_high_risk_merchant as int) * 0.10
            + cast(flag_card_testing as int) * 0.15,
            2
        ) >= 0.25 as is_flagged_fraud
    from scored
)

select * from final

{% if is_incremental() %}
where event_timestamp > (select coalesce(max(event_timestamp), '1900-01-01') from {{ this }})
{% endif %}
