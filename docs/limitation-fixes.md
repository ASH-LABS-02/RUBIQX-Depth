# Limitation fixes and remaining evidence gaps

## Implemented

- Model errors stop processing by default. Only CLI prototype runs can request
  `--allow-fallback`; web uploads require a working model.
- Single-pass and heuristic predictions cannot produce pixel reliability or
  uncertainty maps. Downloads also reject stale uncertainty files for such jobs.
- Raw rotation spread and the provisional metric error model are distinguished
  in metadata, exports and reports. The error model's two-scene calibration is
  not independent validation of its coverage on other landscapes.
- Building reliability displays its actual basis (ensemble or roof consistency),
  rather than assuming every score came from model agreement.
- Relative exports and measurements are labelled unitless; relative reports
  suppress uncalibrated footprint areas.
- Export controls identify sampled meshes and points. Full-grid measurements
  should use the DSM GeoTIFF.
- Flood assumptions remain visible when details are collapsed. Slope angle
  bands no longer imply safety. Planning tools and reports identify field checks
  and simplified flow/coverage assumptions.

## Still requires data and measured improvement

Tall buildings, flat roofs, isolated trees, leaf-off forest, water and low-sun
imagery remain model/domain limitations. No new accuracy claim follows from the
changes above. Validation breadth and uncertainty coverage need additional
independent scenes, including Indian cities, forests and hilly terrain.

Previous experiments remain available in
[A3](a3-multiscale-evaluation.md) and
[A4](a4-coarse-cutoff-evaluation.md). Copernicus height scaling (A1) did not improve both required calibration
setups; A3 slightly worsened held-out GAMUS errors. Neither was enabled by
default. The coarse threshold remains 2.5 m: the promising A4 candidate needs
independent confirmation rather than tuning to the reserved DC scoring scenes.

Do not train, fit calibration parameters or select production settings against
`samples/dc_lidar/*/lidar_dsm_2024.tif`. Those files are scoring references only.
