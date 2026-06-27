-- Tile: Daily Spend Trend
-- Suggested visualization: Line chart — spend_date on X, total_spend (and txn_count) on Y
--
-- Overall daily total spend and transaction count, rolled up across all customers from the
-- gold.customer_spend_daily mart (one row per customer per day -> one row per day here).

select
    spend_date,
    sum(total_amount) as total_spend,
    sum(txn_count)    as txn_count
from gold.customer_spend_daily
group by spend_date
order by spend_date
