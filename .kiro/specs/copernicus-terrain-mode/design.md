# Copernicus Terrain Mode Bugfix Design

## Overview

The auto-fetched Copernicus GLO-30 calibration path (`copernicus-glo30-auto`) currently defaults
to `dem_kind="surface"`, treating the Copernicus DSM — which embeds building and canopy tops —
directly as a terrain floor. This inflates the derived DTM by the height of existing surface
objects, suppresses the nDSM below the 2.5 m building-detection threshold, and causes zero
buildings to be detected in both DC test scenes.

The fix introduces **Mode C**: when `dem_origin == "copernicus-glo30-auto"` and `dem_kind` is not
explicitly overridden by the user, derive an approximate bare-earth surface from the Copernicus
DSM using morphological opening (grey_opening, kernel ~150–300 m) followed by Gaussian smoothing,
then feed that terrain proxy into the existing `dem_kind="terrain"` calibration path. The
Copernicus DSM is still used for the `vs_copernicus_30m` consistency score.

Modes A and B remain available via explicit `--dem-kind surface` and `--dem-kind surface
--no-consistency` flags respectively.

## Glossary

- **Bug_Condition (C)**: `dem_origin == "copernicus-glo30-auto"` AND `dem_kind` resolves to
  `"surface"` (i.e. `dem_kind="auto"` was used with a COP30 source — the current default).
- **Property (P)**: For all inputs meeting Bug_Condition, the fixed pipeline SHALL produce an
  nDSM with sufficient above-ground heights that building detection finds >100 buildings at
  Glover Park and >80 at Capitol Hill East, and LiDAR RMSE is within ~0.5 m of the DTM baseline.
- **Preservation**: All non-Copernicus-auto DEM paths, explicit user `--dem-kind` overrides, and
  all offline pytest tests must behave identically to the pre-fix code.
- **`copernicus_terrain_from_dsm(dsm, gsd)`**: New helper (in `calibrate.py`) that applies
  grey_opening and Gaussian smoothing to a Copernicus DSM array to approximate bare earth.
- **`effective_kind`**: Variable in `pipeline.py` that selects `"surface"` or `"terrain"` for
  `calibrate()`. The bug is that it resolves to `"surface"` for COP30 auto.
- **`dem_origin`**: String set in `pipeline.py` to `"copernicus-glo30-auto"` when the DEM was
  auto-fetched; used by the fix to gate the Mode C logic.
- **nDSM**: Normalised Digital Surface Model — above-ground height layer (`k · structure`).
  Zero buildings are detected when all nDSM values fall below `min_height_m = 2.5 m`.

## Bug Details

### Bug Condition

The bug manifests when a georeferenced image is processed without a user-supplied DEM, triggering
the auto Copernicus fetch, and `dem_kind` is left at the default `"auto"`. The `pipeline.py` line:

```python
effective_kind = "surface" if dem and effective_source == "COP30" and dem_kind == "auto" else dem_kind
```

resolves `effective_kind = "surface"`. Inside `calibrate()`, the `kind == "surface"` branch then
computes:

```python
terrain = dem - k * S          # DEM already contains building heights → terrain overshot
dsm = terrain + datum_offset + k * structure
```

Because `dem` (Copernicus DSM) already includes building heights, `terrain` is elevated above true
ground, and `k * structure` (the nDSM) is systematically too small.

**Formal Specification:**

```
FUNCTION isBugCondition(run_config)
  INPUT: run_config of type PipelineRunConfig
  OUTPUT: boolean

  RETURN run_config.dem_origin  = "copernicus-glo30-auto"
         AND run_config.dem_kind = "auto"          // user did not override
         AND effective_kind      = "surface"        // current resolution in pipeline.py
END FUNCTION
```

### Examples

- **Glover Park (current)**: Copernicus DSM used as terrain floor → DTM ~3–4 m above true ground
  → nDSM max ~1–2 m → 0 buildings detected, LiDAR RMSE 7.31 m (vs 4.30 m with bare-earth DTM).
- **Capitol Hill East (current)**: Same mechanism → 0 buildings detected, LiDAR RMSE 5.32 m
  (vs 2.88 m with bare-earth DTM).
- **Expected after fix (Mode C)**: Approximate terrain derived from Copernicus via morphological
  opening → DTM ≈ true ground → nDSM captures building heights → >100 / >80 buildings detected,
  LiDAR RMSE approaching DTM baseline.

## Expected Behavior

### Preservation Requirements

**Unchanged Behaviors:**
- `--dem-kind surface` (Mode A): existing `_surface_fit` path, unchanged.
- `--dem-kind surface --no-consistency` (Mode B): surface path without 30 m correction, unchanged.
- `--dem-kind terrain` with any user DEM: DEM passed straight to terrain path, unchanged.
- `--dem-kind auto` with SRTMGL1: resolves to `"surface"` as before (SRTM is a DSM), unchanged.
- Offline pytest suite: no new I/O or network calls in tests; all 39 existing tests must pass.

