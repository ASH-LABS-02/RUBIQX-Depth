# DepthWizard UI v2 — handover

Implemented 1 October 2026 in `C:\Users\AR\Desktop\depthwizard`.
This is a frontend polish pass over the existing rail / layer dock / inspector. It does not retrain the model, change accuracy results, or establish new benchmark claims.

## Scope and commits

- Art direction: `2f3f94eb` — mission layout, typography and overlays.
- P1: `485b9967` — import, onboarding, navigation and validation workflows.
- P2: final local commit — stability fixes, offline map, unit corrections, tests and screenshots.
- No push or remote merge performed. Existing user changes in Python/training scripts were not staged or overwritten.
- No API endpoint, request body or response field changed. No framework, build step or remote font dependency added. Existing Three.js and addons remain vendored.
- README images outside `docs/images/ui-v2/` were not replaced. The team can regenerate those separately.

## Brief checklist

Status **done** means implemented; browser verification limits are listed below. **Partial** identifies a specific remaining requirement.

### 1. Hard constraints

| Item | Status / implementation |
| --- | --- |
| API compatibility / existing control IDs | Done — existing named inputs and handlers retained; import controls reparented. |
| Offline rendering | Done — local fonts, vendored renderer, generated sky and uploaded optical map. Removed external basemap tile requests. Auto-download DEM remains an explicit online processing option; uploaded DEM is the offline alternative. |
| Laptop / CPU server | Done — on-demand rendering retained; adaptive quality added. Actual iGPU hardware benchmark not available. |
| Product naming | Done — DepthWizard chrome and export provenance; RUBIQX footer. |
| Backend-derived numbers / honest labels | Done — results use scene data; calibration confidence and relative units remain explicit. Input DEM is labelled not estimated. No fabricated confidence when its product is absent. |
| Regression checks | Done at milestones and final handover — 28 pytest tests; GeoTIFF and PNG workspace walkthroughs. A full suite was not run after every individual edit. |

### 2. Existing v1.5 checks

| Item | Status / fixes |
| --- | --- |
| Default Glover Park | Done — first scene selection prefers `dc-glover-park`; explicit URL hash still selects its scene. |
| Readable titles | Done — stage and header titles retained, long inspector content scrolls internally. |
| Pretrained ↔ DepthWizard swipe | Done — full model names, corrected handle and evidence note. |
| Overlay collisions | Done — fixed locations for toolbar, legend, evidence, fly hints and probe; presentation closes comparison/map panels. |
| Error legend | Done — signed reference error saturates at ±20 m for metric scenes; relative scenes use their own units. |
| One-step anchor apply | Done — selected building height applies through existing rescale endpoint; original multi-anchor and GCP controls retained. |
| Consistent height | Done — uses reported structural height; PNG inspector no longer says metres or undefined storeys. |
| Toolbar tooltips / immediate redraw | Done — named navigation and existing descriptive tooltips; layer switch requests a frame. |
| Flood slider | Done — scene limits set before value; animate and drain checked; hero counters now update with flood state. |

### 2b. Art direction

| Item | Status / implementation |
| --- | --- |
| Palette / typography | Done — navy, amber actions, cyan selections; local Space Grotesk; monospace/tabular numeric readouts. |
| Receding chrome / instrumentation | Done — hairlines, restrained glass, consistent drawer headers and spacing; prominent accuracy numbers. |
| Motion | Done — short ease-out interactions; cancellable 5-second opening; reduced-motion support. Existing user-triggered tours, rain and building animations remain available. |
| Terrain | Done — generated sky gradient, haze, balanced ambient occlusion and existing directional sun shadows. Low frame rates reduce AO/quality. |
| Minimap | Done — optical footprint, camera marker, geographic ticks where coordinates are available; image-pixel ticks for PNG. |
| Probe | Done — compact crosshair readout, relative/metric heights, coordinates where available; avoids fixed overlays. |
| Presentation | Done — title, compass, scale and metric strip; slow orbit, reduced-motion orbit disabled. Open comparison, map, export and help overlays are closed. |

