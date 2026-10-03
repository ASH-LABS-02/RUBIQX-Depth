# Tree detail, Walk and rendering measurements

Implemented on 3 October 2026. These changes affect viewer navigation and display
geometry; they do not modify exported DSM/DTM/nDSM pixels or height scores.

## Tree representations

Trees retain their existing canopy candidates, dark green palette, metric
placement and estimated ground/top elevations. Spatial chunks use detailed
branching crowns nearby and a simple crown/trunk at distance. Projected screen
size and selected quality control switching, with hysteresis and a throttled
check. Distant trees cast no shadows; Performance also disables nearby tree
shadows. Both representations occupy memory, and transitions change silhouette.

The existing slow-frame fallback can use Performance tree detail while the
selected overall quality remains Balanced. Reports retain both settings.
Geometry diagnostics are estimates before frustum culling and renderer passes;
they are not measured FPS savings.

## Walk

Walk is available for metric scenes. Entry searches for nearby clear displayed
ground rather than starting on a roof. Estimated building polygons block a
0.35 m camera radius, movement subdivides into at most 0.18 m steps, and blocked
motion can slide along a footprint. The camera follows ground at 1.7 m eye
height; diagonal travel is normalized. Movement limits are 35 degrees and
0.45 m per step, evaluated in source metres before visual exaggeration.

These inferred footprints and displayed surfaces do not describe real doors,
interiors, bridges, access restrictions or every obstacle. Walk is not a
validated evacuation or pedestrian-access model. It can fail to find suitable
ground, in which case it reports the issue rather than spawning arbitrarily.

## Record a comparable profile

1. Open a scene and select City/Surface, layer, mesh detail and render quality.
2. Set a reproducible camera position/path and the intended viewport.
3. Open **Explore → Measure rendering performance**, add a hardware note and
   choose **Record 30 s**. Keep the tab visible and avoid opening a modal.
4. Retain the displayed report or **Download JSON**, together with the build
   revision and input scene. Repeat equivalent settings before/after a change.

The recorder excludes a 2 s warmup, forces continuous rendering and suspends
automatic idle orbit. Opening a dialog, hiding the tab or changing scene cancels
capture. User-driven navigation, quality changes or flood animation may still
affect results; keep them consistent. CPU submission timing includes JavaScript
updates and render submission, excluding GPU completion. Draw/triangle peaks
include shadows and postprocessing passes.

## Recorded local preview

[Raw JSON](performance/glover-park-city-intel-preview.json), 3 October 2026:

| Setting/result | Observed value |
|---|---|
| Scene | Default DC Glover Park; City / Optical / Orbit |
| Display grid | 512 × 512 |
| Viewport / pixel ratio | 1280 × 720 / 1.25 |
| Browser renderer | Chrome 154 in Codex; ANGLE Intel UHD Direct3D11 |
| Selected quality / final tree detail | Balanced / Performance via adaptive fallback |
| Trees | 3,069; all distant at this camera position |
| Measured frames / time | 914 / 29.97 s |
| FPS / median frame / P95 frame | 30.49 / 31.2 ms / 53.0 ms |
| CPU submission median / P95 | 15.4 ms / 28.9 ms |
| Peak renderer calls / triangles | 449 / 651,226 |
| Estimated active tree triangles / all-detailed representation | 110,484 / 1,544,936 |

This is a current preview, not a before/after speed comparison, GPU timing,
RTX 4060 benchmark or general performance guarantee. The report was saved from
the visible JSON panel; the browser download event was not independently
confirmed. No console warning/error appeared during these local checks.

Also checked: nearby-ground Walk entry; Help focus containment and camera-key
suppression; nested Import → Ctrl+K → Escape focus restoration; viewer/import
layout at 390 × 844. Full walking collision paths, large-scene memory/disposal,
other browsers, screen readers and headsets remain release checks. No new
pytest suite was run.
