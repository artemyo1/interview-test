\set ON_ERROR_STOP on

\if :{?n_prices}
\else
    \set n_prices 300000
\endif

BEGIN;

SELECT pg_advisory_xact_lock(20261026);

SELECT
    :'n_prices'::integer AS n_prices,
    :'n_prices'::integer > 0 AS valid_n_prices
\gset

\if :valid_n_prices
\else
    DO $$
    BEGIN
        RAISE EXCEPTION 'n_prices must be positive';
    END
    $$;
\endif

CREATE SCHEMA IF NOT EXISTS raw;

SET LOCAL search_path TO raw, public;

CREATE TABLE IF NOT EXISTS raw.bootstrap_state (
    id integer PRIMARY KEY CHECK (id = 1),
    n_prices integer NOT NULL CHECK (n_prices > 0),
    initialized_at timestamptz NOT NULL DEFAULT now()
);

SELECT EXISTS (
    SELECT 1
    FROM raw.bootstrap_state
    WHERE id = 1
) AS initialized
\gset

\if :initialized

    SELECT n_prices = :'n_prices'::integer AS same_size
    FROM raw.bootstrap_state
    WHERE id = 1
    \gset

    \if :same_size
        \echo 'Source already initialized; skipping generation'
    \else
        DO $$
        BEGIN
            RAISE EXCEPTION
                'Source already initialized with another n_prices';
        END
        $$;
    \endif

\else

    \ir schema.sql
    \ir synthetic.sql

    INSERT INTO raw.bootstrap_state (id, n_prices)
    VALUES (1, :'n_prices'::integer);

\endif

COMMIT;