**Scope:**
All inputs where `isBugCondition` is `False` — any explicit `--dem-kind`, any non-COP30 source,
any user-supplied DEM, non-georeferenced images — must produce bit-identical outputs to the
pre-fix code.

## Hypothesized Root Cause

1. **Wrong DEM type for `dem_kind="auto"`**: The auto-resolution logic assumes any COP30 source
   is best treated as a surface DEM. For a Copernicus DSM this is appropriate when the goal is
   datum anchoring at 30 m scale, but catastrophic for building detection because the terrain
   floor is inflated by the very structures the pipeline is trying to measure.

2. **`_surface_fit` leaks surface signal into terrain**: `terrain = dem - k * S` subtracts the
   fitted structure mean from the Copernicus DSM, but the Copernicus DSM already contains building
   heights at full amplitude; `k * S` cannot perfectly cancel the real building signal, leaving
   a residue that over-elevates the derived terrain.

3. **`reference_consistent=True` doubles down**: The 30 m correction `dsm += gaussian(dem -
   uniform(dsm), n/3)` pulls the fine-resolution DSM back toward the Copernicus surface values,
   further destroying sub-30 m building contrast.

4. **No fallback detection when buildings == 0**: The pipeline has no alert when `effective_kind`
   choice suppresses all building detections; the bug silently ships a metrics.json claiming 0
   buildings.

## Correctness Properties

Property 1: Bug Condition — Copernicus Auto Uses Terrain-Derived Floor

_For any_ pipeline run where `isBugCondition` holds (Copernicus auto-fetch, `dem_kind="auto"`),
the fixed pipeline SHALL derive an approximate bare-earth surface from the Copernicus DSM via
`copernicus_terrain_from_dsm()` and pass it to `calibrate()` with `dem_kind="terrain"` and
`reference_consistent=False`, producing nDSM values large enough for building detection (>100
buildings at Glover Park, >80 at Capitol Hill East) and LiDAR RMSE within ~0.5 m of the DTM
baseline.

**Validates: Requirements 2.1, 2.2, 2.3, 2.4**

Property 2: Preservation — Explicit dem_kind and Non-Copernicus Paths Unchanged

_For any_ pipeline run where `isBugCondition` is `False` (explicit `--dem-kind`, non-COP30
source, user-supplied DEM, or relative mode), the fixed pipeline SHALL produce exactly the same
`effective_kind`, `dsm`, `ndsm`, `dtm`, building counts, and `vs_copernicus_30m` metrics as the
original code, preserving all existing calibration behaviour.

**Validates: Requirements 3.1, 3.2, 3.3, 3.4, 3.5, 3.6**

## Fix Implementation

### Changes Required

**File**: `depthwizard/calibrate.py`

**New helper function** `copernicus_terrain_from_dsm(dsm_arr, gsd)`:

```python
def copernicus_terrain_from_dsm(dsm_arr: np.ndarray, gsd: float) -> np.ndarray:
    """Approximate bare-earth terrain from a Copernicus DSM array.

    Morphological opening with a kernel spanning ~200 m removes building/canopy
    tops; subsequent Gaussian smoothing fills small gaps left by narrow streets.
    Identical approach to metrics._ground() but tuned for a coarser (30 m) input.
    """
    kernel_px = max(3, int(round(200.0 / max(gsd, 1.0))))
    opened = ndimage.grey_opening(np.nan_to_num(dsm_arr, nan=float(np.nanmin(dsm_arr))),
                                   size=(kernel_px, kernel_px))
    return ndimage.gaussian_filter(opened, max(1.0, 50.0 / max(gsd, 1.0))).astype(np.float32)
```

**File**: `depthwizard/pipeline.py`

**Change 1** — Replace the `effective_kind` line and add a `cop_terrain` preprocessing step:

```python
# Old:
effective_kind = "surface" if dem and effective_source == "COP30" and dem_kind == "auto" else dem_kind

# New:
effective_kind = dem_kind  # user override respected as-is
cop_terrain_dem = None     # path to the preprocessed terrain proxy (Mode C)
if dem and dem_origin == "copernicus-glo30-auto" and dem_kind == "auto":
    # Mode C default: derive approximate terrain from the Copernicus DSM so that
    # nDSM captures real building heights instead of being suppressed by the
    # surface signal already embedded in the Copernicus DSM.
    from .calibrate import copernicus_terrain_from_dsm
    import rasterio
    from rasterio.transform import from_bounds as _from_bounds
    # Read the fetched Copernicus GeoTIFF, compute terrain proxy, write sidecar
    cop_terrain_path = out / "dem_terrain.tif"
    with rasterio.open(dem) as _src:
        _cop_arr = _src.read(1).astype(np.float32)
        _cop_res = abs(_src.transform.a)
        _cop_profile = _src.profile.copy()
    _terrain_arr = copernicus_terrain_from_dsm(_cop_arr, _cop_res)
    _cop_profile.update(dtype="float32", nodata=np.nan)
    with rasterio.open(cop_terrain_path, "w", **_cop_profile) as _dst:
        _dst.write(_terrain_arr, 1)
        _dst.update_tags(SOURCE="Copernicus GLO-30 terrain proxy (morphological opening)",
                         VERTICAL_DATUM="EGM2008 geoid (Copernicus GLO-30)")
    cop_terrain_dem = str(cop_terrain_path)
    effective_kind = "terrain"
```

