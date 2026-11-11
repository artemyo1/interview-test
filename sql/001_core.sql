CREATE SCHEMA IF NOT EXISTS core;

CREATE TABLE IF NOT EXISTS core.dim_cities (
    gc_id integer PRIMARY KEY,
    source_city_id bigint NOT NULL,
    city_name text NOT NULL,
    region text,
    latitude double precision,
    longitude double precision
);