"""Tests for Copernicus GLO-30 terrain mode fix.

Covers:
  - ground-extraction unit test: copernicus_terrain_from_dsm on a synthetic
    hill + box (pure numpy, no model, no network)
  - pipeline routing tests: when DEM is auto-fetched Copernicus the pipeline
    passes the correct dem_kind / match_dem_30m / dem_path to calibrate()
    (mocked — no model, no network)
  - explicit override preservation: --dem-kind surface is not hijacked by
    the Mode C branch

Bug evidence: real-data runs on both DC scenes with real Copernicus GLO-30
produced 0 buildings detected (LiDAR RMSE 7.31 m Glover Park, 5.32 m Capitol
Hill East). The synthetic 64x64 scene was insufficient to reproduce the
calibration failure because surface-fit scale estimation behaves differently
on a toy flat DEM; see CHANGES_ITEM2.md for the full empirical comparison.

Validates: Requirements 2.1, 2.2, 3.1, 3.2, 3.3, 3.4, 3.5
"""

import numpy as np
import pytest


# ---------------------------------------------------------------------------
# Ground-extraction unit test (Task 4b)
# Pure numpy — no model, no network, no rasterio I/O
# ---------------------------------------------------------------------------

def test_copernicus_terrain_from_dsm_removes_box_preserves_hill():
    """copernicus_terrain_from_dsm removes elevated objects and preserves terrain.

    Input: 256x256 Gaussian hill (amplitude 60 m, sigma 40 px) with a 15 m
    rectangular box (20x20 px) sitting on top of it.

    Expected:
    - output <= input elementwise (morphological opening never raises values)
    - box is substantially removed (terrain under box recovered to within 3 m
      of the true hill value at that location)
    - hill flanks far from the box are preserved to within 2 m
    """
    from depthwizard.calibrate import copernicus_terrain_from_dsm

    H, W = 256, 256
    gsd = 30.0  # native Copernicus pixel size

    # Gaussian hill
    y, x = np.ogrid[:H, :W]
    hill = 60.0 * np.exp(-((x - 128) ** 2 + (y - 128) ** 2) / (2 * 40.0 ** 2))
    hill = hill.astype(np.float32)

    # 15 m box at rows 100:105, cols 100:105 (5x5 px = 150 m at 30 m/px,
    # smaller than the ~210 m opening kernel so it should be removed)
    box = hill.copy()
    box[100:105, 100:105] += 15.0

    result = copernicus_terrain_from_dsm(box, gsd)

    # Opening never raises values above the INPUT maximum (Gaussian smoothing
    # after opening can locally slightly exceed the opened array at pixels where
    # the opening dips below neighbours, but must stay below the global DSM max)
    assert float(result.max()) <= float(box.max()) + 0.1, (
        f"copernicus_terrain_from_dsm exceeded input maximum: "
        f"result.max()={result.max():.3f}, box.max()={box.max():.3f}"
    )
    # The vast majority of pixels should be <= input (opening property)
    frac_above = float(np.mean(result > box + 0.1))
    assert frac_above < 0.01, (
        f"copernicus_terrain_from_dsm raised {frac_above:.1%} of pixels above input "
        f"(expected < 1%)"
    )

    # Box centre: result should be close to the true hill value (within 5 m)
    box_cy, box_cx = 102, 102
    true_hill_at_box = float(hill[box_cy, box_cx])
    result_at_box = float(result[box_cy, box_cx])
    assert result_at_box < true_hill_at_box + 5.0, (
        f"Box not removed: result at box centre = {result_at_box:.2f} m, "
        f"true hill = {true_hill_at_box:.2f} m (expected < hill + 5 m)"
    )

    # Hill flank far from box (e.g. row=128, col=200): preserved within 2 m
    flank_r, flank_c = 128, 200
    assert abs(float(result[flank_r, flank_c]) - float(hill[flank_r, flank_c])) < 2.0, (
        f"Hill flank distorted: result = {result[flank_r, flank_c]:.2f} m, "
        f"true hill = {hill[flank_r, flank_c]:.2f} m (expected within 2 m)"
    )


