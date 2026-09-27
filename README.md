# Banana Shire Map Feature Extraction

Turns a planning-scheme overlay PDF map (like the *Banana Shire Agricultural
Land Overlay Map*) into real geographic coordinates: a GeoJSON of every
colored area on the map (agricultural land, stock routes, etc.), ready to
load into QGIS, a database, or a web map.

## How the pipeline fits together

```
  1. PDF map                       (what the council publishes)
        │  pdf_to_raster.py
        ▼
  2. Plain raster image            (just pixels, no coordinates yet)
        │  QGIS Georeferencer      (manual, one-time step -- see docs/GEOREFERENCING_IN_QGIS.md)
        ▼
  3. Georeferenced GeoTIFF         (now has a real CRS + affine transform)
        │  extract_map_features.py
        ▼
  4. extracted_map_features.geojson + extraction_report.json + extraction_preview.png
        │  db_import.py
        ▼
  5. PostGIS / GeoPackage / MongoDB / wherever you need it
```

Only step 2 (georeferencing) needs a human in QGIS. Steps 1, 3 and 5 are
scripts you run from the command line, and step 3 is the one you'll re-run
for every new map once it's set up.

---

## 1. One-time setup

### 1.1 Prerequisites

- **Python 3.10+** (`python3 --version`)
- **GDAL system libraries.** `rasterio` and `geopandas` both depend on GDAL.
  On most systems the pip wheels bundle GDAL for you, but if installation
  fails with a GDAL-related error, install it at the OS level first:

  ```bash
  # macOS (Homebrew)
  brew install gdal

  # Ubuntu/Debian
  sudo apt-get update && sudo apt-get install -y gdal-bin libgdal-dev

  # Windows
  # Easiest path: install via conda (see 1.2 "Windows alternative" below),
  # which handles GDAL for you. Avoid plain pip+venv on Windows unless
  # you're comfortable troubleshooting GDAL wheel issues.
  ```

