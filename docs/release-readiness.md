# Release readiness — 3 October 2026

This checklist records source inspection and existing evidence. It is not a
release, deployment approval or claim of new FPS/accuracy improvement.

## Verified source and recorded evidence

| Area | Evidence available | Limit |
|---|---|---|
| Model lineage | Current documented v6a and versioned raw results in [BENCHMARKS](BENCHMARKS.md) / [MODEL_CARD](MODEL_CARD.md) | Same-domain tests and historically inspected DC scores do not establish new geographic generalization |
| Uncertainty | Source records the 4.0 m floor / 5.5 spread gain, two-scene fit and `independent_validation=False` | The same DC references cannot independently validate the final formula |
| Earlier implementation checks | [Semantic prototype](semantic-prototype.md) records 72 pytest passes and a local CLI/viewer check on 3 Oct | Predates the latest tree LOD, modal and Walk changes; no new test suite was run for this documentation update |
| Tree LOD | Source preserves canopy candidates, metric placement and top anchoring while switching chunk representations/shadows | Display geometry only; both representations remain in memory, and chunk switches can change silhouettes. Diagnostics estimate geometry before frustum culling |
| Dialogs | Source implements focus containment/restoration, topmost Escape, Ctrl+K behavior and preserved inert/ARIA background state; the local manual checks below passed | A single preview does not establish browser/assistive-technology acceptance |
| Walk | Source implements metric-scene spawning, estimated footprint barriers and slope/step movement limits; nearby-ground spawn was observed locally | Full movement remains pending. Displayed terrain and inferred footprints omit real access, interiors, bridges and other obstacles; not pedestrian safety validation |
| Rendering profile | One [local Intel UHD preview report](performance/glover-park-city-intel-preview.json) records frame pacing, CPU submission and renderer context/counts | No controlled before/after or target-hardware acceptance benchmark; CPU timing excludes GPU completion |
| Roofs / anchors / VR | Inferred flat/plane/gable roofs, provisional shadow/OSM anchors and gated WebXR session code exist | Surveyed roof/anchor accuracy and headset compatibility are not established by source availability |

## Local manual observations — 3 October 2026

- City rendered with no console warnings/errors in the in-app browser preview.
- Walk entered at nearby clear ground; sustained movement, collision edge
  cases and all view/scene transitions remain pending.
- Help contained keyboard focus and suppressed the F camera shortcut.
  Import → Ctrl+K opened a nested command palette; the Escape chain closed
  the top dialog first and restored focus through the dialogs to the trigger.
- At 390 × 844, the viewer and import dialog fit the viewport; horizontal
  workspace/layer scrolling is intentional. This is one responsive spot check.
- The [final preview report](performance/glover-park-city-intel-preview.json)
  records **30.49 FPS**, **53.0 ms P95**, **914 frames** on the reported Intel
  UHD renderer, 1280 × 720, pixel ratio 1.25, City/Optical/Orbit, 512 × 512 grid,
  3,069 tree candidates. Quality was Balanced; the existing adaptive fallback
  selected Performance tree representation. The reported tree geometry estimate
  was 110,484 triangles versus 1,544,936 for its detailed representation, before
  frustum culling. This is a geometry comparison, not measured FPS improvement.
  The earlier interim frame capture is superseded by this saved report. The
  displayed report was saved via the DOM; the profile JSON download interaction
  was not verified.

## Read-only service and asset audit

From the repository root:

```powershell
python -B scripts/check_release.py --base-url http://127.0.0.1:8010 --timeout 10
python -B scripts/check_release.py --base-url http://16.170.173.94 --timeout 10
```

The CLI sends only GET requests to health, scenes, local-model metadata,
index and the declared app script. It caps each request timeout at 10 seconds
and each response at 2 MiB, follows no redirects and changes no service state.
It prints compact JSON with status/errors, missing metadata, index asset
versions and the app script hash compared with this checkout. Exit 0 means
the listed HTTP/schema/asset checks passed; it does not establish release
readiness or model accuracy. An optional `--checkpoint` directory adds a
config/weight hash receipt with selected non-path config fields; checkpoint
paths and raw model API errors are redacted.

The local audit at 16:39 UTC returned HTTP 200 for the checked resources:
health `ok:true`, 25 scenes, local model `ready:true`, stage `complete`, six
completed epochs. Served app version `20261003-navigation-budget` and script
content matched this checkout. The API does not expose a model version,
checkpoint hash or server build version, so the audit reports those omissions
explicitly. No optional checkpoint hash receipt was collected in this run.

The AWS root and `/api/health` previously each timed out at 15 seconds from
this environment. That observation does not establish the deployed version
or whether the service is unavailable to other clients; remote asset/model
identity and readiness remain pending. No deployment was performed.

## Pending before release claims

- [ ] Record checkpoint/config/input hashes and resolve model/data licences for
  the proposed distribution. Keep the optional semantic checkpoint experimental
  until its licensing and independent mask evidence are established.
- [ ] Use the [independent benchmark workflow](independent-benchmark.md) to
  freeze development settings, audit source-AOI overlap and score untouched
  scene-disjoint sites. Fit uncertainty on different development references.
  Include Indian/Cartosat, forest, hill, shadow and tall-object cases.
- [ ] Capture comparable Surface/City, Orbit/Walk/Tour and quality-mode profiles
  on target machines. Record browser, renderer/hardware, viewport, pixel ratio,
  scene and camera path; retain reports and frame-time percentiles. Compare
  equivalent settings before asserting any performance improvement.
- [ ] Check LOD silhouette transitions, shadows, picking, memory use and scene
  reload/disposal on large tree scenes. Confirm display changes leave exported
  height grids and analysis units consistent.
- [ ] Exercise Walk spawning near roofs/bounds, narrow footprints, steep/stepped
  surfaces, City/Surface switching, exaggeration, reloads and pointer-lock
  release. Document failures using estimated geometry rather than claiming
  real-world access.
- [ ] Exercise every dialog with keyboard-only navigation: Tab/Shift+Tab,
  hidden/disabled controls, text editing, Ctrl+K nesting, repeated Escape,
  trigger restoration and pending gallery loads. Check background camera
  shortcuts/pointer lock are suspended. Check screen readers and supported
  browsers.
- [ ] Check WebXR on a supported secure-context headset/browser and recording
  codecs on target browsers; retain support gates when unavailable.
- [ ] Confirm profile JSON downloading and collect comparable captures on
  target hardware; the saved DOM preview report is not a download acceptance check.
- [ ] Refresh appropriate implementation checks after the final source changes
  and record their actual scope/results; retain the earlier 72-pass record as
  historical evidence.
- [ ] Build/install on a clean Windows machine with the intended model files,
  DEM/geoid caches and dependencies; check offline failure behavior, export
  completeness, long-running jobs and uninstall/reinstall behavior.

No deployment or installer changes are made by this checklist.
