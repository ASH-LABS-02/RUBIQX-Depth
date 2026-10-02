"""Download a cloud-free Sentinel-2 true-colour (10 m) crop of a city – no account needed.

Uses the public Earth Search STAC API (Element 84, AWS Open Data) and reads only
the needed window from the Cloud-Optimised GeoTIFF.

  python scripts/fetch_sentinel2_city.py --city bengaluru --km 5 --out D:/DepthWizard/india
"""
from __future__ import annotations

import argparse
import json
import math
import urllib.request
from pathlib import Path

import rasterio
from rasterio.warp import transform_bounds
from rasterio.windows import from_bounds

CITIES = {  # lat, lon of a dense central area
    "bengaluru": (12.9716, 77.5946), "delhi": (28.6315, 77.2167), "mumbai": (19.0176, 72.8562),
    "chennai": (13.0604, 80.2496), "hyderabad": (17.3850, 78.4867), "kolkata": (22.5726, 88.3639),
    "ahmedabad": (23.0225, 72.5714),
}
STAC = "https://earth-search.aws.element84.com/v1/search"


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--city", default="bengaluru", choices=sorted(CITIES))
    ap.add_argument("--lat", type=float); ap.add_argument("--lon", type=float)
    ap.add_argument("--km", type=float, default=5.0)
    ap.add_argument("--max-cloud", type=float, default=5.0)
    ap.add_argument("--out", type=Path, default=Path("D:/DepthWizard/india"))
    a = ap.parse_args()
    lat, lon = (a.lat, a.lon) if a.lat is not None else CITIES[a.city]
    dlat = a.km / 2 / 111.32
    dlon = a.km / 2 / (111.32 * math.cos(math.radians(lat)))
    bbox = [lon - dlon, lat - dlat, lon + dlon, lat + dlat]
    body = {"collections": ["sentinel-2-l2a"], "bbox": bbox, "limit": 10,
            "query": {"eo:cloud_cover": {"lt": a.max_cloud}},
            "sortby": [{"field": "properties.datetime", "direction": "desc"}]}
    req = urllib.request.Request(STAC, data=json.dumps(body).encode(), headers={"Content-Type": "application/json"})
    items = json.load(urllib.request.urlopen(req, timeout=60))["features"]
    if not items:
        raise SystemExit("no scene under the cloud limit – try --max-cloud 15")
    for it in items:
        href = it["assets"].get("visual", {}).get("href")
        if not href:
            continue
        with rasterio.open(href) as src:
            b = transform_bounds("EPSG:4326", src.crs, *bbox)
            win = from_bounds(*b, transform=src.transform).round_offsets().round_lengths()
            data = src.read(window=win, boundless=True, fill_value=0)
            if (data[0] == 0).mean() > 0.05:      # crop at tile edge – try the next scene
                continue
            prof = src.profile.copy()
            prof.update(width=data.shape[2], height=data.shape[1], transform=src.window_transform(win),
                        driver="GTiff", compress="deflate", tiled=False)
            prof.pop("blockxsize", None); prof.pop("blockysize", None)
        a.out.mkdir(parents=True, exist_ok=True)
        date = it["properties"]["datetime"][:10]
        dst = a.out / f"{a.city}_sentinel2_{date}_10m.tif"
        with rasterio.open(dst, "w", **prof) as d:
            d.write(data)
        print(f"{it['id']}  cloud {it['properties']['eo:cloud_cover']:.1f} %  -> {dst}  ({data.shape[2]}x{data.shape[1]} px)")
        return
    raise SystemExit("all candidate scenes cut the area at a tile edge – move --lat/--lon slightly")


if __name__ == "__main__":
    main()
