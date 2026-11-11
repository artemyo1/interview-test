CREATE SCHEMA IF NOT EXISTS core;

CREATE TABLE IF NOT EXISTS core.dim_cities (
    gc_id integer PRIMARY KEY,
    source_city_id bigint NOT NULL,
    city_name text NOT NULL,
    region text,
    latitude double precision,
    longitude double precision
);

CREATE TABLE IF NOT EXISTS core.dim_body_types (
    body_type_id bigint PRIMARY KEY,
    body_type_name text NOT NULL,
    std_id integer,
    body_group text NOT NULL CHECK (
        body_group IN (
            'Тент',
            'Рефрижератор',
            'Изотерм',
            'Борт',
            'Прочие'
        )
    )
);

CREATE OR REPLACE VIEW core.price_body_groups AS
WITH source_types AS (
    SELECT
        price_id,
        body_type_id
    FROM raw.price_body_types

    UNION

    SELECT
        p.id AS price_id,
        p.body_type AS body_type_id
    FROM raw.prices AS p
    WHERE p.body_type IS NOT NULL
      AND NOT EXISTS (
          SELECT 1 FROM raw.price_body_types AS b
          WHERE b.price_id = p.id
      )
)
SELECT DISTINCT
    p.id AS price_id,
    CASE
        WHEN s.body_type_id IS NULL THEN 'Не указан'
        ELSE coalesce(b.body_group, 'Прочие')
    END AS body_group
FROM raw.prices AS p
LEFT JOIN source_types AS s
    ON s.price_id = p.id
LEFT JOIN core.dim_body_types AS b
    ON b.body_type_id = s.body_type_id;

CREATE OR REPLACE VIEW core.price_quality AS
SELECT
    p.id AS price_id,
    CASE
        WHEN p.price <= 0
            THEN 'NON_POSITIVE_PRICE'
        WHEN p.currency_code = '643' AND p.price <= 1000
            THEN 'RUB_PRICE_TOO_LOW'
        WHEN p.distance <= 0
            THEN 'NON_POSITIVE_DISTANCE'
        WHEN p.total_weight < 0
            THEN 'NEGATIVE_WEIGHT'
        WHEN p.total_volume < 0
            THEN 'NEGATIVE_VOLUME'
        WHEN NOT EXISTS (
            SELECT 1 FROM raw.cities AS loading_city
            WHERE loading_city.gc_id = p.loading_city_gcid
        )
            THEN 'UNKNOWN_LOADING_CITY'
        WHEN NOT EXISTS (
            SELECT 1 FROM raw.cities AS unloading_city
            WHERE unloading_city.gc_id = p.unloading_city_gcid
        )
            THEN 'UNKNOWN_UNLOADING_CITY'
        ELSE NULL
    END AS dq_reason
FROM raw.prices AS p;

CREATE OR REPLACE VIEW core.price_normalization AS
WITH rated AS (
    SELECT
        p.id AS price_id,
        p.price AS source_price,
        p.currency_code,
        p.nearest_rate_date,
        q.dq_reason AS base_dq_reason,
        CASE
            WHEN p.currency_code = '643' THEN 1.0
            ELSE rate.value
        END AS exchange_rate
    FROM raw.prices AS p
    JOIN core.price_quality AS q
        ON q.price_id = p.id
    LEFT JOIN LATERAL (
        SELECT er.value
        FROM raw.exchange_rates AS er
        WHERE er.num_code = p.currency_code
          AND er.setting_date = p.nearest_rate_date
          AND p.currency_code IN ('840', '978')
        ORDER BY er.id
        LIMIT 1
    ) AS rate ON true
),
validated AS (
    SELECT
        rated.*,
        CASE
            WHEN base_dq_reason IS NOT NULL THEN base_dq_reason
            WHEN currency_code NOT IN ('643', '840', '978')
                THEN 'UNSUPPORTED_CURRENCY'
            WHEN exchange_rate IS NULL
                THEN 'MISSING_EXCHANGE_RATE'
            WHEN exchange_rate <= 0
                THEN 'INVALID_EXCHANGE_RATE'
            ELSE NULL
        END AS dq_reason
    FROM rated
)
SELECT
    price_id,
    source_price,
    currency_code,
    nearest_rate_date,
    exchange_rate,
    dq_reason,
    CASE
        WHEN dq_reason IS NULL THEN source_price * exchange_rate
        ELSE NULL
    END AS price_rub
