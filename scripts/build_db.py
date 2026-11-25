"""Download NYC TLC trip data (Jan 2024) and build data/quarry.duckdb.

Source: NYC Taxi & Limousine Commission open data
https://www.nyc.gov/site/tlc/about/tlc-trip-record-data.page
"""
from __future__ import annotations

import sys
import urllib.request
from pathlib import Path

import duckdb

ROOT = Path(__file__).resolve().parent.parent
RAW = ROOT / "data" / "raw"
DB = ROOT / "data" / "quarry.duckdb"
BASE = "https://d37ci6vzurychx.cloudfront.net"
FILES = {
    "yellow_2024-01.parquet": f"{BASE}/trip-data/yellow_tripdata_2024-01.parquet",
    "green_2024-01.parquet": f"{BASE}/trip-data/green_tripdata_2024-01.parquet",
    "taxi_zones.csv": f"{BASE}/misc/taxi_zone_lookup.csv",
}


def fetch() -> None:
    RAW.mkdir(parents=True, exist_ok=True)
    for name, url in FILES.items():
        out = RAW / name
        if out.exists():
            continue
        print(f"downloading {url}")
        req = urllib.request.Request(url, headers={"User-Agent": "quarry-build/1.0"})
        with urllib.request.urlopen(req, timeout=120) as r, open(out, "wb") as f:
            while chunk := r.read(1 << 20):
                f.write(chunk)


SCHEMA = """
CREATE TABLE zones (
    zone_id      INTEGER PRIMARY KEY,
    borough      VARCHAR,
    zone         VARCHAR,
    service_zone VARCHAR
);
CREATE TABLE vendors (vendor_id INTEGER PRIMARY KEY, name VARCHAR);
CREATE TABLE payment_types (payment_type INTEGER PRIMARY KEY, description VARCHAR);
CREATE TABLE rate_codes (rate_code_id INTEGER PRIMARY KEY, description VARCHAR);
INSERT INTO vendors VALUES (1, 'Creative Mobile Technologies'), (2, 'Curb Mobility'), (6, 'Myle Technologies');
INSERT INTO payment_types VALUES (0, 'Flex fare'), (1, 'Credit card'), (2, 'Cash'), (3, 'No charge'),
    (4, 'Dispute'), (5, 'Unknown'), (6, 'Voided trip');
INSERT INTO rate_codes VALUES (1, 'Standard rate'), (2, 'JFK'), (3, 'Newark'), (4, 'Nassau or Westchester'),
    (5, 'Negotiated fare'), (6, 'Group ride'), (99, 'Unknown');
"""

TRIPS = """
CREATE TABLE trips AS
SELECT
    row_number() OVER () AS trip_id,
    taxi_type, vendor_id, pickup_at, dropoff_at, passenger_count, trip_distance,
    rate_code_id, pickup_zone_id, dropoff_zone_id, payment_type,
    fare_amount, tip_amount, tolls_amount, total_amount, congestion_surcharge, airport_fee
FROM (
    SELECT 'yellow' AS taxi_type, VendorID::INT AS vendor_id,
           tpep_pickup_datetime AS pickup_at, tpep_dropoff_datetime AS dropoff_at,
           passenger_count::INT AS passenger_count, trip_distance, RatecodeID::INT AS rate_code_id,
           PULocationID::INT AS pickup_zone_id, DOLocationID::INT AS dropoff_zone_id,
           payment_type::INT AS payment_type, fare_amount, tip_amount, tolls_amount, total_amount,
           congestion_surcharge, Airport_fee AS airport_fee
    FROM read_parquet('{yellow}')
    UNION ALL
    SELECT 'green', VendorID::INT, lpep_pickup_datetime, lpep_dropoff_datetime,
           passenger_count::INT, trip_distance, RatecodeID::INT, PULocationID::INT, DOLocationID::INT,
           payment_type::INT, fare_amount, tip_amount, tolls_amount, total_amount,
           congestion_surcharge, NULL
    FROM read_parquet('{green}')
)
WHERE pickup_at >= TIMESTAMP '2024-01-01' AND pickup_at < TIMESTAMP '2024-02-01';
"""

COMMENTS = """
COMMENT ON TABLE trips IS 'One row per NYC yellow or green taxi trip in January 2024';
COMMENT ON COLUMN trips.taxi_type IS 'yellow (Manhattan-focused medallion cab) or green (outer-borough boro taxi)';
COMMENT ON COLUMN trips.pickup_at IS 'Pickup timestamp, local NYC time';
COMMENT ON COLUMN trips.dropoff_at IS 'Dropoff timestamp, local NYC time';
COMMENT ON COLUMN trips.trip_distance IS 'Trip distance in miles';
COMMENT ON COLUMN trips.pickup_zone_id IS 'Pickup zone, joins zones.zone_id';
COMMENT ON COLUMN trips.dropoff_zone_id IS 'Dropoff zone, joins zones.zone_id';
COMMENT ON COLUMN trips.payment_type IS 'Joins payment_types.payment_type';
COMMENT ON COLUMN trips.rate_code_id IS 'Joins rate_codes.rate_code_id';
COMMENT ON COLUMN trips.vendor_id IS 'Joins vendors.vendor_id';
COMMENT ON COLUMN trips.fare_amount IS 'Metered fare in USD';
COMMENT ON COLUMN trips.tip_amount IS 'Tip in USD (card tips only; cash tips not recorded)';
COMMENT ON COLUMN trips.total_amount IS 'Total charged to the rider in USD';
COMMENT ON TABLE zones IS 'TLC taxi zones: 265 neighbourhood-level zones across the five boroughs plus EWR';
COMMENT ON COLUMN zones.borough IS 'Manhattan, Brooklyn, Queens, Bronx, Staten Island, EWR or Unknown';
COMMENT ON COLUMN zones.zone IS 'Zone name, e.g. JFK Airport, Midtown Center';
COMMENT ON TABLE vendors IS 'Technology provider that recorded the trip';
COMMENT ON TABLE payment_types IS 'How the rider paid';
COMMENT ON TABLE rate_codes IS 'Final rate code in effect at the end of the trip';
"""


def build() -> None:
    if DB.exists():
        DB.unlink()
    con = duckdb.connect(str(DB))
    con.execute(SCHEMA)
    con.execute(
        "INSERT INTO zones SELECT LocationID, Borough, Zone, service_zone FROM read_csv_auto(?)",
        [str(RAW / "taxi_zones.csv")],
    )
    con.execute(TRIPS.format(
        yellow=(RAW / "yellow_2024-01.parquet").as_posix(),
        green=(RAW / "green_2024-01.parquet").as_posix(),
    ))
    con.execute(COMMENTS)
    for t in ("trips", "zones", "vendors", "payment_types", "rate_codes"):
        n = con.execute(f"SELECT count(*) FROM {t}").fetchone()[0]
        print(f"{t:14} {n:>10,} rows")
    con.close()


if __name__ == "__main__":
    fetch()
    build()
    print(f"built {DB}")
    sys.exit(0)
