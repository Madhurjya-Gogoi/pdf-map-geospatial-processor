# User Guide: council PDF map -> GeoJSON

This is the whole process, start to finish, for turning one council overlay
PDF into a usable GeoJSON. Repeat it once per overlay (agricultural, flood
zone, bushfire hazard, etc.).

## 1. Download the PDF and name it consistently

Get the overlay map PDF from the council's website. Rename it to:

```
<councilname>_<overlayname>_overlays.pdf
```

Example: `banana_agricultural_overlays.pdf`, `banana_floodzone_overlays.pdf`.

This naming isn't strictly required by the script, but keeping it consistent
makes every later step (and every future overlay you add) predictable.

## 2. Georeference it in QGIS

1. Open QGIS -> `Layer` -> `Georeferencer`.
2. Open the PDF in the Georeferencer.
3. Add your ground control points (GCPs) and match each to its real-world
   coordinate, same as you've already been doing.
4. Once enough GCPs are placed and it looks precise, run the transformation
   and export as a georeferenced GeoTIFF, using the **same base name** as the
   PDF, e.g. `banana_agricultural_overlays.tif`.
5. Verify in QGIS: add the exported GeoTIFF as a layer and confirm it lines
   up correctly against your reference map (OSM, satellite, etc.). Don't
   move on until this looks right -- everything downstream trusts this file.

## 3. Copy both files into the codebase

```
banana-map-extraction/
└── data/
    └── input/
        ├── banana_agricultural_overlays.pdf
        └── banana_agricultural_overlays.tif
```

If QGIS also gave you a `.tfw` world-file sidecar, copy that in too, next to
the `.tif`, with the same base name.

## 4. Run the extraction script

```bash
cd banana-map-extraction
python scripts/extract_map_features.py \
    data/input/banana_agricultural_overlays.tif \
    --source-pdf data/input/banana_agricultural_overlays.pdf \
    --council "Banana Shire" \
    --layer agricultural \
    --debug
```

Change `--layer` to match the overlay (`flood_zone`, `bushfire_hazard`,
etc.) each time. If that layer's colors haven't been sampled yet, see
`docs/ADDING_A_NEW_MAP.md` first.

This writes, into `data/output/`:
- `banana_shire_agricultural.geojson` -- the coordinates (name is dynamic:
  always `<council>_<layer>.geojson`, so it never collides across overlays)
- `extraction_report.json` -- CRS, bounds, feature counts
- `extraction_preview.png` -- the extracted outlines drawn on the source map

## 5. Verify manually

Open `extraction_preview.png`. Confirm the traced outlines sit exactly on
top of the real colored areas from the PDF. If something's off, it's the
GCP placement from Step 2 -- go back and add more/better-distributed points,
don't try to fix it by tweaking the script.

## 6. Store it (when you're ready for a database)

No database is required to use the output -- `data/output/*.geojson` is
already a complete, usable file store. When you do want one:

```bash
# GeoPackage (simplest -- single local file, opens directly in QGIS)
python scripts/db_import.py data/output/banana_shire_agricultural.geojson \
    --backend gpkg --out data/output/banana_shire_agricultural.gpkg \
    --table agricultural_land

# PostGIS
python scripts/db_import.py data/output/banana_shire_agricultural.geojson \
    --backend postgis --conn "postgresql://user:pass@localhost:5432/mydb" \
    --table agricultural_land --if-exists replace

# MongoDB
python scripts/db_import.py data/output/banana_shire_agricultural.geojson \
    --backend mongodb --conn "mongodb://localhost:27017" \
    --db mapdata --collection agricultural_land
```

See `config/db_config.example.txt` for where to fill in your real connection
details before running any of the above.