- **QGIS** (free, https://qgis.org/download/) -- used only for the manual
  georeferencing step, not required to run the Python scripts themselves.

### 1.2 Create and activate a virtual environment

```bash
# macOS / Linux
cd banana-map-extraction
python3 -m venv .venv
source .venv/bin/activate

# Windows (PowerShell)
cd banana-map-extraction
python -m venv .venv
.venv\Scripts\Activate.ps1

# Windows (cmd.exe)
cd banana-map-extraction
python -m venv .venv
.venv\Scripts\activate.bat
```

You'll know it worked because your shell prompt gets a `(.venv)` prefix.
Every `pip install` and `python` command below assumes this is active --
if you close your terminal, just re-run the `activate` line to get back in.

**Windows alternative (recommended if plain pip gives GDAL errors):**
```powershell
conda create -n banana-map python=3.11
conda activate banana-map
conda install -c conda-forge rasterio geopandas shapely pyproj opencv numpy pymupdf
```

### 1.3 Install Python dependencies

```bash
pip install --upgrade pip
pip install -r requirements.txt

# Only if you'll load results into PostGIS:
pip install -r requirements-db.txt

# Only if you'll load results into MongoDB:
pip install -r requirements-mongo.txt
```

### 1.4 Verify the install

```bash
python scripts/extract_map_features.py --help
```
If that prints the usage text with no import errors, you're set up correctly.

---

## 2. Processing a map (the part you repeat for every new PDF)

### Step A -- Rasterize the PDF

```bash
python scripts/pdf_to_raster.py data/input/my_new_map.pdf data/input/my_new_map_raw.png --dpi 400
```
This just flattens the PDF into a plain image -- no coordinates yet.

### Step B -- Georeference it in QGIS

Full walkthrough: **[docs/GEOREFERENCING_IN_QGIS.md](docs/GEOREFERENCING_IN_QGIS.md)**

Short version: open the image in QGIS's Georeferencer, click known
ground-control points (town markers, road intersections, a coordinate
graticule -- whatever the map actually has), assign each one its real-world
coordinate, run the transformation, and export the result as a GeoTIFF
(e.g. `data/input/my_new_map_georeferenced.tif`).

**This is the only manual step in the whole pipeline**, and its accuracy is
the ceiling for everything downstream -- garbage GCPs in, garbage
coordinates out. Take your time on it.

### Step C -- Find the legend colors for the new map

Every map's legend uses its own colors. Crop just the legend out of your
rasterized image (any image viewer/editor works) and sample it:

```bash
python scripts/sample_legend_color.py data/input/legend_crop.png 127 440
```

Full walkthrough, including how to build the `categories.json` this feeds
into: **[docs/ADDING_A_NEW_MAP.md](docs/ADDING_A_NEW_MAP.md)**

If you're re-running against the *same* Banana Shire map style, skip this --
`config/categories.banana_shire.json` is already built for you and is also
the script's built-in default.

### Step D -- Extract the GeoJSON

You almost never need to type an output filename or a `--categories` path
by hand -- give it `--council` and `--layer` and it names/finds things for
you (see **"Dynamic naming"** below). Recommended form:

```bash
python scripts/extract_map_features.py \
    data/input/my_new_map_georeferenced.tif \
    --source-pdf data/input/my_new_map.pdf \
    --council "Banana Shire" \
    --layer agricultural \
    --min-area 8000 \
    --simplify 30 \
    --color-tolerance 28 \
    --debug
```

This automatically:
- looks for `config/categories.agricultural.json` (from `--layer`) and uses
  it if present -- no need to pass `--categories` at all once that file exists
- writes the result to `data/output/banana_shire_agricultural.geojson` --
  no need to type an output filename
- tags every feature (and the FeatureCollection itself) with
  `"council": "Banana Shire"` and `"layer": "agricultural"`

The old fully-explicit form still works if you'd rather control every path
yourself:

```bash
python scripts/extract_map_features.py \
    data/input/my_new_map_georeferenced.tif \
    data/output/my_new_map.geojson \
    --categories config/categories.my_new_map.json \
    --min-area 8000 --simplify 30 --color-tolerance 28 --debug
```

Outputs land next to the geojson (in `--outdir` if given, else
`data/output/`):
- `<council>_<layer>.geojson` (or your explicit filename) -- the actual feature data
- `extraction_report.json` -- CRS, bounds, feature counts, which categories file was used, known limitations
- `extraction_preview.png` -- extracted polygons overlaid on the source map, for a visual sanity check

**Always open `extraction_preview.png` before trusting the output.** If
the traced outlines don't sit on top of the real colored areas, the
georeferencing in Step B needs redoing, not the extraction script.

All CLI flags:

| Flag | Default | Meaning |
|---|---|---|
| `output_geojson` (positional) | *auto-named* | Optional. Omit it to get `data/output/<council>_<layer>.geojson` automatically. |
| `--council` | `banana_shire` | Council/shire name. Used for output naming and tags every feature with `"council"`. |
| `--layer` | *auto-derived* | Layer name, e.g. `agricultural`, `flood_zone`. Used to find `config/categories.<layer>.json`, name the output file, and tag every feature with `"layer"`. If omitted, derived from `--source-pdf`'s (or the input tif's) filename -- see "Dynamic naming" below. |
| `--source-pdf` | *(none)* | Only used so `--layer` can be auto-derived from the original PDF's filename; the PDF itself isn't read. |
| `--min-area` | 8000 | Minimum polygon area (m²) to keep; smaller = noise, filtered out |
| `--simplify` | 30 | Douglas-Peucker simplification tolerance, in meters |
| `--color-tolerance` | 28 | Per-channel RGB matching tolerance (0-255) |
| `--threshold` | 80 | Dark-pixel cutoff used to auto-detect the page frame/legend panel/inset box |
| `--close-kernel` | 5 | Morphological closing kernel (pixels); bridges thin lines crossing a filled area |
| `--output-crs` | EPSG:4326 | CRS of the output GeoJSON coordinates |
| `--area-crs` | EPSG:28356 | Projected CRS used only for area_m2/area_hectares math (pick one appropriate to your region) |
| `--categories` | *auto-resolved* | Path to a categories.json. Overrides auto-resolution by `--layer`/`--council` (see Step C). |
| `--outdir` | *(next to output file)* | Where the report + preview get written |
| `--debug` | off | Verbose logging to stderr |

---

## Dynamic naming: `--council` and `--layer`

This project is meant to work on **any** council's PDF map, not just Banana
Shire's, and on **any** overlay type (agricultural land, flood zone, bushfire
hazard, whatever the council publishes) -- without editing a single line of
code for each new map. Two flags drive everything:

- **`--council`** -- your council/shire's name. Default: `banana_shire`.
  You always set this once per council (`--council "Banana Shire"`,
  `--council "Gladstone Regional Council"`, etc.) and every output file and
  feature gets tagged with it.
