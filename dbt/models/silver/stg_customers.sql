{{ config(materialized="view") }}

with source as (

    select * from {{ source('bronze', 'customers_raw') }}

),

deduped as (

    select
        *,
        row_number() over (
            partition by customer_id
            order by _ingested_at desc
        ) as _row_num
    from source

)

select
    customer_id,
    name                                  as customer_name,
    home_country,
    home_city,
    cast(home_lat as double)              as home_lat,
    cast(home_lon as double)              as home_lon,
    cast(account_open_date as date)       as account_open_date,
    cast(typical_txn_amount as double)    as typical_txn_amount,
    cast(typical_txn_std as double)       as typical_txn_std
from deduped
where _row_num = 1