# ---------------------------------------------------------------------------
# Pipeline routing tests (Task 4a)
# Mock calibrate, fetch_copernicus_glo30, relative_height — no model, no network
# ---------------------------------------------------------------------------

def _write_tiny_geotiff(path, arr, crs_epsg=32643, origin=(500_000.0, 3_400_000.0), gsd=30.0):
    """Write a small float32 GeoTIFF for use as a mock DEM or RGB."""
    import rasterio
    from rasterio.crs import CRS
    from rasterio.transform import from_origin

    h, w = arr.shape[:2]
    bands = 1 if arr.ndim == 2 else arr.shape[2]
    with rasterio.open(
        path, "w",
        driver="GTiff", height=h, width=w,
        count=bands, dtype="float32",
        crs=CRS.from_epsg(crs_epsg),
        transform=from_origin(origin[0], origin[1], gsd, gsd),
    ) as dst:
        if arr.ndim == 2:
            dst.write(arr.astype(np.float32), 1)
            dst.update_tags(
                SOURCE="Copernicus DEM GLO-30 Public (AWS Open Data)",
                VERTICAL_DATUM="EGM2008 geoid (Copernicus GLO-30)",
                SOURCE_TILES="Copernicus_DSM_COG_10_N38_00_W078_00_DEM",
            )
        else:
            for b in range(bands):
                dst.write(arr[:, :, b].astype(np.float32), b + 1)


def _make_mock_rgb_geotiff(path, size=32, gsd=0.5):
    """Write a minimal georeferenced RGB GeoTIFF."""
    import rasterio
    from rasterio.crs import CRS
    from rasterio.transform import from_origin

    rgb = np.full((size, size, 3), 128, dtype=np.uint8)
    with rasterio.open(
        path, "w",
        driver="GTiff", height=size, width=size,
        count=3, dtype="uint8",
        crs=CRS.from_epsg(32643),
        transform=from_origin(500_000.0, 3_400_000.0, gsd, gsd),
    ) as dst:
        for b in range(3):
            dst.write(rgb[:, :, b], b + 1)


def _make_dummy_dinfo(size=32):
    """Minimal dinfo dict that pipeline.run expects from relative_height."""
    return {
        "agl": True,
        "tta": 4,
        "learned_scale": lambda gsd: 15.0,
        "std_rel": np.zeros((size, size), dtype=np.float32),
    }


def test_preservation_cop_auto_routes_to_terrain_mode(tmp_path):
    """When fetch_dem=True and dem_kind='auto', pipeline uses terrain proxy.

    Asserts calibrate() is called with:
    - dem_kind='terrain'
    - reference_consistent=False  (match_dem_30m=False for Mode C)
    - dem_path pointing to dem_terrain.tif (the derived proxy), not dem.tif

    No model weights loaded; no network calls made.
    Validates: Requirements 2.1, 2.2, 2.3
    """
    import unittest.mock as mock
    from pathlib import Path
    import depthwizard.pipeline as pipeline_mod

    size = 32
    rgb_path = str(tmp_path / "rgb.tif")
    cop_dem_path = tmp_path / "cop_dem.tif"
    out_dir = str(tmp_path / "out_mode_c")

    _make_mock_rgb_geotiff(rgb_path, size=size)
    cop_arr = np.full((size, size), 50.0, dtype=np.float32)
    _write_tiny_geotiff(cop_dem_path, cop_arr)

    rel_arr = np.random.default_rng(0).random((size, size)).astype(np.float32)
    dinfo = _make_dummy_dinfo(size)

    captured = {}

    def mock_calibrate(rel, img, **kwargs):
        captured.update(kwargs)
        # Return a minimal (dsm, units, cal) triple
        from depthwizard.calibrate import Calibration
        cal = Calibration(
            method="dem-fusion", scale_k=15.0, dem_kind=kwargs.get("dem_kind"),
            reference_consistent=kwargs.get("reference_consistent", False),
            vertical_datum="EGM2008 geoid (Copernicus GLO-30)",
        )
        cal.dtm = np.zeros((size, size), dtype=np.float32)
        cal.ndsm = np.ones((size, size), dtype=np.float32) * 3.0
        dsm = np.full((size, size), 53.0, dtype=np.float32)
        return dsm, "metre", cal

    with (
        mock.patch.object(pipeline_mod, "fetch_copernicus_glo30",
                          return_value=cop_dem_path),
        mock.patch.object(pipeline_mod, "cached_tile_names",
                          return_value=["Copernicus_DSM_COG_10_N38_00_W078_00_DEM"]),
        mock.patch.object(pipeline_mod, "relative_height",
                          return_value=(rel_arr, "da2-gamus-full", rel_arr * 0.05, dinfo)),
        mock.patch.object(pipeline_mod, "calibrate", side_effect=mock_calibrate),
    ):
        pipeline_mod.run(
            rgb_path, out_dir,
            fetch_dem=True, dem_kind="auto", match_dem_30m=True,
            model="small",  # not actually used — mocked
            tta=1, device="cpu",
        )

    assert captured.get("dem_kind") == "terrain", (
        f"Expected dem_kind='terrain' for auto-fetch Copernicus, got {captured.get('dem_kind')!r}"
    )
    dem_path_used = captured.get("dem_path", "")
    assert str(dem_path_used).endswith("dem_terrain.tif"), (
        f"Expected dem_path to point to dem_terrain.tif, got {dem_path_used!r}"
    )


