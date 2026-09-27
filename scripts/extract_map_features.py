#!/usr/bin/env python3
"""
extract_map_features.py

Extracts categorized area polygons (e.g. "Agricultural land", "Stock route")
from a georeferenced planning-scheme overlay raster (GeoTIFF) that was produced
by flattening a scanned/rasterized PDF map and registering it in QGIS.

The GeoTIFF's own CRS + affine transform is treated as the single source of
truth for geographic coordinates. Pixel colors are classified against colors
sampled from the map's own legend swatches, converted to polygons, then those
polygon vertices are converted from pixel space -> the raster's CRS -> EPSG:4326
(or whatever --output-crs is given) using the affine transform and pyproj.

No coordinate is ever invented from visual inspection of the PDF: every vertex
written to the output GeoJSON is `transform * (col, row)` for some (col, row)
that was actually classified as belonging to that feature in the raster.

Usage (explicit):
    python extract_map_features.py input.tif output.geojson \
        --min-area 5000 --simplify 30 --color-tolerance 28 \
        --output-crs EPSG:4326 --debug

Usage (dynamic naming -- recommended; see "Dynamic naming" in README.md):
    python extract_map_features.py input.tif \
        --council "Banana Shire" --layer agricultural --debug
    # -> auto-writes data/output/banana_shire_agricultural.geojson,
    #    auto-loads config/categories.agricultural.json,
    #    and tags every feature + the report with council/layer.

Outputs (next to output.geojson, or in --outdir if given):
    <output.geojson>
    extraction_report.json
    extraction_preview.png
"""

import argparse
import json
import os
import re
import sys
from datetime import datetime, timezone

import numpy as np
import cv2
import rasterio
from rasterio.windows import Window
import shapely
import shapely.geometry
from shapely.geometry import Polygon, mapping
from shapely.ops import transform as shp_transform
from shapely.validation import make_valid
import pyproj

SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
PROJECT_ROOT = os.path.dirname(SCRIPT_DIR)
CONFIG_DIR = os.path.join(PROJECT_ROOT, "config")

# Filename suffixes the pipeline itself tends to add (pdf_to_raster.py /
# QGIS export conventions). Stripped off before deriving a --layer name from
# a PDF/TIF filename, so "agricultural_georeferenced.tif" and
# "agricultural_raw.png" both still derive the layer "agricultural".
_AUTO_LAYER_STRIP_SUFFIXES = ("_georeferenced", "_geo", "_raw", "_gcp", "-georeferenced")


# ----------------------------------------------------------------------------
# Legend / category color definitions.
#
# These were sampled directly from the Banana Shire Agricultural Land Overlay
# Map's own legend swatches (see config/categories.agricultural.json). They
# are used ONLY as a last-resort fallback -- when neither --categories nor
# a matching config/categories.<layer>.json / categories.<council>.json file
# can be found -- so that the very first run of this script (before you've
# made a categories file for your own map) still does something sensible.
# For any real map, create your own categories.json; see "What color do I
# tell it?" in README.md and docs/ADDING_A_NEW_MAP.md.
# ----------------------------------------------------------------------------
DEFAULT_CATEGORIES = [
    # name,               RGB color,        geometry style
    ("Agricultural land", (230, 184, 44),  "polygon"),
    ("Stock route",       (0,   255,  0),  "polygon"),
]


def slugify(text):
    """'Banana Shire' -> 'banana_shire'; 'Flood Zone!!' -> 'flood_zone'."""
    text = text.strip().lower()
    text = re.sub(r"[^a-z0-9]+", "_", text)
    return text.strip("_") or "layer"


def derive_layer_name(*candidate_paths):
    """
    Auto-derives a --layer slug from the first usable filename among
    candidate_paths (e.g. the --source-pdf if given, else the input_tif),
    stripping common pipeline suffixes first. 'agricultural.pdf' -> 'agricultural';
    'floodZone_georeferenced.tif' -> 'flood_zone'.
    """
    for p in candidate_paths:
        if not p:
            continue
        base = os.path.splitext(os.path.basename(p))[0]
        for suf in _AUTO_LAYER_STRIP_SUFFIXES:
            if base.lower().endswith(suf):
                base = base[: -len(suf)]
                break
        slug = slugify(base)
        if slug:
            return slug
    return "layer"


