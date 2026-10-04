# Viewer guide

[Back to README](../README.md)

It uses Three.js, vendored locally, so no CDN is needed and it works offline.

The app opens into **Terrain Mission Control**: a full-bleed 3D scene, a compact mode rail, a contextual tool drawer, a thumbnail layer dock, and an evidence status chip. The scene gallery highlights six contrasting examples; the scene picker still lists every local job. Import uses a three-step side sheet for image, scale/reference evidence, and processing. On narrow screens the tool drawer moves below the scene. A linked comparison keeps source RGB and estimated height aligned at the same pixel.

| Feature | Details |
|---|---|
| Navigation | Labelled **Orbit / Fly / Walk / Tour** controls; Fly uses pointer-lock and WASD/QE/Shift; Walk uses WASD/Shift at eye level on the displayed surface (metric scenes only; estimated footprint barriers are approximate); Tour is a 30 s cinematic pass (Shift+C; Esc stops); **Top down** and **Fullscreen**; a minimap you can click to jump |
| Surfaces | Optical drape, height colour ramp, slope (° for metric DSMs; relative gradient otherwise), curvature, error vs a metric reference; contour lines; wireframe |
| Controls | Expandable scene summary through **Details**; vertical exaggeration; display-only mesh smoothing; sun azimuth and illustrative time of day |
| Comparison | Linked source RGB and height maps with a shared cursor; clicking a pixel sets the 3D height probe |
| Probe | Estimated height, reference height, error, slope, aspect, and map coordinates (E/N) for georeferenced scenes |
| Coordinates | Source-affine E/N grid and live readout (Shift+G toggles); geographic CRS uses degrees, projected coordinates retain native CRS units; live lat/lon is transformed from the source affine coordinates with PROJ; plain images show pixels |
| Profile | Two clicks draw an elevation cross-section: estimate vs reference, length, Δh, grade, profile RMSE |
| Analysis | Connected flood scenarios from the lowest scene edge or a clicked source, a separate level-plane option, rainfall playback, estimated building and population exposure, route and refuge screening, landslide runout, and relay line of sight. Mission analysis requires an aligned DTM and local projected metre CRS. With a metric reference DSM, cut/fill volumes compare the estimated and reference surfaces. |
| Validate mode | RMSE/MAE/r cards, a DEM baseline comparison, estimated-versus-reference plots, and expandable per-landscape, height-band, edge-gradient, and calibration details |
| Export | Grouped raster, 3D, and evidence actions: GeoTIFF, textured GLB/OBJ, CityJSON, PLY, report, a complete ZIP, and a provenance-stamped viewer screenshot |
| Keyboard | 1–6 workspaces; Shift+1–9 surface layers; Shift+C cinematic; Esc stops; Shift+G coordinate grid; O/F/T/D/V/R for navigation; G gallery; E export; Ctrl+K action search; H help |

## Rendering and evidence

Surface view preserves the estimated raster surface. City buildings, fitted roofs,
procedural facades and dark green clustered tree crowns are visual representations,
not independently observed geometry. Exaggeration, lighting and smoothing do not
improve height accuracy. Trees are available only for metric scenes.

Balanced quality adapts to frame-time pressure. Surface/Optical views can stream
bounded full-resolution tiles for large metric scenes; other layers use the display
grid. A parent tile remains visible until its children are ready. Smoothing and
mesh-detail controls are disabled when incompatible with the streamed surface.
See [rendering budgets](rendering-budget.md).

Browser-local recent views preserve camera, layer and exaggeration. Calibration
changes are reported when restoring a view. Supported GCP/anchor edits have bounded
undo history. Processing jobs can be cancelled at stage or tile boundaries and
retried from the saved upload.

## Disaster interpretation

Static connected flooding and level-plane flooding are scenario screens. Rainfall
playback uses an assumed runoff fraction. The separate bounded surface-flow tool
models rainfall, infiltration and drainage losses on a sampled grid with closed
boundaries; it is not a validated event forecast and does not model upstream inflow,
sewer networks or external outfalls. Keep its evidence separate from bathtub counts.

Routes use supplied access masks where available; unknown access is not verified
safety. Refuges, population exposure, slope/runout and relay viewshed results require
field review. A viewshed measures geometric visibility, not radio coverage.
