# Implementation Plan

## Overview

Fix the Copernicus GLO-30 auto-calibration bug where `dem_kind="surface"` suppresses nDSM
heights and zeroes out building detections. The bug is confirmed by real-data evidence (0 buildings
detected on both DC scenes with real Copernicus). Fix strategy: empirically compare three modes on
real data, route the auto-fetch default to the best mode, and replace the deleted synthetic
exploration test with a cheap routing test and a ground-extraction unit test.

## Tasks

- [ ] 1. Delete synthetic exploration test and run real-data mode comparison
  - Remove `test_surface_mode_suppresses_ndsm_bug` from `tests/test_cop_terrain_mode.py`
    (the bug is confirmed by real-data 0-building results; the 64×64 synthetic scene cannot
    reproduce the calibration failure because surface-fit scale estimation behaves differently
    on a toy flat DEM than on real 30 m Copernicus terrain)
  - **Write** `scripts/compare_cop_modes.py` that runs the three modes below on both DC scenes
    and prints/writes the comparison table.  The script must NOT touch any source file.
    Use `D:\DepthWizard\venv\Scripts\python.exe` throughout.
  - **Inputs** (cached tiles are fine — no network required):
    - Glover Park: `samples/dc_lidar/glover_park/rgb.tif`,
      reference `samples/dc_lidar/glover_park/lidar_dsm_2024.tif`
    - Capitol Hill East: `samples/dc_lidar/capitol_hill_east/rgb.tif`,
      reference `samples/dc_lidar/capitol_hill_east/lidar_dsm_2024.tif`
  - **Three modes** (pass `fetch_dem=True`, let pipeline auto-fetch cached Copernicus):
    - Mode A — surface + 30 m consistency (current default):
      `dem_kind="surface"`, `match_dem_30m=True`
    - Mode B — surface, consistency off:
      `dem_kind="surface"`, `match_dem_30m=False`
    - Mode C — ground-from-Copernicus (terrain proxy):
      derive approximate bare earth from the fetched Copernicus DSM via morphological opening
      (~200 m kernel) + Gaussian smoothing (see Task 2 for the helper), then call pipeline with
      `dem_kind="terrain"`, `match_dem_30m=False`, passing the derived terrain GeoTIFF as `dem`
      (explicit path, `fetch_dem=False` so the pipeline uses the supplied terrain file)
  - **Checkpoint / model**: `D:\DepthWizard\checkpoints\da2-gamus-full`, 4 TTA passes, GPU
  - **Collect from each run** (from `metrics.json` / `meta.json`):
    - `absolute.rmse`, `absolute.mae`, `absolute.r`, `absolute.bias`
    - bias-corrected RMSE: `sqrt(rmse² – bias²)` (computed in script)
    - `buildings_count` from `meta.json`
    - per-building RMSE from `metrics.json["buildings"]["rmse"]` if present
  - **Print table** (and write to `out/cop_modes/comparison_table.csv`):
    ```
    mode | scene         | RMSE  | MAE   | r    | bias  | RMSE_debiased | buildings | per_bldg_RMSE
    A    | glover_park   | ...   | ...   | ...  | ...   | ...           | ...       | ...
    A    | capitol_hill  | ...   | ...   | ...  | ...   | ...           | ...       | ...
    B    | glover_park   | ...   | ...   | ...  | ...   | ...           | ...       | ...
    ...
    ```
  - Reference targets: DTM-baseline Glover Park RMSE 4.30 m / 145 buildings,
    Capitol Hill 2.88 m / 102 buildings
  - Run: `D:\DepthWizard\venv\Scripts\python.exe scripts/compare_cop_modes.py`
  - _Requirements: 1.1, 1.2, 1.3, 1.4, 2.4_
  - Commit: `exp: Mode A/B/C empirical comparison on DC scenes`

