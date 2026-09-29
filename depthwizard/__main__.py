"""Command line: python -m depthwizard IMAGE [options]"""
import argparse
import json

from .pipeline import run


def main(argv=None):
    p = argparse.ArgumentParser(prog="depthwizard",
                                description="Single-view DSM estimation from RGB satellite imagery")
    p.add_argument("image", help="PNG / JPG / TIFF / GeoTIFF")
    p.add_argument("-o", "--out", default="output", help="output folder")
    p.add_argument("--dem", help="low-res DEM GeoTIFF (SRTM 30 m, CartoDEM) for absolute scale")
    p.add_argument("--fetch-dem", action="store_true",
                   help="download a DEM for the footprint (needs OPENTOPO_API_KEY)")
    p.add_argument("--dem-source", default="COP30", choices=["COP30", "SRTMGL1"],
                   help="DEM for --fetch-dem: Copernicus GLO-30 or SRTM GL1")
    p.add_argument("--gcp", help="CSV of ground control points (x,y,z or easting,northing,z)")
    p.add_argument("--ref", help="reference DSM/LiDAR raster for validation")
    p.add_argument("--model", default="small",
                   help="small | base | large | HF id | local fine-tuned checkpoint")
    p.add_argument("--scene", default="auto", choices=["auto", "urban", "sparse", "forest", "hilly"])
    p.add_argument("--gsd", type=float, default=1.0, help="assumed m/pixel for non-georeferenced input")
    p.add_argument("--device", help="cuda | mps | cpu")
    p.add_argument("--no-fallback", action="store_true",
                   help="fail instead of using the heuristic when the model is unavailable")
    a = p.parse_args(argv)
    meta = run(a.image, a.out, dem=a.dem, gcp=a.gcp, reference=a.ref, model=a.model,
               scene=a.scene, fetch_dem=a.fetch_dem, dem_source=a.dem_source, assumed_gsd_m=a.gsd,
               allow_fallback=not a.no_fallback, device=a.device)
    if "metrics" in meta:
        print(json.dumps(meta["metrics"], indent=2))


if __name__ == "__main__":
    main()
