#!/usr/bin/env python3
"""
db_import.py

Loads a GeoJSON produced by extract_map_features.py into a database.
Three backends are supported; pick the one that matches where you want the
data to live.

--- PostGIS (PostgreSQL with the PostGIS extension) ---
    python db_import.py extracted_map_features.geojson \
        --backend postgis \
        --conn "postgresql://user:password@localhost:5432/mydatabase" \
        --table agricultural_land \
        --if-exists replace

--- GeoPackage / SQLite (a single local file, no server needed) ---
    python db_import.py extracted_map_features.geojson \
        --backend gpkg \
        --out data/output/agricultural_land.gpkg \
        --table agricultural_land

--- MongoDB (stores each feature as a native GeoJSON document) ---
    python db_import.py extracted_map_features.geojson \
        --backend mongodb \
        --conn "mongodb://localhost:27017" \
        --db mapdata --collection agricultural_land

Requires the extra dependencies for whichever backend you use -- see
requirements-db.txt (PostGIS/GPKG) and requirements-mongo.txt (MongoDB).
These are optional and NOT part of the core requirements.txt, since most
projects only need one backend.
"""

import argparse
import json
import sys


def import_postgis(geojson_path, conn_str, table, if_exists):
    import geopandas as gpd
    from sqlalchemy import create_engine

    gdf = gpd.read_file(geojson_path)
    engine = create_engine(conn_str)
    gdf.to_postgis(table, engine, if_exists=if_exists, index=False)
    print(f"Wrote {len(gdf)} features to PostGIS table '{table}' "
          f"(if_exists='{if_exists}').")
    print("Tip: after this, run in psql:  CREATE INDEX ON "
          f'"{table}" USING GIST (geometry);   -- if not created automatically')


def import_gpkg(geojson_path, out_path, table):
    import geopandas as gpd

    gdf = gpd.read_file(geojson_path)
    gdf.to_file(out_path, layer=table, driver="GPKG")
    print(f"Wrote {len(gdf)} features to {out_path} (layer '{table}').")
    print("Open directly in QGIS: Layer > Add Layer > Add Vector Layer > select this .gpkg")


def import_mongodb(geojson_path, conn_str, db_name, collection_name):
    from pymongo import MongoClient

    with open(geojson_path) as f:
        data = json.load(f)

    client = MongoClient(conn_str)
    coll = client[db_name][collection_name]
    docs = data["features"]
    if docs:
        result = coll.insert_many(docs)
        print(f"Inserted {len(result.inserted_ids)} documents into "
              f"{db_name}.{collection_name}.")
        print("Tip: for geo queries, create a 2dsphere index:\n"
              f'  db.{collection_name}.createIndex({{"geometry": "2dsphere"}})')
    else:
        print("No features found in the GeoJSON -- nothing inserted.")


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("geojson_path")
    ap.add_argument("--backend", required=True, choices=["postgis", "gpkg", "mongodb"])
    ap.add_argument("--conn", help="Connection string (postgis: SQLAlchemy URL, mongodb: Mongo URI)")
    ap.add_argument("--table", default="map_features", help="Table/layer name (postgis, gpkg)")
    ap.add_argument("--if-exists", default="replace", choices=["fail", "replace", "append"],
                     help="postgis only: behavior if the table already exists (default replace)")
    ap.add_argument("--out", help="gpkg only: output .gpkg file path")
    ap.add_argument("--db", help="mongodb only: database name")
    ap.add_argument("--collection", help="mongodb only: collection name")
    args = ap.parse_args()

    if args.backend == "postgis":
        if not args.conn:
            sys.exit("--conn is required for --backend postgis")
        import_postgis(args.geojson_path, args.conn, args.table, args.if_exists)

    elif args.backend == "gpkg":
        if not args.out:
            sys.exit("--out is required for --backend gpkg")
        import_gpkg(args.geojson_path, args.out, args.table)

    elif args.backend == "mongodb":
        if not (args.conn and args.db and args.collection):
            sys.exit("--conn, --db and --collection are all required for --backend mongodb")
        import_mongodb(args.geojson_path, args.conn, args.db, args.collection)


if __name__ == "__main__":
    main()
