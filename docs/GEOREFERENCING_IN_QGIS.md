# Georeferencing a map in QGIS

This is the one manual step in the pipeline. It turns a plain image (pixels
only) into a GeoTIFF (pixels + a real-world coordinate system). Everything
downstream is only as accurate as this step, so it's worth doing carefully.

## What you need before starting

- The rasterized image from `pdf_to_raster.py` (Step A in the main README).
- A set of **ground control points (GCPs)**: places you can identify both
  *on the raster* (by clicking) and *in the real world* (by coordinate).
  Good options, roughly in order of preference:
  1. A printed coordinate graticule/grid on the map itself (tick marks with
     easting/northing or lat/lon labels) -- most maps of this style don't
     have one, but check.
  2. Town/locality markers *on the actual map area* -- only useful if the
     main map itself shows them (not just a separate small locator inset;
     an inset map is a different image at a different scale and can't be
     used to georeference the main map).
  3. Recognizable shapes: the outline of the region itself, distinctive
     river bends, road intersections -- matched against an OpenStreetMap or
     satellite basemap loaded in QGIS.
- Ideally 6+ GCPs, spread across the whole extent (not clustered in one
  corner) -- this matters more than having many points bunched together.

## Steps

1. **Open QGIS.** Optionally add a basemap first (Web menu > QuickMapServices
   > OpenStreetMap, installing the plugin if needed) so you have something
   to read real-world coordinates from.

2. **Open the Georeferencer:** `Layer > Georeferencer`.

3. **Load your raster:** `File > Open Raster`, select the image from Step A.

4. **Add GCPs.** For each control point:
   - Click its exact location on the raster (the Georeferencer will prompt
     for a coordinate).
   - Either type the real-world X/Y directly, or click "From Map Canvas" and
     click the matching spot on your basemap in the main QGIS window.
   - Repeat for every GCP. The GCP table at the bottom shows a running
     **residual** for each point -- keep an eye on it.

5. **Check residuals before transforming.** A residual much larger than the
   others usually means you clicked slightly off, or mismatched the point.
   Fix or remove it before proceeding -- one bad GCP can warp the whole
   transform.

6. **Set transformation settings:** `Settings > Transformation Settings`
   - **Transformation type:** Thin Plate Spline generally handles
     printed/scanned map distortion better than a simple 1st-order
     polynomial, especially over large or irregular extents. Polynomial 1
     is fine if your GCPs are very evenly spread and residuals are already low.
   - **Resampling method:** Cubic (or Bilinear) for smoother output.
   - **Target SRS:** the CRS you want the output in -- match whatever the
     source map states (e.g. "GDA94 / MGA Zone 56" -> EPSG:28356). If the
     map doesn't say, a locally-appropriate UTM zone or your country's
     standard projected CRS is a safe choice.
   - **Output raster:** choose a path ending in `.tif` -- this is the file
     you'll hand to `extract_map_features.py`.

7. **Run it:** `File > Start Georeferencing`.

8. **Sanity-check the result.** Load the output GeoTIFF into the main QGIS
   canvas alongside your basemap. Does the shire/region outline land where
   it should? Do known towns/features line up? If not, go back and adjust
   or add GCPs -- don't proceed to extraction on a bad georeference.

## Notes specific to this style of council overlay map

- These maps typically have **three visually distinct zones**: the main map,
  a small locator "inset" map (often top-left), and a title/legend side
  panel. `extract_map_features.py` tries to auto-detect and exclude the
  inset and side panel so their pixels don't get treated as real geography
  -- but if that auto-detection fails on a new map's layout (check
  `extraction_report.json`'s `layout_detected` block), the more reliable
  fix is to just crop those regions out of the image *before* georeferencing,
  so they're never in the raster to begin with.
- If the main map genuinely has no usable GCPs of its own (no graticule, no
  markers, nothing matchable), matching the map's own boundary *shape*
  against an authoritative boundary dataset for that region (e.g. an
  official LGA/administrative boundary layer) via several distinctive
  boundary vertices as GCPs is a good fallback -- it uses information that's
  actually present on the map rather than guessing.
