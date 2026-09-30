import numpy as np

from depthwizard.analysis import change_detection, landslide_susceptibility, solar_positions
from depthwizard.buildings import extract_buildings
from depthwizard.shadows import cast_shadows


def test_shadow_length_matches_geometry():
    h = np.zeros((200, 200), np.float32); h[90:110, 90:110] = 10.0
    sh = cast_shadows(h, 1.0, azimuth_deg=180, elevation_deg=45)   # sun due south
    # a 10 m block at 45° casts a ~10 m shadow to the north (smaller row index)
    col = sh[:, 100]
    north = np.flatnonzero(col[:90])
    assert north.size and 7 <= 90 - north.min() <= 12


def test_landslide_index_higher_on_steep_slopes():
    x = np.tile(np.arange(200, dtype=np.float32), (200, 1))
    dtm = np.where(x < 100, 0.0, (x - 100) * 1.0).astype(np.float32)  # 45° slope on the right half
    idx, stats = landslide_susceptibility(dtm, None, 1.0)
    assert idx[:, 150].mean() > idx[:, 40].mean() + 0.2
    assert abs(sum(stats["fractions"].values()) - 1) < 1e-6


def test_change_detection_flags_collapsed_building():
    pre = np.zeros((100, 100), np.float32); pre[20:40, 20:40] = 12; pre[60:80, 60:80] = 9
    post = pre.copy(); post[20:40, 20:40] = 1
    labels = np.zeros((100, 100), np.int32); labels[20:40, 20:40] = 1; labels[60:80, 60:80] = 2
    d, st = change_detection(pre, post, 1.0, labels)
    assert st["building_ids_height_loss"] == [1] and st["volume_loss_m3"] > 3000


def test_buildings_exclude_vegetation():
    dsm = np.zeros((120, 120), np.float32); dsm[10:40, 10:40] = 8; dsm[70:100, 70:100] = 8
    rgb = np.full((120, 120, 3), 120, np.uint8); rgb[70:100, 70:100] = (40, 140, 40)  # tree canopy
    out = extract_buildings(dsm, dtm=np.zeros_like(dsm), gsd=1.0, world_w=120, world_h=120, rgb=rgb)
    assert out["count"] == 1


def test_solar_positions_reasonable():
    pos = solar_positions(22.0)
    assert pos and all(0 < el < 90 for _, el, _ in pos)


def test_water_mask_finds_large_smooth_blue_area_only():
    from depthwizard.analysis import water_mask
    rng = np.random.default_rng(0)
    rgb = rng.integers(60, 200, (200, 200, 3)).astype(np.uint8)       # textured land
    rgb[50:150, 20:180] = (40, 70, 95)                                 # 160 x 100 px lake
    rgb[5:15, 5:15] = (40, 70, 95)                                     # tiny pond: ignored
    m = water_mask(rgb, 0.5)
    assert m[100, 100] and not m[10, 10] and m.mean() < 0.5
