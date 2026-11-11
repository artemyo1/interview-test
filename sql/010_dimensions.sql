INSERT INTO core.dim_cities (
    gc_id,
    source_city_id,
    city_name,
    region,
    latitude,
    longitude
)
SELECT DISTINCT ON (gc_id)
    gc_id,
    id AS source_city_id,
    btrim(split_part(full_name_local, ',', 1)) AS city_name,
    CASE
        WHEN strpos(full_name_local, ',') > 0
        THEN nullif(
            btrim(
                substring(
                    full_name_local
                    FROM strpos(full_name_local, ',') + 1
                )
            ),
            ''
        )
        ELSE NULL
    END AS region,
    latitude,
    longitude
FROM raw.cities
ORDER BY gc_id, id
ON CONFLICT (gc_id) DO UPDATE
SET
    source_city_id = EXCLUDED.source_city_id,
    city_name = EXCLUDED.city_name,
    region = EXCLUDED.region,
    latitude = EXCLUDED.latitude,
    longitude = EXCLUDED.longitude;