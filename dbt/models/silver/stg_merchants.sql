{{ config(materialized="view") }}

with source as (

    select * from {{ source('bronze', 'merchants_raw') }}

),

deduped as (

    select
        *,
        row_number() over (
            partition by merchant_id
            order by _ingested_at desc
        ) as _row_num
    from source

)

select
    merchant_id,
    merchant_name,
    lower(merchant_category)        as merchant_category,
    merchant_country,
    merchant_city,
    cast(merchant_lat as double)    as merchant_lat,
    cast(merchant_lon as double)    as merchant_lon,
    lower(risk_tier)                as risk_tier
from deduped
where _row_num = 1
