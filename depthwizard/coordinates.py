"""Pixel-edge coordinates transformed through the source CRS, always longitude first."""
from pyproj import CRS, Transformer


def lonlat(meta, points):
    if not meta.get("crs") or not meta.get("transform"):
        raise ValueError("Scene has no geographic coordinate transform")
    a, b, c, d, e, f = map(float, meta["transform"][:6])
    w, h = int(meta["src_w"]), int(meta["src_h"])
    transformer = Transformer.from_crs(CRS.from_user_input(meta["crs"]).to_2d(),
                                       "EPSG:4326", always_xy=True, allow_ballpark=False)
    coords = []
    for u, v in points:
        if not 0 <= u <= 1 or not 0 <= v <= 1:
            raise ValueError("Coordinates must lie in the normalized image extent")
        x, y = a * u * w + b * v * h + c, d * u * w + e * v * h + f
        lon, lat = transformer.transform(x, y, errcheck=True)
        coords.append([float(lon), float(lat)])
    return coords
