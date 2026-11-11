import psycopg


def get_connection(db_config):
    return psycopg.connect(**db_config, connect_timeout=10)
