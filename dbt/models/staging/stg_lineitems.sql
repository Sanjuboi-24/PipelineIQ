-- stg_lineitems.sql
-- Expands and casts raw LINEITEM records.

WITH source AS (
    SELECT * FROM {{ source('raw', 'LINEITEM') }}
),

cleaned AS (
    SELECT
        L_ORDERKEY                          AS order_key,
        L_PARTKEY                           AS part_key,
        L_SUPPKEY                           AS supplier_key,
        L_LINENUMBER                        AS line_number,
        L_QUANTITY                          AS quantity,
        L_EXTENDEDPRICE                     AS extended_price,
        L_DISCOUNT                          AS discount_pct,
        L_TAX                               AS tax_pct,
        L_RETURNFLAG                        AS return_flag,
        L_LINESTATUS                        AS line_status,
        CAST(L_SHIPDATE AS DATE)            AS ship_date,
        CAST(L_COMMITDATE AS DATE)          AS commit_date,
        CAST(L_RECEIPTDATE AS DATE)         AS receipt_date,
        L_SHIPINSTRUCT                      AS ship_instructions,
        L_SHIPMODE                          AS ship_mode,

        -- Derived financial columns
        ROUND(L_EXTENDEDPRICE * (1 - L_DISCOUNT), 2)               AS net_price,
        ROUND(L_EXTENDEDPRICE * (1 - L_DISCOUNT) * (1 + L_TAX), 2) AS gross_price,

        CURRENT_TIMESTAMP() AS _loaded_at

    FROM source
    WHERE L_ORDERKEY IS NOT NULL
)

SELECT * FROM cleaned