def resolve_categories_path(explicit_path, layer, council_slug):
    """
    Decides which categories.json to load, in this order:
      1. --categories, if given explicitly (always wins).
      2. config/categories.<layer>.json
      3. config/categories.<council_slug>.json   (legacy/alternate convention)
      4. None -> caller falls back to DEFAULT_CATEGORIES with a warning.
    Returns (path_or_None, how) where `how` explains the choice for logging.
    """
    if explicit_path:
        return explicit_path, "explicit --categories"
    by_layer = os.path.join(CONFIG_DIR, f"categories.{layer}.json")
    if os.path.isfile(by_layer):
        return by_layer, f"auto-matched config/categories.{layer}.json from --layer"
    by_council = os.path.join(CONFIG_DIR, f"categories.{council_slug}.json")
    if os.path.isfile(by_council):
        return by_council, f"auto-matched config/categories.{council_slug}.json from --council"
    return None, None


def load_categories(path):
    """
    Loads a category color table from a JSON file so this script can be
    reused on a *different* map without editing source code. Expected format:

    [
      {"name": "Agricultural land", "rgb": [230, 184, 44], "style": "polygon"},
      {"name": "Stock route",       "rgb": [0, 255, 0],    "style": "polygon"}
    ]

    "style" is currently informational only (stored as a confidence hint);
    every category is extracted as polygon geometry regardless, since that's
    what a filled-area raster classification produces. See
    docs/ADDING_A_NEW_MAP.md for how to find these RGB values on a new map.
    """
    with open(path) as f:
        data = json.load(f)
    cats = []
    for entry in data:
        name = entry["name"]
        rgb = tuple(int(v) for v in entry["rgb"])
        style = entry.get("style", "polygon")
        cats.append((name, rgb, style))
    if not cats:
        raise ValueError(f"No categories found in {path}")
    return cats


def log(msg, debug=False, force=False):
    if debug or force:
        print(msg, file=sys.stderr)


