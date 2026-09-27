#!/usr/bin/env python3
"""
pdf_to_raster.py

Step 0 of the pipeline: converts a page of a PDF map into a plain (NOT yet
georeferenced) high-resolution raster image, ready to be loaded into QGIS's
Georeferencer tool.

This does NOT produce coordinates. It only flattens the PDF into pixels at a
DPI high enough that clicking ground-control points precisely is possible.
Georeferencing itself must be done in QGIS (see docs/GEOREFERENCING_IN_QGIS.md),
producing the GeoTIFF that extract_map_features.py actually reads.

Usage:
    python pdf_to_raster.py input.pdf output.png --dpi 400
    python pdf_to_raster.py input.pdf output.tif --dpi 400 --page 0
"""

import argparse
import sys

import fitz  # PyMuPDF


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("input_pdf")
    ap.add_argument("output_image", help="Output path, e.g. raw_map.png or raw_map.tif")
    ap.add_argument("--dpi", type=int, default=400,
                     help="Render resolution. 300-400 is usually enough to click GCPs "
                          "precisely; go higher (600+) only if the source PDF itself is "
                          "very high resolution and you need to click small features.")
    ap.add_argument("--page", type=int, default=0, help="0-indexed page number (default 0)")
    args = ap.parse_args()

    doc = fitz.open(args.input_pdf)
    if args.page >= len(doc):
        print(f"ERROR: PDF only has {len(doc)} page(s); --page {args.page} is out of range.",
              file=sys.stderr)
        sys.exit(1)

    page = doc[args.page]
    zoom = args.dpi / 72.0  # PDF points are 1/72 inch
    mat = fitz.Matrix(zoom, zoom)
    pix = page.get_pixmap(matrix=mat)
    pix.save(args.output_image)

    print(f"Rendered page {args.page} at {args.dpi} DPI -> {args.output_image} "
          f"({pix.width}x{pix.height} px)")
    print("Next step: open this image in QGIS's Georeferencer (Layer > Georeferencer), "
          "add ground control points, and export a georeferenced GeoTIFF. "
          "See docs/GEOREFERENCING_IN_QGIS.md.")


if __name__ == "__main__":
    main()
