import pytest

from quarry.guard import GuardError, validate

TABLES = {"trips", "zones", "payment_types", "rate_codes", "vendors"}


@pytest.mark.parametrize("sql", [
    "SELECT count(*) FROM trips",
    "select 1;",
    "WITH a AS (SELECT * FROM trips) SELECT count(*) FROM a",
    "SELECT taxi_type FROM trips UNION ALL SELECT borough FROM zones",
    "SELECT z.zone, rank() OVER (ORDER BY count(*) DESC) FROM trips t JOIN zones z ON t.pickup_zone_id = z.zone_id GROUP BY 1",
    "SELECT * FROM (SELECT trip_id FROM trips) sub",
    "SELECT * FROM range(5)",
])
def test_allows_reads(sql):
    assert validate(sql, TABLES)


@pytest.mark.parametrize("sql, why", [
    ("DROP TABLE trips", "SELECT"),
    ("DELETE FROM trips", "SELECT"),
    ("INSERT INTO zones VALUES (1, 'a', 'b', 'c')", "SELECT"),
    ("UPDATE trips SET fare_amount = 0", "SELECT"),
    ("CREATE TABLE x AS SELECT 1", "SELECT"),
    ("ALTER TABLE trips ADD COLUMN x INT", "SELECT"),
    ("COPY trips TO 'out.csv'", "SELECT"),
    ("ATTACH 'other.db'", "SELECT"),
    ("PRAGMA database_list", "SELECT"),
    ("SET threads = 1", "SELECT"),
    ("SELECT 1; DROP TABLE trips", "one statement"),
    ("SELECT * FROM read_csv('secrets.csv')", "read_csv"),
    ("SELECT * FROM read_parquet('x.parquet')", "read_parquet"),
    ("SELECT * FROM 'data/raw/taxi_zones.csv'", "unknown table"),
    ("SELECT * FROM glob('*')", "glob"),
    ("SELECT current_setting('threads')", "current_setting"),
    ("SELECT getenv('HOME')", "getenv"),
    ("SELECT * FROM duckdb_settings()", "duckdb_settings"),
    ("SELECT * FROM secret_table", "unknown table"),
    ("SELECT * FROM information_schema.tables", "schema"),
    ("WITH x AS (DELETE FROM trips RETURNING *) SELECT * FROM x", ""),
    ("", "empty"),
    ("SELEC * FRM trips", ""),
])
def test_blocks(sql, why):
    with pytest.raises(GuardError) as e:
        validate(sql, TABLES)
    assert why.lower() in str(e.value).lower()
