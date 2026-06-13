-- fct_orders.sql
-- Order fact table joining orders → lineitems with correct grain.
-- Each row = one lineitem. Avoids fanout by joining on both keys.

WITH orders AS (
    SELECT * FROM {{ ref('stg_orders') }}
),

lineitems AS (
    SELECT * FROM {{ ref('stg_lineitems') }}
),

customers AS (
    SELECT * FROM {{ ref('stg_customers') }}
),

joined AS (
    SELECT
        -- Keys
        o.order_key,
        o.customer_key,
        l.line_number,
        l.part_key,
        l.supplier_key,

        -- Order attributes
        o.order_date,
        o.order_year,
        o.order_month,
        o.order_status,
        o.order_status_label,
        o.order_priority,
        o.clerk_id,

        -- Line item financials
        l.quantity,
        l.extended_price,
        l.discount_pct,
        l.tax_pct,
        l.net_price,
        l.gross_price,
        l.ship_date,
        l.ship_mode,

        -- Customer context
        c.customer_name,
        c.market_segment,
        c.account_balance,

        -- Metadata
        CURRENT_TIMESTAMP() AS _loaded_at

    FROM orders o
    -- Correct grain: one row per order line
    JOIN lineitems l ON l.order_key = o.order_key
    LEFT JOIN customers c ON c.customer_key = o.customer_key
)

SELECT * FROM joined
