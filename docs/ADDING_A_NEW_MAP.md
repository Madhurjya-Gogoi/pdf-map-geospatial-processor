# Adding a new map (different legend, different colors)

`extract_map_features.py` classifies pixels by comparing them to colors you
tell it about -- it does not know in advance what "agricultural land" or
"stock route" looks like. For a new map with a different legend, you build a
small `categories.json` once, then reuse it for every future map that shares
that same legend style.

## 1. Get a clean crop of the legend

After running `pdf_to_raster.py`, open the resulting image in any editor and
crop out just the legend box (the little swatches + labels). Save it as its
own image, e.g. `data/input/legend_crop.png`. You don't need to be pixel-perfect
-- just get the legend clearly in frame.

## 2. Find each swatch's real color

Open `legend_crop.png` in any viewer that shows pixel coordinates on hover
(Preview on Mac, most image editors, even VS Code's built-in image preview
shows coordinates), and note the approximate center (x, y) of each color
swatch.

Then run:

```bash
python scripts/sample_legend_color.py data/input/legend_crop.png 127 440 127 367 127 640
```

(pass as many `x y` pairs as you have swatches -- one pair per swatch). It
prints the exact RGB at each point, plus a ready-to-paste JSON snippet:

```
(127,440) -> RGB [230, 184, 44]     categories.json snippet: {"name": "REPLACE_ME", "rgb": [230, 184, 44], "style": "polygon"}
```

## 3. Build categories.json

Collect the snippets, fill in real names (matching the labels printed next
to each swatch on the map), and save as e.g.
`config/categories.my_new_map.json`:

```json
[
  {"name": "Agricultural land", "rgb": [230, 184, 44], "style": "polygon"},
  {"name": "Residential area",  "rgb": [255, 200, 200], "style": "polygon"},
  {"name": "Road reserve",      "rgb": [120, 120, 120], "style": "polygon"}
]
```

Only include categories that are **filled areas with a distinct, consistent
color**. Skip:
- Categories with no fill (e.g. a legend entry that's just an unfilled
  white/outlined box) -- there's no color to classify against.
- Categories that are just line styles (dashed boundaries, etc.) unless the
  line is thick/solid enough to reliably classify as a filled color; thin
  dashed lines are usually better handled as a "known limitation" than
  force-fit into this pixel-color approach.

## 4. Run extraction with your new config

```bash
python scripts/extract_map_features.py \
    data/input/my_new_map_georeferenced.tif \
    data/output/my_new_map.geojson \
    --categories config/categories.my_new_map.json \
    --debug
```

## 5. Tune if needed

Check `extraction_preview.png` first. Then:

- **Missing chunks of a category / ragged edges:** increase
  `--color-tolerance` a little (e.g. 28 -> 36) if the fill has anti-aliasing
  or slight print/scan color drift. Increase `--close-kernel` if thin lines
  (e.g. cadastre/property boundaries) are fragmenting a should-be-solid area
  into many tiny pieces.
- **Adjacent categories bleeding into each other:** decrease
  `--color-tolerance` instead, and/or check the two colors aren't too close
  together (e.g. two shades of orange) -- if they're genuinely similar, you
  may need a tighter tolerance and to accept some misclassification at the
  boundary between them.
- **Whole extra area appearing that shouldn't be there:** almost always the
  legend-panel/inset-box auto-detection missing something (see
  `extraction_report.json`'s `layout_detected`). Easiest fix is cropping
  those regions out before georeferencing, as noted in
  GEOREFERENCING_IN_QGIS.md.
- **Too many tiny noise polygons:** increase `--min-area`.
- **Polygons too blocky/pixelated:** this is usually a raster-resolution
  limit, not a script setting -- re-render Step A at a higher `--dpi` and
  re-georeference, since you can't recover detail that was never rasterized
  in the first place. `--simplify` set very low (e.g. `0`) will show you the
  raw, unsimplified pixel-accuracy outline if you want to check this.