FROM validated;

CREATE OR REPLACE VIEW core.price_categories AS
SELECT
    p.id AS price_id,
    p.total_weight,
    p.total_volume,
    coalesce(
        nullif(btrim(p.weight_category), ''),
        CASE
            WHEN round(p.total_weight::numeric, 1) >= 30 THEN 'G20T'
            WHEN round(p.total_weight::numeric, 1) >= 10 THEN 'FTL'
            WHEN round(p.total_weight::numeric, 1) >= 5 THEN '10T'
            WHEN round(p.total_weight::numeric, 1) >= 2.5 THEN '5T'
            WHEN round(p.total_weight::numeric, 1) >= 0.5 THEN 'L5T'
            ELSE 'OTH'
        END
    ) AS weight_category,
    coalesce(
        nullif(btrim(p.volume_category), ''),
        CASE
            WHEN p.total_volume > 0 AND p.total_volume <= 120
                THEN 'FTL'
            ELSE 'OTH'
        END
    ) AS volume_category
FROM raw.prices AS p;

CREATE OR REPLACE VIEW core.prepared_prices AS
SELECT
    p.id AS price_id,
    p.created_at,
    p.organization_id,
    p.loading_date,
    p.end_loading_date,
    p.loading_city_gcid,
    p.unloading_city_gcid,
    p.distance,
    p.total_weight,
    p.total_volume,
    p.body_type,
    p.price_type,
    p.is_fact,
    p.complex,
    p.outlier_mark,
    n.source_price,
    n.currency_code,
    n.nearest_rate_date,
    n.exchange_rate,
    n.price_rub,
    c.weight_category,
    c.volume_category
FROM raw.prices AS p
JOIN core.price_normalization AS n
    ON n.price_id = p.id
JOIN core.price_categories AS c
    ON c.price_id = p.id
WHERE n.dq_reason IS NULL;

CREATE TABLE IF NOT EXISTS core.fact_prices (
    price_id bigint PRIMARY KEY,
    created_at timestamp,
    organization_id integer,
    loading_date date NOT NULL,
    end_loading_date date,
    loading_city_gcid integer NOT NULL
        REFERENCES core.dim_cities (gc_id),
    unloading_city_gcid integer NOT NULL
        REFERENCES core.dim_cities (gc_id),
    distance double precision NOT NULL CHECK (distance > 0),
    total_weight double precision NOT NULL CHECK (total_weight >= 0),
    total_volume double precision NOT NULL CHECK (total_volume >= 0),
    body_type integer,
    price_type smallint NOT NULL,
    is_fact boolean NOT NULL,
    complex boolean NOT NULL,
    outlier_mark boolean NOT NULL,
    source_price double precision NOT NULL CHECK (source_price > 0),
    currency_code varchar(3) NOT NULL,
    nearest_rate_date date,
    exchange_rate double precision NOT NULL CHECK (exchange_rate > 0),
    price_rub double precision NOT NULL CHECK (price_rub > 0),
    weight_category varchar(4) NOT NULL,
    volume_category varchar(4) NOT NULL
);

CREATE INDEX IF NOT EXISTS fact_prices_loading_date_idx
    ON core.fact_prices (loading_date);

CREATE TABLE IF NOT EXISTS core.fact_price_body_groups (
    price_id bigint NOT NULL
        REFERENCES core.fact_prices (price_id) ON DELETE CASCADE,
    body_group text NOT NULL CHECK (
        body_group IN (
            'Тент',
            'Рефрижератор',
            'Изотерм',
            'Борт',
            'Прочие',
            'Не указан'
        )
    ),
    PRIMARY KEY (price_id, body_group)
);

CREATE TABLE IF NOT EXISTS core.fact_price_dates (
    price_id bigint NOT NULL
        REFERENCES core.fact_prices (price_id) ON DELETE CASCADE,
    price_date date NOT NULL,
    PRIMARY KEY (price_id, price_date)
);

CREATE INDEX IF NOT EXISTS fact_price_dates_price_date_idx
    ON core.fact_price_dates (price_date);