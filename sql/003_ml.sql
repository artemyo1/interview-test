CREATE SCHEMA IF NOT EXISTS mart;

CREATE TABLE IF NOT EXISTS mart.ml_price_predictions (
    price_id bigint PRIMARY KEY
        REFERENCES core.fact_prices (price_id) ON DELETE CASCADE,
    loading_date date NOT NULL,
    loading_city_gcid integer NOT NULL,
    unloading_city_gcid integer NOT NULL,
    body_group text NOT NULL,
    prediction_set text NOT NULL CHECK (prediction_set IN ('test', 'future')),
    actual_price_rub double precision CHECK (actual_price_rub > 0),
    predicted_price_rub double precision NOT NULL CHECK (predicted_price_rub > 0),
    abs_error_rub double precision CHECK (abs_error_rub >= 0),
    ape_pct double precision CHECK (ape_pct >= 0),
    model_name text NOT NULL,
    as_of date NOT NULL,
    scored_at timestamptz NOT NULL,
    CHECK (
        (
            prediction_set = 'future'
            AND loading_date > as_of
            AND actual_price_rub IS NULL
            AND abs_error_rub IS NULL
            AND ape_pct IS NULL
        )
        OR (
            prediction_set = 'test'
            AND loading_date BETWEEN as_of - 21 AND as_of
            AND actual_price_rub IS NOT NULL
            AND abs_error_rub IS NOT NULL
            AND ape_pct IS NOT NULL
        )
    )
);

CREATE INDEX IF NOT EXISTS ml_price_predictions_route_date_idx
    ON mart.ml_price_predictions (
        loading_city_gcid, unloading_city_gcid, loading_date
    );
