WITH months AS (
    SELECT
        price_id,
        date_trunc('month', price_date)::date AS price_month,
        max(price_date) AS last_price_date
    FROM core.fact_price_dates
    WHERE price_date <= %(as_of)s
    GROUP BY price_id, date_trunc('month', price_date)::date
),
observations AS (
    SELECT
        p.price_id,
        m.price_month,
        p.loading_city_gcid,
        p.unloading_city_gcid,
        b.body_group,
        p.weight_category,
        p.price_rub,
        p.price_rub / nullif(p.distance, 0) AS price_km,
        p.price_type,
        p.organization_id,
        m.last_price_date
    FROM core.fact_prices AS p
    JOIN months AS m ON m.price_id = p.price_id
    JOIN core.fact_price_body_groups AS b ON b.price_id = p.price_id
    WHERE p.is_fact
      AND p.price_type IN (1, 2, 3, 4, 5, 9)
      AND NOT p.complex
      AND NOT p.outlier_mark
      AND p.source_price > 0
      AND p.distance > 0
      AND p.price_rub > 0
),
monthly_stats AS (
    SELECT
        price_month,
        loading_city_gcid,
        unloading_city_gcid,
        body_group,
        weight_category,
        percentile_cont(ARRAY[0.25, 0.5, 0.75])
            WITHIN GROUP (ORDER BY price_rub) AS trip_percentiles,
        avg(price_rub) AS avg_trip,
        percentile_cont(ARRAY[0.25, 0.5, 0.75])
            WITHIN GROUP (ORDER BY price_km) AS km_percentiles,
        count(DISTINCT price_id) AS trips_count,
        count(DISTINCT organization_id)
            FILTER (WHERE price_type IN (3, 9)) AS carriers_count,
        stddev_samp(price_rub) / nullif(avg(price_rub), 0) AS cv_rate,
        max(last_price_date) AS last_price_date
    FROM observations
    GROUP BY
        price_month,
        loading_city_gcid,
        unloading_city_gcid,
        body_group,
        weight_category
)
INSERT INTO mart.market_corridor (
    price_month,
    loading_city_gcid,
    unloading_city_gcid,
    body_group,
    weight_category,
    p25_trip,
    median_trip,
    p75_trip,
    avg_trip,
    p25_km,
    median_km,
    p75_km,
    trips_count,
    carriers_count,
    cv_rate,
    mom_pct,
    yoy_pct,
    reliability_level,
    last_price_date,
    as_of
)
SELECT
    m.price_month,
    m.loading_city_gcid,
    m.unloading_city_gcid,
    m.body_group,
    m.weight_category,
    m.trip_percentiles[1],
    m.trip_percentiles[2],
    m.trip_percentiles[3],
    m.avg_trip,
    m.km_percentiles[1],
    m.km_percentiles[2],
    m.km_percentiles[3],
    m.trips_count,
    m.carriers_count,
    m.cv_rate,
    (m.trip_percentiles[2] - previous_month.trip_percentiles[2])
        / nullif(previous_month.trip_percentiles[2], 0) * 100 AS mom_pct,
    (m.trip_percentiles[2] - previous_year.trip_percentiles[2])
        / nullif(previous_year.trip_percentiles[2], 0) * 100 AS yoy_pct,
    CASE
        WHEN m.trips_count >= 30
         AND m.carriers_count >= 5
         AND %(as_of)s::date - m.last_price_date <= 30 THEN 'HIGH'
        WHEN m.trips_count >= 10 THEN 'MEDIUM'
        ELSE 'LOW_DATA'
    END,
    m.last_price_date,
    %(as_of)s
FROM monthly_stats AS m
LEFT JOIN monthly_stats AS previous_month
    ON previous_month.price_month = (m.price_month - INTERVAL '1 month')::date
   AND previous_month.loading_city_gcid = m.loading_city_gcid
   AND previous_month.unloading_city_gcid = m.unloading_city_gcid
   AND previous_month.body_group = m.body_group
   AND previous_month.weight_category = m.weight_category
LEFT JOIN monthly_stats AS previous_year
    ON previous_year.price_month = (m.price_month - INTERVAL '1 year')::date
   AND previous_year.loading_city_gcid = m.loading_city_gcid
   AND previous_year.unloading_city_gcid = m.unloading_city_gcid
   AND previous_year.body_group = m.body_group
   AND previous_year.weight_category = m.weight_category;