### 3. First 30 seconds

| Item | Status / implementation |
| --- | --- |
| 3.1 Opening | Done — Roof-fit City, 5-second fly-in; pointer, wheel, key and touch cancel it; reduced-motion skips it. |
| 3.2 Welcome | Done — remembered dismissal, three steps, metric/relative demos, `?` help. |
| 3.3 Evidence | Done — clickable explanation of supplied evidence, scale and datum. Unknown evidence is not labelled measured. |
| 3.4 Units | Done — relative heights in PNG probes, inspector, hero and evidence; uncalibrated storeys/volume require calibration. Flood screening is disabled without metric units. |

### 4. Import

| Item | Status / implementation |
| --- | --- |
| 4.1 Automatic path | Done — bounded classic TIFF / BigTIFF header inspection, endian support, CRS/GSD/bands detection; PNG relative path. Backend remains authoritative. Signed/float elevation rasters are distinguished from unsigned panchromatic optical TIFFs. |
| 4.2 Defaults | Done — COP30 auto-download, auto DEM type, 30 m match, available local GAMUS model, TTA 4; uploaded DEM alternative. Input DEM bypasses depth estimation; PNG does not fetch a DEM. |
| 4.3 Advanced | Done — GCP, reference, optional sun, prior, checkpoint/model and TTA collapsed; run action remains above advanced settings. |
| 4.4 Resolution | Done — detected GSD, warnings outside 0.35–10 m and coarse detail note above 2.5 m. Unknown projected units are not invented as metres; geographic spacing is labelled approximate. |
| 4.5 Progress | Done — actual backend logs drive stages, elapsed time and labelled estimate; raw details retained. Input DEM stages explicitly say not estimated. Errors show a readable message and details. |
| 4.6 Queue | Done — backend queue position drives waiting text. An actual concurrent queue was not exercised. |

### 5. Navigation

| Item | Status / implementation |
| --- | --- |
| 5.1 Named modes | Done — Orbit / Fly / Tour; Fly hint fades after five seconds away from metrics. |
| 5.2 Camera | Done — terrain double-click fly-to, Home/reset, affine-derived north-up top view. |
| 5.3 Compass / scale | Done — compass follows camera and grid north; scale derives from screen-centre ground plane after zoom. Oblique terrain scale varies with perspective and is labelled accordingly. |
| 5.4 Exaggeration | Done — metric default 1×; presets 1 / 1.5 / 2 / 3; exaggerated badge. |
| 5.5 Performance | Done — sustained >33 ms frame intervals trigger Cinematic → Balanced and then AO reduction with toast. 60 fps remains a target, not a measured guarantee. |

### 6. Validation

| Item | Status / implementation |
| --- | --- |
| 6.1 Metrics | Done — backend RMSE / MAE / Pearson r and actual baseline improvement; r shown to three decimals. |
| 6.2 Order | Done — scatter, histogram, collapsed breakdown with row explanations; calibration warnings in breakdown. |
| 6.3 No reference | Done — upload-reference action opens advanced input. Existing API requires reprocessing with the original image; it does not attach a reference to the current scene in place. |
| 6.4 Copernicus comparison | Partial — actual DSM minus input DEM overlay; Copernicus label only when scene provenance identifies it. Current endpoint exposes a resampled display grid, so this view is explicitly **display-grid calibration consistency**, not native 30 m blind scoring. Producing the exact native aligned 30 m map requires additional raster data/API work excluded by this frontend-only brief. Existing backend 30 m validation results remain in the accuracy breakdown when available. |

### 7. Visual polish

| Item | Status / implementation |
| --- | --- |
| 7.1 Scale | Done — standard sizes/spacing/radius for new chrome, tabular figures and numeric alignment. Legacy feature controls retained. |
| 7.2 Panels | Done — consistent title/subtitle/collapse, spacing and hairlines. |
| 7.3 Legend | Done — active legend immediately above layer dock at a fixed position. |
| 7.4 Contrast | Partial — dark default and higher-contrast main/secondary text; light variables adjusted. Full WCAG AA audit of every legacy control, disabled state and textured viewport label was not completed. |
| 7.5 Loading | Done — thin stage progress and skeleton/busy panel treatment. |

