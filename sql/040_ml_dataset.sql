WITH body_groups AS (
    SELECT
        price_id,
        string_agg(body_group, '|' ORDER BY body_group) AS body_group
    FROM core.fact_price_body_groups
    GROUP BY price_id
)
SELECT
    p.price_id,
    p.loading_date,
    p.loading_city_gcid,
    p.unloading_city_gcid,
    loading_city.region AS loading_region,
    unloading_city.region AS unloading_region,
    loading_city.latitude AS loading_latitude,
    loading_city.longitude AS loading_longitude,
    unloading_city.latitude AS unloading_latitude,
    unloading_city.longitude AS unloading_longitude,
    p.distance,
    p.total_weight,
    p.total_volume,
    b.body_group,
    p.weight_category,
    p.volume_category,
    CASE
        WHEN p.loading_date <= %(as_of)s THEN p.price_rub
        ELSE NULL
    END AS actual_price_rub
FROM core.fact_prices AS p
JOIN core.dim_cities AS loading_city
    ON loading_city.gc_id = p.loading_city_gcid
JOIN core.dim_cities AS unloading_city
    ON unloading_city.gc_id = p.unloading_city_gcid
JOIN body_groups AS b ON b.price_id = p.price_id
WHERE p.loading_date >= %(train_start)s
  AND (
      p.loading_date > %(as_of)s
      OR (
          p.is_fact
          AND p.price_type IN (1, 2, 3, 4, 5, 9)
          AND NOT p.complex
          AND NOT p.outlier_mark
      )
  )
ORDER BY p.loading_date, p.price_id;
