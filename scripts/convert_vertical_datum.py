"""Convert one absolute elevation raster between explicit vertical datums, offline."""
from __future__ import annotations

import argparse
import json
from pathlib import Path
import sys

from pyproj.exceptions import ProjError
from rasterio.errors import RasterioError

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from depthwizard.vertical_datum import VerticalDatumError, convert_raster_vertical_datum, normalize_datum


def _datum(value: str) -> str:
    try:
        return normalize_datum(value)
    except VerticalDatumError as exc:
        raise argparse.ArgumentTypeError(str(exc)) from exc


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("input", type=Path, help="Single-band absolute elevation raster in metres")
    parser.add_argument("output", type=Path, help="New GeoTIFF path; existing files are never overwritten")
    parser.add_argument("--source-datum", required=True, type=_datum,
                        help="ellipsoidal, EGM96/EPSG:5773, or EGM2008/EPSG:3855")
    parser.add_argument("--target-datum", required=True, type=_datum,
                        help="ellipsoidal, EGM96/EPSG:5773, or EGM2008/EPSG:3855")
    parser.add_argument("--height-kind", required=True, choices=["absolute"],
                        help="Attest that the band contains absolute metre elevations, rather than differences/uncertainty")
    parser.add_argument("--attest-source-datum", action="store_true",
                        help="Confirm source absolute metre heights and datum when vertical metadata is missing; cannot override contradictions")
    parser.add_argument("--grid-dir", type=Path, help="Existing local directory containing the required PROJ grids; no downloads")
    parser.add_argument("--block-size", type=int, default=512, help="Conversion block side in pixels (16..4096; default 512)")
    args = parser.parse_args()
    try:
        report = convert_raster_vertical_datum(
            args.input, args.output, source_datum=args.source_datum, target_datum=args.target_datum,
            height_kind=args.height_kind, attest_source_datum=args.attest_source_datum,
            grid_dir=args.grid_dir, block_size=args.block_size,
        )
    except (VerticalDatumError, ProjError, RasterioError, OSError, ValueError) as exc:
        print(f"Vertical datum conversion failed: {exc}", file=sys.stderr)
        return 2
    print(json.dumps(report, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