### 8. Stability

| Item | Status / implementation |
| --- | --- |
| 8.1 Failures | Done with limitation — API errors surface readable toast and Retry; asynchronous property handlers catch rejected promises. Abort cancellation and expected missing optional products are quiet. Retry checks connection without repeating a possibly completed mutation; user then retries the relevant action. It does not resume an interrupted job poll automatically. No console warnings/errors in exercised final flows. |
| 8.2 Stale scenes | Done — load generations and AbortController for metadata/binary layers; stale textures disposed before commit. |
| 8.3 Laptop layout | Done — verified 1366×768, document width 1366 and height 768, scrolling within panel. |
| 8.4 Matrix | Partial — in-app browser verification below. Chrome / Edge unavailable through installed browser control. Final OS file-save location cannot be verified through this browser. |

### 9–10. Scope / deliverables

Done — no new analytics or backend changes; existing comparison, calibration, mission, change and export features retained. Screenshots and this checklist added. Frontend-builder concept generation was skipped because this brief specifically preserves the existing design structure; the visual pass was implemented in local CSS.

## Verification evidence

- `pytest -q`: **28 passed**, 33 existing deprecation/georeferencing warnings.
- `node --test tests/import-metadata.test.mjs`: **11 passed**.
- JavaScript syntax checks: `app.js`, `ui-v2.js`, `import-metadata.js`.
- All six workspaces opened with georeferenced Glover Park and PNG scene; no browser console errors.
- GeoTIFF metadata: 1024×1024, EPSG:26985, 0.50 m/px; PNG path shows relative units.
- Input DEM uploaded through UI, processed with existing API, viewer labels it not estimated. Missing confidence export is disabled.
- SIMULATED post-event scene kept its simulated label. Flood rise showed increasing affected cells/buildings; Drain returned both inspector and hero counters to zero.
- Anchor applied and reset on a temporary copy of Glover Park. Original scene was not modified by this check.
- Automatic approval review blocked recursive QA cleanup despite explicit path checks. Retained local QA folders: `data/jobs/ui-v2-anchor-qa` and `data/jobs/20261001-175059-eba417` (Input DEM). They are not part of the source commit. The temporary preview server and QA browser were closed; existing training processes were left running.
- Model Compare displayed backend results (pretrained 6.35 m, GAMUS 6.21 m), not invented numbers.
- DEM consistency overlay, offline optical map and clean presentation mode exercised.
- DSM export produced a successful HTTP 200 response and kept the application open. Browser download event reporting timed out; final saved filename/path is unverified.
- Tests do not establish model accuracy beyond the supplied references. No new training or benchmark run was performed in this UI pass.

## Before / after images

Before captures use the original 1280×720 browser viewport; after captures use the requested 1366×768 laptop viewport.

| View | Before | After |
| --- | --- | --- |
| Landing | [Before](docs/images/ui-v2/before-landing.jpg) | [After](docs/images/ui-v2/after-landing.jpg) |
| Import | [Before](docs/images/ui-v2/before-import.jpg) | [After](docs/images/ui-v2/after-import.jpg) |
| Buildings / anchor | [Before](docs/images/ui-v2/before-buildings.jpg) | [After](docs/images/ui-v2/after-buildings.jpg) |
| Validate | [Before](docs/images/ui-v2/before-validate.jpg) | [After](docs/images/ui-v2/after-validate.jpg) |
| Model Compare | [Before](docs/images/ui-v2/before-model-compare.jpg) | [After](docs/images/ui-v2/after-model-compare.jpg) |

Additional QA captures show welcome, anchor application, relative units, Input DEM and presentation mode. Start the application using the project's existing startup command; open the local app without a scene hash for the default Glover Park welcome experience.