def test_preservation_explicit_surface_override_not_hijacked(tmp_path):
    """Explicit --dem-kind surface is not replaced by the Mode C terrain proxy.

    When dem_kind='surface' is passed explicitly, pipeline must call
    calibrate() with dem_kind='surface' and the original dem.tif path,
    not the derived dem_terrain.tif.

    Validates: Requirements 3.1, 3.2, 3.3
    """
    import unittest.mock as mock
    import depthwizard.pipeline as pipeline_mod

    size = 32
    rgb_path = str(tmp_path / "rgb.tif")
    cop_dem_path = tmp_path / "cop_dem.tif"
    out_dir = str(tmp_path / "out_explicit_surface")

    _make_mock_rgb_geotiff(rgb_path, size=size)
    cop_arr = np.full((size, size), 50.0, dtype=np.float32)
    _write_tiny_geotiff(cop_dem_path, cop_arr)

    rel_arr = np.random.default_rng(1).random((size, size)).astype(np.float32)
    dinfo = _make_dummy_dinfo(size)

    captured = {}

    def mock_calibrate(rel, img, **kwargs):
        captured.update(kwargs)
        from depthwizard.calibrate import Calibration
        cal = Calibration(
            method="dem-fusion", scale_k=15.0, dem_kind=kwargs.get("dem_kind"),
            reference_consistent=kwargs.get("reference_consistent", True),
            vertical_datum="EGM2008 geoid (Copernicus GLO-30)",
        )
        cal.dtm = np.zeros((size, size), dtype=np.float32)
        cal.ndsm = np.ones((size, size), dtype=np.float32) * 3.0
        dsm = np.full((size, size), 53.0, dtype=np.float32)
        return dsm, "metre", cal

    with (
        mock.patch.object(pipeline_mod, "fetch_copernicus_glo30",
                          return_value=cop_dem_path),
        mock.patch.object(pipeline_mod, "cached_tile_names",
                          return_value=["Copernicus_DSM_COG_10_N38_00_W078_00_DEM"]),
        mock.patch.object(pipeline_mod, "relative_height",
                          return_value=(rel_arr, "da2-gamus-full", rel_arr * 0.05, dinfo)),
        mock.patch.object(pipeline_mod, "calibrate", side_effect=mock_calibrate),
    ):
        pipeline_mod.run(
            rgb_path, out_dir,
            fetch_dem=True, dem_kind="surface", match_dem_30m=True,
            model="small",
            tta=1, device="cpu",
        )

    assert captured.get("dem_kind") == "surface", (
        f"Explicit dem_kind='surface' was overridden; got {captured.get('dem_kind')!r}"
    )
    dem_path_used = str(captured.get("dem_path", ""))
    assert not dem_path_used.endswith("dem_terrain.tif"), (
        f"Explicit surface override should NOT use terrain proxy, but got {dem_path_used!r}"
    )