- **`--layer`** -- the type of overlay this particular PDF shows
  (`agricultural`, `flood_zone`, `bushfire_hazard`...). If you don't pass it,
  it's **auto-derived from the filename** you give `--source-pdf` (or, if
  that's not given, from the `input_tif` filename), lower-cased and with
  punctuation turned into underscores:

  | Input filename | Auto-derived `--layer` |
  |---|---|
  | `agricultural.pdf` | `agricultural` |
  | `floodZone.pdf` | `flood_zone` |
  | `agricultural_georeferenced.tif` | `agricultural` (the `_georeferenced` suffix is stripped) |
  | `bananashire-agricultural-lan-new.pdf` | `bananashire_agricultural_lan_new` (messy filenames auto-derive messily -- see note below) |

  **If your source PDF has a messy/inconsistent filename** (like the last
  row above), don't rely on auto-derivation -- just pass `--layer` explicitly:
  `--layer agricultural`. Auto-derivation is a convenience for cleanly-named
  files, not a requirement; passing `--layer` always wins.

Together they decide:
1. **Output filename** (when you don't type one): `data/output/<council-slug>_<layer>.geojson`, e.g. `banana_shire_agricultural.geojson`.
2. **Which categories file loads** (when you don't pass `--categories`): first tries `config/categories.<layer>.json`, then `config/categories.<council-slug>.json`, then falls back to the built-in Banana Shire colors with a loud `WARNING` telling you to make your own (see Step C above).
3. **Tags on every feature**: `"council"` and `"layer"` properties, plus `"council"`/`"layer"`/`"name"` on the FeatureCollection itself -- so if you ever merge geojsons from multiple councils or overlay types into one database table, you can tell them apart.

### "What color do I tell it?" (the part that trips people up)

`extract_map_features.py` never looks at a PDF and never guesses what
"agricultural land" looks like -- it only compares raw pixel colors against
whatever RGB values you put in a `categories.json`. So the "color" is just:
**the literal fill color of that category's swatch in the map's own printed
legend**, expressed as three 0-255 numbers.

You get that number with `sample_legend_color.py`, not by typing in a color
you think it "should" be:

1. After Step A (`pdf_to_raster.py`), crop just the legend box out of the
   rendered image (any image editor/screenshot tool -- doesn't need to be
   precise, just get the swatches in frame) and save it, e.g.
   `data/input/legend_crop.png`.
2. Open that crop in any viewer that shows pixel coordinates on hover
   (Preview, GIMP, even VS Code's image preview) and note roughly where each
   colored swatch is.
3. Run:
   ```bash
   python scripts/sample_legend_color.py data/input/legend_crop.png 375 340 375 420
   ```
   (one `x y` pair per swatch -- pass as many as you have). It prints the
   *exact* RGB Claude/you don't have to guess, plus a ready-to-paste JSON
   snippet, e.g. `RGB [230, 185, 45]`.
4. Paste those into `config/categories.<layer>.json`, matching each RGB to
   the label printed next to that swatch on the map (see Step C / `docs/ADDING_A_NEW_MAP.md`
   for the exact file format).

That's the whole "color" step -- there's no other configuration needed, and
you do it once per distinct legend style, not once per PDF.

### Step E (optional, later) -- No database needed right now

For now this project is a **simple file store**: `data/output/*.geojson` is
the data store. That's enough to load into QGIS, a web map (Leaflet/Mapbox
can read GeoJSON directly), or process with `geopandas.read_file()` /
`jq` / any GIS tool. You do not need a database to use any of this.

When/if you do want one later, `scripts/db_import.py` already supports
three backends, and `config/db_config.example.txt` has commented,
copy-pasteable connection settings for each one -- copy it to
`config/db_config.txt` (git-ignored) and fill in your real values when
that day comes:

```bash
# PostGIS
python scripts/db_import.py data/output/banana_shire_agricultural.geojson \
    --backend postgis --conn "postgresql://user:pass@localhost:5432/mydb" \
    --table agricultural_land --if-exists replace

# GeoPackage (single local file, opens directly in QGIS -- easiest option)
python scripts/db_import.py data/output/banana_shire_agricultural.geojson \
    --backend gpkg --out data/output/banana_shire_agricultural.gpkg --table agricultural_land

# MongoDB
python scripts/db_import.py data/output/banana_shire_agricultural.geojson \
    --backend mongodb --conn "mongodb://localhost:27017" \
    --db mapdata --collection agricultural_land
```

If your target is something else entirely (a REST API, a CSV of centroids,
BigQuery, etc.), the `.geojson` in `data/output/` is a standard
FeatureCollection -- `geopandas.read_file()` or any GIS/JSON tool can take
it from there.

---

## 2.1 Worked example, start to finish

Say your council PDF is `data/input/bananashire-agricultural-lan-new.pdf`
(a messy, real-world filename) and it's the agricultural-land overlay for
Banana Shire. Here's the exact sequence:

```bash
# A. Rasterize
python scripts/pdf_to_raster.py \
    data/input/bananashire-agricultural-lan-new.pdf \
    data/input/bananashire-agricultural-lan-new_raw.png \
    --dpi 400

# B. Georeference in QGIS (manual, one time) -- see docs/GEOREFERENCING_IN_QGIS.md.
#    Export as data/input/bananashire-agricultural-lan-new_georeferenced.tif

# C. Sample the legend colors (skip this if config/categories.agricultural.json
#    already matches this map's legend -- it does for Banana Shire's own map,
#    since that file ships with this repo already sampled from it)
python scripts/sample_legend_color.py data/input/legend_crop.png 375 340 375 420

# D. Extract -- note --layer is given explicitly because the filename is
#    messy (it would otherwise auto-derive to the whole ugly filename)
python scripts/extract_map_features.py \
    data/input/bananashire-agricultural-lan-new_georeferenced.tif \
    --council "Banana Shire" \
    --layer agricultural \
    --debug
# -> config/categories.agricultural.json is found automatically
# -> writes data/output/banana_shire_agricultural.geojson
# -> also writes extraction_report.json + extraction_preview.png next to it

# E. (optional, later) load into a database -- see config/db_config.example.txt
```

Open `data/output/extraction_preview.png` first to sanity-check the traced
outlines sit on the real colored areas, then use
`data/output/banana_shire_agricultural.geojson` directly, or load it via
`scripts/db_import.py` whenever you actually stand up a database.

---

## 3. Project layout

```
banana-map-extraction/
├── README.md                          <- you are here
├── requirements.txt                   <- core deps
├── requirements-db.txt                <- optional, for PostGIS
├── requirements-mongo.txt             <- optional, for MongoDB
├── .gitignore
├── scripts/
│   ├── pdf_to_raster.py               <- Step A
│   ├── sample_legend_color.py         <- Step C helper
│   ├── extract_map_features.py        <- Step D (the main script)
│   └── db_import.py                   <- Step E
├── config/
│   ├── categories.agricultural.json   <- legend-color table for the "agricultural" layer (Banana Shire's actual colors)
│   ├── categories.banana_shire.json   <- same colors, keyed by council name instead (legacy/alternate lookup)
│   └── db_config.example.txt          <- commented connection settings for Step E, when/if you need a DB
├── data/
│   ├── input/                         <- put source PDFs/TIFs here
│   └── output/                        <- geojson/report/preview land here
└── docs/
    ├── GEOREFERENCING_IN_QGIS.md
    └── ADDING_A_NEW_MAP.md
```

## 4. Troubleshooting

- **`rasterio`/`fiona`/`GDAL` fails to install** -- see the GDAL prerequisite
  note in 1.1, or switch to the conda install path in 1.2.
- **`ERROR: input raster has no valid georeferencing`** -- you passed the
  plain image from Step A instead of the georeferenced GeoTIFF from Step B.
  Re-check that you actually ran "Georeferencer > File > Start Georeferencing"
  in QGIS and pointed the script at *that* output file.
- **`extraction_preview.png` traces don't line up with the map** -- the
  georeferencing (Step B), not the extraction script, is the problem. Add
  more/better-distributed ground control points and re-export the GeoTIFF.
- **A whole color category is missing from the output** -- check
  `extraction_report.json`'s `layout_detected` block; if the legend panel or
  an inset box wasn't detected correctly, part of your map may have been
  excluded. You can also just crop that panel/inset out of the raster
  yourself in Step A/B as a more reliable fix.
- **Script runs out of memory on a huge GeoTIFF** -- downsample the raster
  first (e.g. `gdal_translate -outsize 50% 50% in.tif out.tif`) or process
  on a machine with more RAM; very large scanned maps (>500 MB) can need
  several GB free.
