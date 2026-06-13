-- mart_revenue.sql
-- Daily revenue aggregation for dashboards and anomaly detection.
-- This is the table the currency bug makes look insane.

WITH fct AS (
    SELECT * FROM {{ ref('fct_orders') }}
),

daily AS (
    SELECT
        order_date,
        order_year,
        order_month,
        market_segment,
        order_status_label,

        COUNT(DISTINCT order_key)               AS order_count,
        COUNT(*)                                AS lineitem_count,
        SUM(quantity)                           AS total_quantity,
        ROUND(SUM(extended_price), 2)           AS gross_revenue,
        ROUND(SUM(net_price), 2)                AS net_revenue,
        ROUND(SUM(gross_price), 2)              AS total_revenue_with_tax,
        ROUND(AVG(net_price), 2)                AS avg_lineitem_value,
        ROUND(SUM(net_price) / NULLIF(COUNT(DISTINCT order_key), 0), 2) AS revenue_per_order,

        CURRENT_TIMESTAMP()                     AS _loaded_at

    FROM fct
    GROUP BY 1, 2, 3, 4, 5
)

SELECT * FROM daily
ORDER BY order_date DESC