- [ ] 2. Implement `copernicus_terrain_from_dsm` helper in `calibrate.py`
  - Add after the existing `_surface_fit` function:
    ```python
    def copernicus_terrain_from_dsm(dsm_arr: np.ndarray, gsd: float) -> np.ndarray:
        """Approximate bare-earth terrain from a Copernicus DSM array.

        Morphological opening with a kernel spanning ~200 m removes building/canopy
        tops; Gaussian smoothing fills narrow gaps left by streets and small clearings.
        Identical in spirit to metrics._ground() but tuned for a coarser (≈30 m) input.
        """
        kernel_px = max(3, int(round(200.0 / max(gsd, 1.0))))
        opened = ndimage.grey_opening(
            np.nan_to_num(dsm_arr, nan=float(np.nanmin(dsm_arr))),
            size=(kernel_px, kernel_px))
        return ndimage.gaussian_filter(
            opened, max(1.0, 50.0 / max(gsd, 1.0))).astype(np.float32)
    ```
  - `gsd` is the native DEM pixel size (~30 m for Copernicus), not the image GSD
  - kernel_px ≈ 200/30 ≈ 7 px → spans ~210 m, enough to remove building footprints
  - Gaussian sigma ≈ 50/30 ≈ 1.7 px — light smoothing of opening artifacts
  - No changes to `calibrate()` itself; no changes to `_surface_fit`
  - _Requirements: 2.1, 2.2_
  - Commit: `feat(calibrate): add copernicus_terrain_from_dsm helper`

- [ ] 3. Route auto-fetched Copernicus default to the best mode in `pipeline.py`
  - **Decision rule** (apply after reviewing Task 1 table):
    - If Mode C (terrain proxy) is within ~0.5 m RMSE of DTM baseline AND detects >100/80
      buildings → use Mode C as the new auto default (see code below).
    - If Mode B is better than Mode C but within threshold → use Mode B.
    - If no mode passes → keep Mode A code unchanged, set `fetch_dem` default back to `False`
      in `__main__.py` and document in `CHANGES_ITEM2.md`.
  - **Mode C implementation** — replace the single `effective_kind` line in `pipeline.py`:
    ```python
    # OLD (one line):
    effective_kind = "surface" if dem and effective_source == "COP30" and dem_kind == "auto" else dem_kind

    # NEW (Mode C branch):
    effective_kind = dem_kind  # explicit user override always respected
    cop_terrain_dem = None     # path to derived terrain proxy; None for all non-Mode-C paths
    if dem and dem_origin == "copernicus-glo30-auto" and dem_kind == "auto":
        from .calibrate import copernicus_terrain_from_dsm
        import rasterio as _rio
        cop_terrain_path = out / "dem_terrain.tif"
        with _rio.open(dem) as _src:
            _arr  = _src.read(1).astype(np.float32)
            _res  = abs(_src.transform.a)
            _prof = _src.profile.copy()
        _prof.update(dtype="float32", nodata=np.nan)
        _terrain = copernicus_terrain_from_dsm(_arr, _res)
        with _rio.open(cop_terrain_path, "w", **_prof) as _dst:
            _dst.write(_terrain, 1)
            _dst.update_tags(
                SOURCE="Copernicus GLO-30 terrain proxy (morphological opening)",
                VERTICAL_DATUM="EGM2008 geoid (Copernicus GLO-30)")
        cop_terrain_dem = str(cop_terrain_path)
        effective_kind  = "terrain"
        log("  Mode C: derived terrain proxy from Copernicus DSM (morphological opening)")
    ```
  - Also update the `do_calibration` call to use `cop_terrain_dem` when set:
    ```python
    cal_dem = cop_terrain_dem if cop_terrain_dem else dem
    dsm, units, cal = do_calibration(cal_dem, effective_kind, effective_source, datum)
    ```
  - Keep the `vs_copernicus_30m` comparison using the original `dem` variable (not
    `cop_terrain_dem`) — consistency score stays against the real Copernicus DSM
  - If Mode B is chosen instead: set `reference_consistent=False` when `dem_origin ==
    "copernicus-glo30-auto"` inside `do_calibration`; no terrain proxy needed
  - If no mode passes: leave pipeline.py unchanged; set `default=False` on `--fetch-dem` in
    `__main__.py`
  - _Requirements: 2.1, 2.2, 2.3, 3.1, 3.2, 3.3, 3.4, 3.5_
  - Commit: `fix(pipeline): route Copernicus auto-fetch to best mode (Mode C / B / disable)`

