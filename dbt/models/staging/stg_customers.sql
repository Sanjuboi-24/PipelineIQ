-- stg_customers.sql
-- Deduplicates and validates the raw CUSTOMER table.

WITH source AS (
    SELECT * FROM {{ source('raw', 'CUSTOMER') }}
),

deduped AS (
    SELECT *,
        ROW_NUMBER() OVER (PARTITION BY C_CUSTKEY ORDER BY C_CUSTKEY) AS rn
    FROM source
),

cleaned AS (
    SELECT
        C_CUSTKEY       AS customer_key,
        C_NAME          AS customer_name,
        C_ADDRESS       AS customer_address,
        C_NATIONKEY     AS nation_key,
        C_PHONE         AS phone_number,
        C_ACCTBAL       AS account_balance,
        C_MKTSEGMENT    AS market_segment,
        C_COMMENT       AS customer_comment,

        CURRENT_TIMESTAMP() AS _loaded_at

    FROM deduped
    WHERE rn = 1
      AND C_CUSTKEY IS NOT NULL
)

SELECT * FROM cleaned