# ----------------------------------------------------------------------------
# Step 1: page-layout detection (frame border / legend panel / inset locator)
# ----------------------------------------------------------------------------
def detect_layout(gray, debug=False):
    """
    Detects the print layout of a typical council/shire overlay map:
      - an outer black frame around the whole page
      - a vertical divider separating the map from a title/legend side panel
      - a small inset "locator" map box (usually top-left)
    Returns a dict of pixel bounds. Any element that can't be confidently
    detected is left as None and simply not excluded.
    """
    h, w = gray.shape
    dark = (gray < 80).astype(np.uint8)

    col_sum = dark.sum(axis=0)
    row_sum = dark.sum(axis=1)

    # Outer border: strongest near-full-height column near the left edge,
    # and near-full-width row near the top edge.
    left_candidates = np.where(col_sum[: w // 4] > 0.9 * h)[0]
    top_candidates = np.where(row_sum[: h // 4] > 0.9 * w)[0]
    right_candidates = np.where(col_sum[3 * w // 4 :] > 0.9 * h)[0] + 3 * w // 4
    bottom_candidates = np.where(row_sum[3 * h // 4 :] > 0.9 * w)[0] + 3 * h // 4

    outer_left = int(left_candidates.min()) if len(left_candidates) else 0
    outer_top = int(top_candidates.min()) if len(top_candidates) else 0
    outer_right = int(right_candidates.max()) if len(right_candidates) else w - 1
    outer_bottom = int(bottom_candidates.max()) if len(bottom_candidates) else h - 1

    # Legend/title panel divider: next strong near-full-height vertical line,
    # searched from ~55% width onward (panels are conventionally on the right).
    search_start = int(w * 0.55)
    mid_cols = np.where(col_sum[search_start:outer_right] > 0.9 * h)[0]
    panel_x = int(mid_cols.min() + search_start) if len(mid_cols) else None

    # Inset locator box: look for a solid rectangular dark border in the
    # top-left quadrant (roughly x<45%, y<45% of the page). We require the
    # candidate to be clearly *smaller* than the outer page frame (otherwise
    # contour merging can pick up the outer frame itself) and to be a roughly
    # 4-cornered rectangle, and among valid candidates we keep the smallest
    # one that's still large enough to be a real inset (avoids picking up
    # text blocks / stray line noise).
    inset_box = None
    quad = dark[: int(h * 0.45), : int(w * 0.45)] * 255
    contours, _ = cv2.findContours(quad, cv2.RETR_LIST, cv2.CHAIN_APPROX_SIMPLE)
    candidates = []
    for c in contours:
        x, y, bw, bh = cv2.boundingRect(c)
        if bw < 0.08 * w or bh < 0.08 * h:
            continue
        if bw > 0.32 * w or bh > 0.32 * h:
            continue  # too big to be the small locator inset -> likely the outer frame
        peri = cv2.arcLength(c, True)
        approx = cv2.approxPolyDP(c, 0.02 * peri, True)
        if len(approx) < 4 or len(approx) > 8:
            continue  # not rectangle-ish
        candidates.append((x, y, bw, bh, bw * bh))
    if candidates:
        # smallest plausible rectangle = the actual inset border (biggest
        # remaining contours after the size cap tend to be near-duplicates
        # of the same border at slightly different thicknesses)
        x, y, bw, bh, _ = min(candidates, key=lambda r: r[4])
        inset_box = (x, y, x + bw, y + bh)

    layout = {
        "outer": (outer_left, outer_top, outer_right, outer_bottom),
        "panel_x": panel_x,
        "inset_box": inset_box,
    }
    log(f"[layout] {layout}", debug=debug)
    return layout


def build_valid_mask(shape, layout):
    """Boolean mask (True = usable map area) from the detected layout."""
    h, w = shape
    mask = np.zeros((h, w), dtype=bool)
    l, t, r, b = layout["outer"]
    r_cut = layout["panel_x"] if layout["panel_x"] else r
    mask[t:b, l:r_cut] = True
    if layout["inset_box"]:
        ix0, iy0, ix1, iy1 = layout["inset_box"]
        mask[iy0:iy1, ix0:ix1] = False
    return mask


# ----------------------------------------------------------------------------
# Step 2: color classification
# ----------------------------------------------------------------------------
def classify_color(rgb, color, tolerance):
    """cv2.inRange-based classification for one target RGB color."""
    lo = np.array([max(0, c - tolerance) for c in color], dtype=np.uint8)
    hi = np.array([min(255, c + tolerance) for c in color], dtype=np.uint8)
    return cv2.inRange(rgb, lo, hi)


# ----------------------------------------------------------------------------
# Step 3: mask -> polygons (pixel space), keeping holes
# ----------------------------------------------------------------------------
def mask_to_polygons(mask_u8, min_area_px, close_kernel=5, simplify_px=0.0, debug=False):
    """
    Cleans a binary mask (closes small gaps from cadastre/property lines
    crossing a filled area) then extracts polygons with their holes via
    RETR_CCOMP contour hierarchy.
    """
    if close_kernel > 0:
        k = cv2.getStructuringElement(cv2.MORPH_ELLIPSE, (close_kernel, close_kernel))
        mask_u8 = cv2.morphologyEx(mask_u8, cv2.MORPH_CLOSE, k)

    contours, hierarchy = cv2.findContours(mask_u8, cv2.RETR_CCOMP, cv2.CHAIN_APPROX_SIMPLE)
    polygons = []
    if hierarchy is None:
        return polygons
    hierarchy = hierarchy[0]

    children = {}
    for idx, h in enumerate(hierarchy):
        parent = h[3]
        if parent != -1:
            children.setdefault(parent, []).append(idx)

    for idx, h in enumerate(hierarchy):
        parent = h[3]
        if parent != -1:
            continue  # this is a hole, handled via its parent
        c = contours[idx]
        if cv2.contourArea(c) < min_area_px:
            continue
        if simplify_px > 0:
            c = cv2.approxPolyDP(c, simplify_px, True)
        if len(c) < 3:
            continue
        exterior = [tuple(p[0]) for p in c]

        holes = []
        for hidx in children.get(idx, []):
            hc = contours[hidx]
            if cv2.contourArea(hc) < min_area_px:
                continue
            if simplify_px > 0:
                hc = cv2.approxPolyDP(hc, simplify_px, True)
            if len(hc) < 3:
                continue
            holes.append([tuple(p[0]) for p in hc])

        try:
            poly = Polygon(exterior, holes)
            if not poly.is_valid:
                poly = make_valid(poly)
            if poly.is_empty:
                continue
            polygons.append(poly)
        except Exception as e:
            log(f"[mask_to_polygons] skipped bad polygon: {e}", debug=debug)
    return polygons


# ----------------------------------------------------------------------------
# Step 4: pixel polygon -> raster CRS -> output CRS
# ----------------------------------------------------------------------------
def pixel_polygon_to_crs(poly, affine):
    """Applies the raster's affine transform to every vertex of a polygon."""

    def fwd(x, y, z=None):
        xs, ys = affine * (x, y)
        return xs, ys

    return shp_transform(fwd, poly)


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("input_tif")
    ap.add_argument("output_geojson", nargs="?", default=None,
                     help="Where to write the GeoJSON. Optional: if omitted, it is "
                          "auto-generated as data/output/<council>_<layer>.geojson "
                          "(or --outdir/<council>_<layer>.geojson if --outdir is given).")
    ap.add_argument("--council", type=str, default="banana_shire",
                     help="Council/shire name. Used to (a) auto-name the output file when "
                          "output_geojson is omitted, and (b) tag every output feature with "
                          "a 'council' property. Default: 'banana_shire'. Spaces/mixed case "
                          "are fine ('Banana Shire') -- it's slugified for filenames.")
    ap.add_argument("--layer", type=str, default=None,
                     help="Layer name for this map, e.g. 'agricultural', 'flood_zone'. Used "
                          "to (a) auto-find config/categories.<layer>.json, (b) auto-name the "
                          "output file, and (c) tag every output feature with a 'layer' "
                          "property. If omitted, it is auto-derived from the filename of "
                          "--source-pdf (preferred) or input_tif, e.g. 'agricultural.pdf' or "
                          "'agricultural_georeferenced.tif' -> layer 'agricultural'.")
    ap.add_argument("--source-pdf", type=str, default=None,
                     help="Optional: path to the original source PDF this GeoTIFF was "
                          "rasterized from (the file you fed to pdf_to_raster.py). Only used "
                          "to auto-derive --layer's name when --layer isn't given explicitly "
                          "-- the PDF itself is not read.")
    ap.add_argument("--min-area", type=float, default=8000,
                     help="Minimum polygon area in output-CRS-appropriate sq meters (default 8000 m^2 = 0.8 ha)")
    ap.add_argument("--simplify", type=float, default=30.0,
                     help="Douglas-Peucker simplification tolerance in meters (default 30)")
    ap.add_argument("--threshold", type=int, default=80,
                     help="Dark-pixel threshold (0-255) used for frame/border detection (default 80)")
    ap.add_argument("--color-tolerance", type=int, default=28,
                     help="Per-channel RGB tolerance for legend color matching (default 28)")
    ap.add_argument("--output-crs", type=str, default="EPSG:4326")
    ap.add_argument("--area-crs", type=str, default="EPSG:28356",
                     help="Projected CRS used to compute area_m2/area_hectares (default GDA94/MGA zone 56)")
    ap.add_argument("--close-kernel", type=int, default=5,
                     help="Morphological closing kernel size in pixels, bridges thin cadastre lines (default 5)")
    ap.add_argument("--outdir", type=str, default=None)
    ap.add_argument("--categories", type=str, default=None,
                     help="Path to a JSON category-color config (see docs/ADDING_A_NEW_MAP.md). "
                          "If omitted, uses the built-in colors sampled from the Banana Shire "
                          "Agricultural Land Overlay Map legend.")
    ap.add_argument("--debug", action="store_true")
    args = ap.parse_args()

    # ---- Resolve dynamic naming: council / layer / categories / output path
    council_slug = slugify(args.council)
    layer = slugify(args.layer) if args.layer else derive_layer_name(args.source_pdf, args.input_tif)

    cat_path, cat_reason = resolve_categories_path(args.categories, layer, council_slug)
    if cat_path:
        categories = load_categories(cat_path)
        log(f"[categories] loaded {len(categories)} categories from {cat_path} ({cat_reason})",
            force=True)
    else:
        categories = DEFAULT_CATEGORIES
        print(
            f"WARNING: no categories file found for layer='{layer}' or council='{council_slug}' "
            f"(looked for config/categories.{layer}.json and config/categories.{council_slug}.json). "
            f"Falling back to the built-in Banana Shire Agricultural Land colors "
            f"({[c[0] for c in DEFAULT_CATEGORIES]}) -- these are almost certainly WRONG for "
            f"your map. Sample your map's own legend colors with scripts/sample_legend_color.py "
            f"and save them as config/categories.{layer}.json, then re-run. "
            f"See 'What color do I tell it?' in README.md.",
            file=sys.stderr,
        )

    outdir = args.outdir or (
        os.path.dirname(os.path.abspath(args.output_geojson)) if args.output_geojson
        else os.path.join(PROJECT_ROOT, "data", "output")
    )
    os.makedirs(outdir, exist_ok=True)

    output_geojson = args.output_geojson or os.path.join(outdir, f"{council_slug}_{layer}.geojson")
    if not args.output_geojson:
        log(f"[naming] output_geojson not given -> auto-named {output_geojson}", force=True)
    args.output_geojson = output_geojson

    # Report + preview are named after whatever the geojson ended up being
    # called (auto-named or explicit) so they never collide across overlays:
    # banana_shire_agricultural.geojson -> ..._agricultural_report.json /
    # ..._agricultural_preview.png, banana_shire_flood_zone.geojson -> its
    # own pair, etc. Each run's three output files always share one prefix.
    output_stem = os.path.splitext(os.path.basename(output_geojson))[0]
    report_path = os.path.join(outdir, f"{output_stem}_report.json")
    preview_path = os.path.join(outdir, f"{output_stem}_preview.png")

    report = {
        "source_file": os.path.abspath(args.input_tif),
        "council": args.council,
        "layer": layer,
        "categories_file": cat_path,
        "run_time_utc": datetime.now(timezone.utc).isoformat(),
        "crs": None,
        "output_crs": args.output_crs,
        "area_crs": args.area_crs,
        "raster_bounds": {},
        "raster_size": {},
        "pixel_size": {},
        "gcp_count": 0,
        "gcp_note": "",
        "categories": [c[0] for c in categories],
        "feature_count": 0,
        "valid_features": 0,
        "invalid_features": 0,
        "removed_noise_features": 0,
        "layout_detected": {},
        "limitations": [
            "Cadastre and Cadastre water parcel legend categories are not extracted as "
            "separate area features: 'Cadastre' has no fill color (white, same as page "
            "background) and 'Cadastre water parcel' is a very faint tint covering a tiny "
            "pixel count, so neither can be reliably distinguished from background/noise "
            "at this raster's ~61 m pixel resolution. These would require re-exporting a "
            "higher-resolution GeoTIFF from QGIS, or a separate line-detection pass for the "
            "thin cadastre boundary lines.",
            "'Stock route' is cartographically a line feature; what's extracted here is the "
            "rasterized footprint (width) of the drawn line, not a true single-vertex "
            "centerline, so stock-route geometries are polygons of small width rather than "
            "LineStrings. Treat stock-route geometry as approximate route corridors.",
            "Source pixel size is ~61 m, so any polygon edge is only accurate to roughly "
            "one pixel (~60-90 m) at best; this is fine for shire-scale overlay reference "
            "but not for cadastral/legal boundary determination.",
            "The LGA boundary line itself (dashed maroon) and all text/legend/title-block "
            "content were intentionally excluded and are not present as features.",
        ],
        "warnings": [],
    }

    # ---- Step 1: open + validate georeferencing --------------------------
    with rasterio.open(args.input_tif) as src:
        if src.crs is None or src.transform is None or src.transform.is_identity:
            print("ERROR: input raster has no valid georeferencing "
                  "(CRS/transform missing or identity). Re-export the GeoTIFF "
                  "from QGIS with 'Georeference' / 'Save As' including CRS.", file=sys.stderr)
            sys.exit(1)

        report["crs"] = str(src.crs)
        report["raster_bounds"] = {
            "left": src.bounds.left, "bottom": src.bounds.bottom,
            "right": src.bounds.right, "top": src.bounds.top,
        }
        report["raster_size"] = {"width": src.width, "height": src.height, "bands": src.count}
        report["pixel_size"] = {"x": src.res[0], "y": src.res[1]}
        gcps, gcp_crs = src.gcps
        report["gcp_count"] = len(gcps)
        if len(gcps) == 0:
            report["gcp_note"] = ("No GCP list embedded in this GeoTIFF; the raster's own "
                                   "affine transform/CRS is used directly as the authoritative "
                                   "georeferencing source, per the GeoTIFF header.")
        else:
            report["gcp_note"] = f"{len(gcps)} embedded GCPs found; geotransform used as authoritative."

        log(f"CRS: {src.crs}", debug=True, force=True)
        log(f"Bounds: {src.bounds}", debug=True, force=True)
        log(f"Size: {src.width}x{src.height}, bands={src.count}", debug=True, force=True)
        log(f"Pixel size: {src.res}", debug=True, force=True)

        affine = src.transform
        n_bands = src.count

        # ---- Step 2: read RGB, detect layout, build valid-area mask -----
        rgb = np.transpose(src.read([1, 2, 3]), (1, 2, 0))  # HxWx3 uint8

    gray = cv2.cvtColor(rgb, cv2.COLOR_RGB2GRAY)
    layout = detect_layout(gray, debug=args.debug)
    report["layout_detected"] = {
        "outer_frame_px": layout["outer"],
        "legend_panel_divider_x_px": layout["panel_x"],
        "inset_locator_box_px": layout["inset_box"],
    }
    valid_mask = build_valid_mask(gray.shape, layout)
    log(f"Valid map-area pixels: {valid_mask.sum()} / {valid_mask.size} "
        f"({100*valid_mask.sum()/valid_mask.size:.1f}%)", debug=args.debug, force=True)

    # ---- Step 3: classify + extract polygons per category ----------------
    to_out_crs = pyproj.Transformer.from_crs(report["crs"], args.output_crs, always_xy=True).transform
    to_area_crs = pyproj.Transformer.from_crs(report["crs"], args.area_crs, always_xy=True).transform

    valid_mask_u8 = valid_mask.astype(np.uint8) * 255

    features = []
    fid = 1
    noise_removed = 0
    invalid_count = 0
    preview_overlay = rgb.copy()
    category_debug_masks = {}

    for name, color, geom_style in categories:
        color_mask = classify_color(rgb, color, args.color_tolerance)
        color_mask = cv2.bitwise_and(color_mask, valid_mask_u8)
        category_debug_masks[name] = color_mask

        # px^2 -> m^2 threshold using the raster's own pixel size
        px_area_m2 = abs(affine.a * affine.e)
        min_area_px = args.min_area / px_area_m2 if px_area_m2 > 0 else args.min_area
        simplify_px = args.simplify / (abs(affine.a)) if affine.a else 0.0

        polys_px = mask_to_polygons(
            color_mask, min_area_px=min_area_px,
            close_kernel=args.close_kernel, simplify_px=simplify_px, debug=args.debug,
        )
        log(f"[{name}] {len(polys_px)} candidate polygons after cleanup", debug=args.debug, force=True)

        for poly_px in polys_px:
            try:
                poly_crs = pixel_polygon_to_crs(poly_px, affine)
                # buffer(0) is the standard, robust fix for the self-crossing
                # rings that approxPolyDP simplification / morphological
                # closing can introduce (e.g. where a stock-route line loops
                # over itself). It's preferred over make_valid() here because
                # make_valid() can leave heavily overlapping duplicate slivers
                # that silently inflate .area, whereas buffer(0) normalizes
                # the polygon into non-overlapping simple parts.
                if not poly_crs.is_valid:
                    poly_crs = poly_crs.buffer(0)
                if poly_crs.is_empty:
                    invalid_count += 1
                    continue
                # Flatten to a plain list of simple Polygons, whatever shape
                # make_valid()/buffer(0) handed back (Polygon, MultiPolygon,
                # or a GeometryCollection mixing Polygon/MultiPolygon/Line/
                # Point parts -- a Polygon-only filter here would silently
                # drop real area sitting inside MultiPolygon members).
                def _flatten_polys(g):
                    if g.is_empty:
                        return []
                    if g.geom_type == "Polygon":
                        return [g]
                    if g.geom_type in ("MultiPolygon", "GeometryCollection"):
                        out = []
                        for sub in g.geoms:
                            out.extend(_flatten_polys(sub))
                        return out
                    return []  # LineString/Point/etc: not an area feature

                simple_polys = _flatten_polys(poly_crs)
                if not simple_polys:
                    invalid_count += 1
                    continue
                b = report["raster_bounds"]
                for sp in simple_polys:
                    if not sp.is_valid:
                        sp = sp.buffer(0)
                    if sp.is_empty or sp.geom_type != "Polygon":
                        invalid_count += 1
                        continue

                    area_geom = shp_transform(to_area_crs, sp)
                    area_m2 = area_geom.area
                    if area_m2 < args.min_area:
                        noise_removed += 1
                        continue

                    cx, cy = sp.centroid.x, sp.centroid.y
                    if not (b["left"] <= cx <= b["right"] and b["bottom"] <= cy <= b["top"]):
                        invalid_count += 1
                        continue

                    out_geom = shp_transform(to_out_crs, sp)
                    features.append({
                        "type": "Feature",
                        "properties": {
                            "id": fid,
                            "council": args.council,
                            "layer": layer,
                            "category": name,
                            "source": "QGIS_Georeferenced_Map",
                            "confidence": 0.7 if geom_style == "polygon" else 0.5,
                            "area_m2": round(area_m2, 1),
                            "area_hectares": round(area_m2 / 10000.0, 3),
                        },
                        "geometry": mapping(out_geom),
                    })
                    fid += 1
            except Exception as e:
                invalid_count += 1
                log(f"[{name}] dropped a polygon: {e}", debug=args.debug)

    report["feature_count"] = len(features) + invalid_count + noise_removed
    report["valid_features"] = len(features)
    report["invalid_features"] = invalid_count
    report["removed_noise_features"] = noise_removed

    geojson = {
        "type": "FeatureCollection",
        "name": f"{args.council} - {layer}",
        "council": args.council,
        "layer": layer,
        "features": features,
    }
    with open(args.output_geojson, "w") as f:
        json.dump(geojson, f)
    log(f"Wrote {len(features)} features to {args.output_geojson}", force=True)

    with open(report_path, "w") as f:
        json.dump(report, f, indent=2, default=str)
    log(f"Wrote report to {report_path}", force=True)

    # ---- preview image -----------------------------------------------------
    small_scale = max(1, max(rgb.shape[0], rgb.shape[1]) // 1600)
    prev = cv2.resize(rgb, (rgb.shape[1] // small_scale, rgb.shape[0] // small_scale))
    prev_bgr = cv2.cvtColor(prev, cv2.COLOR_RGB2BGR)
    colors_cycle = [(230, 184, 44), (0, 255, 0)]
    for i, (name, color, _) in enumerate(categories):
        mask_small = cv2.resize(category_debug_masks[name], (prev.shape[1], prev.shape[0]),
                                 interpolation=cv2.INTER_NEAREST)
        contours, _ = cv2.findContours(mask_small, cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_SIMPLE)
        bgr_color = tuple(int(c) for c in color[::-1])
        cv2.drawContours(prev_bgr, contours, -1, bgr_color, 2)
    cv2.putText(prev_bgr, f"{len(features)} features extracted", (10, 25),
                cv2.FONT_HERSHEY_SIMPLEX, 0.7, (0, 0, 0), 2)
    cv2.imwrite(preview_path, prev_bgr)
    log(f"Wrote preview to {preview_path}", force=True)

    print(json.dumps({
        "council": args.council,
        "layer": layer,
        "output_geojson": args.output_geojson,
        "categories_file": cat_path,
        "crs": report["crs"],
        "gcp_count": report["gcp_count"],
        "raster_bounds": report["raster_bounds"],
        "pixel_size": report["pixel_size"],
        "feature_count": report["feature_count"],
        "valid_features": report["valid_features"],
        "invalid_features": report["invalid_features"],
        "removed_noise_features": report["removed_noise_features"],
    }, indent=2))


if __name__ == "__main__":
    main()