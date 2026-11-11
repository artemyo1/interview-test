CREATE SCHEMA IF NOT EXISTS mart;

CREATE TABLE IF NOT EXISTS mart.market_corridor (
    price_month date NOT NULL,
    loading_city_gcid integer NOT NULL,
    unloading_city_gcid integer NOT NULL,
    body_group text NOT NULL,
    weight_category varchar(4) NOT NULL,
    p25_trip double precision NOT NULL,
    median_trip double precision NOT NULL,
    p75_trip double precision NOT NULL,
    avg_trip double precision NOT NULL,
    p25_km double precision NOT NULL,
    median_km double precision NOT NULL,
    p75_km double precision NOT NULL,
    trips_count bigint NOT NULL CHECK (trips_count > 0),
    carriers_count bigint NOT NULL CHECK (
        carriers_count >= 0 AND carriers_count <= trips_count
    ),
    cv_rate double precision,
    mom_pct double precision,
    yoy_pct double precision,
    reliability_level text NOT NULL CHECK (
        reliability_level IN ('HIGH', 'MEDIUM', 'LOW_DATA')
    ),
    last_price_date date NOT NULL,
    as_of date NOT NULL,
    PRIMARY KEY (
        price_month,
        loading_city_gcid,
        unloading_city_gcid,
        body_group,
        weight_category
    )
);

CREATE INDEX IF NOT EXISTS market_corridor_route_idx
    ON mart.market_corridor (
        loading_city_gcid,
        unloading_city_gcid,
        body_group,
        weight_category,
        price_month
    );

CREATE OR REPLACE VIEW mart.route_card AS
SELECT
    m.*,
    loading_city.city_name AS loading_city_name,
    loading_city.region AS loading_region,
    unloading_city.city_name AS unloading_city_name,
    unloading_city.region AS unloading_region
FROM mart.market_corridor AS m
JOIN core.dim_cities AS loading_city
    ON loading_city.gc_id = m.loading_city_gcid
JOIN core.dim_cities AS unloading_city
    ON unloading_city.gc_id = m.unloading_city_gcid;
