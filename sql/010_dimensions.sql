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

INSERT INTO core.dim_body_types (
    body_type_id,
    body_type_name,
    std_id,
    body_group
)
SELECT
    id,
    name,
    std_id,
    CASE std_id
        WHEN 56 THEN 'Тент'
        WHEN 4 THEN 'Рефрижератор'
        WHEN 2 THEN 'Изотерм'
        WHEN 15 THEN 'Борт'
        ELSE 'Прочие'
    END
FROM raw.body_types
ON CONFLICT (body_type_id) DO UPDATE
SET
    body_type_name = EXCLUDED.body_type_name,
    std_id = EXCLUDED.std_id,
    body_group = EXCLUDED.body_group;