**Change 2** — Pass `cop_terrain_dem` (when set) to `do_calibration`:

```python
# In do_calibration() call:
cal_dem = cop_terrain_dem if cop_terrain_dem else dem
dsm, units, cal = do_calibration(cal_dem, effective_kind, effective_source, datum)
```

**Change 3** — Keep `vs_copernicus_30m` score using the original `dem` (not the terrain proxy):

The existing condition `if dem and effective_source == "COP30" and units == "metre"` remains
valid because `dem` still points to the original Copernicus DSM GeoTIFF.

**Change 4** — `dem_kind` metadata in `cal` reflects the resolved mode. No change needed;
`calibrate()` already records `cal.dem_kind = kind`.

### Scope of Change

- `calibrate.py`: add `copernicus_terrain_from_dsm()` (~10 lines). No changes to `calibrate()`.
- `pipeline.py`: replace 1 line (`effective_kind = ...`) with ~15 lines that conditionally build
  the terrain proxy sidecar and set `effective_kind = "terrain"`.
- No changes to `dem_fetch.py`, `__main__.py`, `metrics.py`, or any test files for the core fix.

## Testing Strategy

### Validation Approach

Empirical-first: run three modes on real DC scene data with the actual Copernicus tiles (cached)
to get real building counts and LiDAR RMSE. The synthetic 64×64 exploration test was insufficient
because surface-fit scale estimation behaves differently on a toy flat DEM; real Copernicus
terrain variation is needed to reproduce the building-suppression failure. The test suite covers:

1. **Ground-extraction unit test**: pure-numpy test of `copernicus_terrain_from_dsm` on a
   synthetic hill + box; verifies the helper lowers bumps and preserves terrain shape.
2. **Routing tests**: mock-based pipeline tests (no model, no network) that assert the correct
   `dem_kind`, `match_dem_30m`, and `dem_path` kwargs are passed to `calibrate()` for the
   auto-fetch Copernicus path and for explicit `--dem-kind` overrides.

### Ground-Extraction Test

**Goal**: Confirm `copernicus_terrain_from_dsm` removes elevated objects and preserves terrain.

**Test Case**:
- Input: 256×256 array = Gaussian hill (amplitude 60 m, sigma 40 px) + 15 m rectangular box
  (20×20 px) at a position on the hill
- Assert: `output ≤ input` elementwise (morphological opening never raises)
- Assert: `output[box_centre] < hill_value_at_box_centre + 3.0` (box substantially removed)
- Assert: `|output[hill_flank] - input[hill_flank]| < 2.0` (hill shape preserved far from box)

### Routing Tests

**Goal**: Confirm pipeline routes auto-fetch Copernicus to the chosen mode and that explicit
overrides are unchanged.

**Test Cases**:
1. **Auto-fetch Copernicus → Mode C (terrain proxy)**:
   Mock `fetch_copernicus_glo30`, mock `relative_height`, patch `calibrate`.
   Assert `calibrate` called with `dem_kind="terrain"`, `reference_consistent=False`,
   `dem_path` pointing to `dem_terrain.tif`.
2. **Explicit `--dem-kind surface` override not hijacked**:
   Same mocks, call with `dem_kind="surface"`.
   Assert `calibrate` called with `dem_kind="surface"` and original `dem.tif`.

### Real-Data Acceptance (scripts/compare_cop_modes.py)

**Goal**: Decide which mode to route to by comparing LiDAR RMSE and building count on both DC
scenes.

**Reference targets** (from DTM-calibrated v2 runs):
- Glover Park: LiDAR RMSE 4.30 m, 145 buildings
- Capitol Hill East: LiDAR RMSE 2.88 m, 102 buildings

**Acceptance threshold**: within ~0.5 m of DTM baseline RMSE AND buildings detected (>100 / >80).

**Decision rule**:
- Mode C passes both scenes → Route auto-fetch to Mode C
- Mode B passes, Mode C does not → Route to Mode B  
- Neither passes → Revert `fetch_dem` default to `False`, document table