- [ ] 4. Write routing test and ground-extraction unit test
  - **4a. Routing test** — add to `tests/test_cop_terrain_mode.py`; no model, no network:
    - Mock `depthwizard.pipeline.fetch_copernicus_glo30` to return a pre-written tiny
      GeoTIFF (use `tmp_path`); also mock `depthwizard.pipeline.relative_height` to return a
      constant array and dummy `dinfo`
    - Patch `depthwizard.pipeline.calibrate` with `unittest.mock.patch` and capture the kwargs
    - Call `pipeline.run(rgb_path, out_dir, fetch_dem=True, dem_kind="auto", ...)` with a
      minimal georeferenced RGB GeoTIFF
    - **Assert** (for Mode C default):
      - `calibrate` was called with `dem_kind="terrain"`
      - `match_dem_30m` / `reference_consistent` is `False`
      - The `dem_path` argument points to `dem_terrain.tif` (the derived terrain proxy),
        not the raw `dem.tif`
    - **Assert** (explicit override is still respected):
      - When called with `dem_kind="surface"`, `calibrate` receives `dem_kind="surface"`
        and the original `dem.tif`, not the terrain proxy
    - Mark this test group with `@pytest.mark.parametrize` or separate functions; tag with
      `preservation` in the function name so `-k preservation` still selects them
  - **4b. Ground-extraction unit test** — add to `tests/test_cop_terrain_mode.py`:
    - Build a 256×256 synthetic array: Gaussian hill centred at (128, 128), amplitude 60 m,
      sigma 40 px; plus a 15 m rectangular box 20×20 px at (100:120, 100:120)
    - Call `copernicus_terrain_from_dsm(hill_plus_box, gsd=30.0)`
    - Assert: output ≤ input elementwise (opening never raises values)
    - Assert: output at the box centre < (hill_value_at_that_point + 3.0)
      (box is substantially removed — terrain under the box is recovered to within 3 m)
    - Assert: output at hill flanks (far from the box) agrees with input to within 2 m
      (hill shape is preserved)
    - No model, no network, no rasterio I/O needed — pure numpy
  - Run: `D:\DepthWizard\venv\Scripts\python.exe -m pytest tests/test_cop_terrain_mode.py -v`
  - **EXPECTED OUTCOME**: all tests in file pass
  - _Requirements: 2.1, 2.2, 3.1, 3.2, 3.3, 3.4, 3.5_
  - Commit: `test(pipeline): routing and ground-extraction tests for Copernicus terrain mode`

- [ ] 5. Run full pytest suite and update documentation
  - Run: `D:\DepthWizard\venv\Scripts\python.exe -m pytest -q`
  - **EXPECTED OUTCOME**: all tests pass (39 existing + new tests); zero failures
  - If any existing test fails, diagnose and fix before proceeding; do NOT weaken assertions
  - Update `CHANGES_ITEM2.md`:
    - Add a **Mode Comparison** section with the full Mode A/B/C table from Task 1
    - Add an **Acceptance Decision** section: which mode was chosen as default and why;
      if no mode passed, document the decision to revert `fetch_dem=False` with the table
    - Update the GPU acceptance table if Mode C numbers differ from the prior runs
  - Commit: `docs: Mode A/B/C comparison table and acceptance decision in CHANGES_ITEM2.md`
  - Ensure all commits are local only — do NOT push or rebase

## Task Dependency Graph

```json
{
  "waves": [
    {"wave": 1, "tasks": ["1", "2"]},
    {"wave": 2, "tasks": ["3"]},
    {"wave": 3, "tasks": ["4"]},
    {"wave": 4, "tasks": ["5"]}
  ]
}
```

## Notes

- Tasks 1 and 2 are independent: the helper function can be written while the comparison runs.
- Task 3 depends on the Task 1 table to decide which mode to route to.
- Task 4 (tests) depends on Task 2 (the helper must exist to import) and Task 3 (routing
  must be in place to test it end-to-end).
- Task 5 (pytest + docs) must be the final step.
- The cached Copernicus tiles in `data/dem_cache/` are sufficient — no live network calls needed.
- All commits are local only; do NOT push or rebase.
- Reference baselines to beat: Glover Park LiDAR RMSE 4.30 m / 145 buildings,
  Capitol Hill East 2.88 m / 102 buildings (from DTM-calibrated v2 runs).
