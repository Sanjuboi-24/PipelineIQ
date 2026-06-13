-- stg_orders.sql
-- Cleans and casts the raw ORDERS table.
-- Adds derived columns used downstream in fct_orders.

WITH source AS (
    SELECT * FROM {{ source('raw', 'ORDERS') }}
),

cleaned AS (
    SELECT
        O_ORDERKEY                                          AS order_key,
        O_CUSTKEY                                           AS customer_key,
        O_ORDERSTATUS                                       AS order_status,
        O_TOTALPRICE                                        AS total_price_usd,
        CAST(O_ORDERDATE AS DATE)                           AS order_date,
        O_ORDERPRIORITY                                     AS order_priority,
        O_CLERK                                             AS clerk_id,
        O_SHIPPRIORITY                                      AS ship_priority,
        O_COMMENT                                           AS order_comment,

        -- Derived
        YEAR(CAST(O_ORDERDATE AS DATE))                     AS order_year,
        MONTH(CAST(O_ORDERDATE AS DATE))                    AS order_month,

        CASE
            WHEN O_ORDERSTATUS = 'F' THEN 'FULFILLED'
            WHEN O_ORDERSTATUS = 'O' THEN 'OPEN'
            WHEN O_ORDERSTATUS = 'P' THEN 'PARTIAL'
            ELSE 'UNKNOWN'
        END                                                 AS order_status_label,

        CURRENT_TIMESTAMP()                                 AS _loaded_at

    FROM source
    WHERE O_ORDERKEY IS NOT NULL
)

SELECT * FROM cleaned
