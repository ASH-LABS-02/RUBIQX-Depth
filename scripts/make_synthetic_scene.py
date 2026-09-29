"""Generate a synthetic, fully-known test scene so the whole pipeline and the
metrics can be verified without internet access.

Outputs (in samples/synthetic/):
  scene_rgb.tif    georeferenced RGB (UTM 43N, 0.5 m GSD)
  scene_rgb.png    same image without georeferencing
  truth_dsm.tif    ground-truth DSM (m)
  srtm_like.tif    30 m DEM (blurred, noisy, coarse) – stands in for SRTM
  gcps.csv         5 ground control points (pixel x,y,z)
"""
from pathlib import Path

import numpy as np
import rasterio
from PIL import Image
from rasterio.transform import from_origin
from scipy import ndimage

rng = np.random.default_rng(7)
N, GSD = 1024, 0.5
OUT = Path(__file__).resolve().parents[1] / "samples" / "synthetic"
OUT.mkdir(parents=True, exist_ok=True)
CRS = "EPSG:32643"
X0, Y0 = 500_000.0, 1_220_000.0   # arbitrary UTM 43N origin (South India)
T = from_origin(X0, Y0, GSD, GSD)

yy, xx = np.mgrid[0:N, 0:N] / N
# terrain: a ridge + rolling hills, 380–420 m
terrain = 380 + 25 * np.exp(-((xx - 0.8) ** 2 + (yy - 0.25) ** 2) / 0.05) \
    + 6 * np.sin(4 * xx + 1) * np.cos(3 * yy) + 4 * yy
struct = np.zeros((N, N))
kind = np.zeros((N, N), np.uint8)   # 0 ground, 1 building, 2 tree, 3 road

# roads
for c in (300, 650):
    kind[:, c - 10:c + 10] = 3
    kind[c - 10:c + 10, :] = 3
# buildings in the west half (urban)
for _ in range(45):
    w, h = rng.integers(20, 60, 2)
    x, y = rng.integers(20, 560 - w), rng.integers(20, N - h - 20)
    if (kind[y:y + h, x:x + w] != 0).any():
        continue
    height = rng.choice([4, 7, 10, 14, 22, 30], p=[.2, .25, .2, .15, .12, .08])
    struct[y:y + h, x:x + w] = height
    kind[y:y + h, x:x + w] = 1
# forest patch south-east
trees = np.zeros((N, N))
for _ in range(900):
    x, y = rng.integers(680, N - 10), rng.integers(560, N - 10)
    r = rng.integers(4, 10)
    hgt = rng.uniform(10, 22)
    sl = np.s_[max(0, y - r):y + r, max(0, x - r):x + r]
    yy2, xx2 = np.mgrid[sl]
    d = np.hypot(yy2 - y, xx2 - x) / r
    crown = hgt * np.clip(1 - d ** 2, 0, None)
    trees[sl] = np.maximum(trees[sl], crown)
tm = (trees > 1) & (kind == 0)
struct[tm] = trees[tm]
kind[tm] = 2
truth = (terrain + struct).astype(np.float32)

# ---- render RGB: albedo * hillshade, with cast shadows
alb = np.zeros((N, N, 3))
alb[:] = (0.55, 0.50, 0.40)                                   # bare soil
alb += rng.normal(0, 0.03, (N, N, 1))
alb[kind == 3] = (0.35, 0.35, 0.37)
roof_cols = np.array([(0.75, 0.74, 0.72), (0.62, 0.30, 0.25), (0.45, 0.47, 0.52)])
lab, n = ndimage.label(kind == 1)
cols = roof_cols[rng.integers(0, 3, n + 1)]
alb[kind == 1] = cols[lab[kind == 1]]
alb[kind == 2] = (0.18, 0.36, 0.15)
alb[kind == 2] *= (0.8 + 0.4 * (trees[kind == 2] / 22))[:, None]
# grass in sparse north-east
grass = (kind == 0) & (xx > 0.6) & (yy < 0.5)
alb[grass] = (0.42, 0.52, 0.30)

gy, gx = np.gradient(truth, GSD)
az, el = np.radians(135), np.radians(50)
nx, ny, nz = -gx, -gy, np.ones_like(gx)
nrm = np.sqrt(nx ** 2 + ny ** 2 + nz ** 2)
shade = np.clip((nx * np.sin(az) * np.cos(el) - ny * np.cos(az) * np.cos(el) + nz * np.sin(el)) / nrm, 0, 1)
# cast shadows: march towards the sun
shadow = np.zeros((N, N), bool)
dx, dy = np.sin(az), -np.cos(az)
tan_el = np.tan(el)
for step in range(1, 80):
    sx, sy = int(round(-dx * step)), int(round(-dy * step))
    shifted = np.roll(np.roll(truth, sy, 0), sx, 1)
    shadow |= shifted - truth > step * GSD * tan_el
light = 0.35 + 0.75 * shade
light[shadow] *= 0.45
rgb = np.clip(alb * light[..., None], 0, 1)
rgb = ndimage.gaussian_filter(rgb, (0.7, 0.7, 0))            # sensor PSF
rgb8 = (rgb * 255).astype(np.uint8)

prof = dict(driver="GTiff", width=N, height=N, crs=CRS, transform=T)
with rasterio.open(OUT / "scene_rgb.tif", "w", count=3, dtype="uint8", **prof) as d:
    for i in range(3):
        d.write(rgb8[..., i], i + 1)
Image.fromarray(rgb8).save(OUT / "scene_rgb.png")
with rasterio.open(OUT / "truth_dsm.tif", "w", count=1, dtype="float32", **prof) as d:
    d.write(truth, 1)

# ---- SRTM-like: radar sees partly into canopy / buildings, 30 m, ±3 m noise
k = int(30 / GSD)
pad = 2 * k
big = np.pad(terrain + 0.4 * struct, pad, mode="edge")
coarse = ndimage.gaussian_filter(big, k / 2)[k // 2::k, k // 2::k]
coarse = coarse + rng.normal(0, 2.5, coarse.shape)
Ts = from_origin(X0 - pad * GSD, Y0 + pad * GSD, 30, 30)
with rasterio.open(OUT / "srtm_like.tif", "w", driver="GTiff", width=coarse.shape[1],
                   height=coarse.shape[0], count=1, dtype="float32", crs=CRS, transform=Ts) as d:
    d.write(coarse.astype(np.float32), 1)

# ---- GCPs (surveyed points incl. two rooftops)
pts = [(100, 100), (900, 150), (500, 900), (200, 700), (850, 850)]
roofs = np.argwhere(kind == 1)
pts += [tuple(roofs[len(roofs) // 3][::-1]), tuple(roofs[2 * len(roofs) // 3][::-1])]
with open(OUT / "gcps.csv", "w") as f:
    f.write("x,y,z\n")
    for x, y in pts:
        f.write(f"{x},{y},{truth[y, x]:.2f}\n")
print("wrote", OUT, "truth range", truth.min(), truth.max())
