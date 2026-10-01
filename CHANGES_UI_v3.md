# DepthWizard UI v3 — BOLD

Implemented locally on 1 October 2026. The terrain now occupies the full viewport, with a closed-by-default inspector, floating header and icon rail, a vertical camera toolbar, circular layer previews and a circular optical inset.

## Delivered

- A common glass treatment, saffron actions, cyan data, local Space Grotesk and JetBrains Mono, keyboard focus rings and responsive inspector controls.
- A bounded terrain cutaway with layered decorative walls, rim, base, contact shadow, fading ground grid, navy/teal atmosphere and lifted ACES exposure. The soil texture is illustrative; it is not geological evidence and is not included in scientific exports.
- A five-second first-arrival camera sequence and fading title, skipped on input or reduced-motion preference. Idle rotation starts after 20 seconds and stops on input.
- Compact backend-derived scene metrics, contextual legends and fully named comparison labels. Comparison selection closes the inspector to keep the labels visible.
- Balanced rendering caps pixel ratio at 1.5, avoids rebuilding static shadows every frame and retains adaptive AO reduction. Slow active frames trigger solid glass.
- An orbit camera clamp fix allows the complete terrain block to remain in view. The previous first-person bounds also constrained orbit framing.

## Deliberate adjustments

- Arrival copy says **estimated 3D world** or **relative 3D world**, according to the scene. It does not claim that an unverified reconstruction is measured.
- The inspector is 88 px from the right on desktop, reserving space for the camera toolbar. Its width remains at most 360 px.
- Valid source sun metadata is preserved; the default elevation is 35 degrees when unavailable.
- Relative scenes use a display grid and image-up indicator, without implying a geographic north or surveyed 100 m scale.
- Narrow layouts show fewer summary numerals and scroll the layer dock; the full analysis remains in the workspaces.

## Verification and limits

- Python suite: **28 passed**, 33 existing warnings, using the fresh temporary directory `D:\DepthWizard\codex-ui-qa-archive\pytest-v3-final`.
- Existing metadata JavaScript tests: **11 passed**. JavaScript syntax checks passed, including the final comparison-close change.
- Browser review at 1920×1080, 1366×768 and 390×844. The laptop rail/metric overlap found during review was fixed. No horizontal page overflow at the laptop viewport.
- Reviewed Explore, Buildings (including the known-height control), Validate, Model Compare, flood rise/drain, Present/Escape, import and export access. The 3D error legend remains ±20 m. Browser warning/error log was empty at the final desktop check.
- A PNG scene showed relative evidence, unavailable RMSE/tallest metrics, image-pixel area and a pixel scale. The optical inset and camera compass use image-up labels for unreferenced images.
- The previous v2 anchor apply/reset and export checks are recorded in `CHANGES_UI_v2.md`. This pass checked their UI access; it did not repeat calibration on the original scene or certify a newly downloaded export file.
- **45 fps on a laptop iGPU has not been established.** Adaptive AO reduction activated in the preview browser. Browser automation briefly timed out during full-resolution flood animation, then recovered; Drain and subsequent views worked. Hardware performance measurement remains necessary before claiming that target.
- This UI pass does not establish new model accuracy. Scene metrics shown in screenshots come from existing scene outputs. Concurrent model/training changes belong to separate work.

## Visual fidelity review

| Reference anchor | Implementation |
| --- | --- |
| Full-screen terrain with quiet surrounding UI | Full-bleed canvas; inspector closed at entry; floating controls |
| Physical block and soil sides | Procedural banded walls, base and top rim; actual DSM surface retained |
| Atmosphere and depth | Gradient, fog, contact shadow and a fading ground grid |
| Slim central header and icon rails | 44 px header; 56 px desktop rails with tooltips |
| Circular layers and optical map | Live layer thumbnails and existing clickable optical map |
| Cyan numeric evidence strip | Backend-derived metrics, evidence text and CRS retained |

The generated concept is a direction reference, not an accuracy reference. Its idealized sharp buildings and soil detail are not substituted for real source geometry. The implementation uses the existing optical image and estimated surface. The concept is 1672×940; the requested comparison captures are all 1920×1080.

Above-fold copy changes: the brand remains **DepthWizard**; the arrival sentence uses **estimated/relative**; evidence and scene names remain backend-driven; control labels move to tooltips; RMSE/building/height/area labels become compact small caps. No model score was invented to match the concept.

## Before / after

| View | Before | After |
| --- | --- | --- |
| Landing | [Before](docs/images/ui-v3/before-landing.jpg) | [After](docs/images/ui-v3/after-landing.jpg) |
| Explore | [Before](docs/images/ui-v3/before-explore.jpg) | [After](docs/images/ui-v3/after-explore.jpg) |
| Buildings | [Before](docs/images/ui-v3/before-buildings.jpg) | [After](docs/images/ui-v3/after-buildings.jpg) |
| Disaster, flood running | [Before](docs/images/ui-v3/before-disaster.jpg) | [After](docs/images/ui-v3/after-disaster.jpg) |
| Validate | [Before](docs/images/ui-v3/before-validate.jpg) | [After](docs/images/ui-v3/after-validate.jpg) |
| Model Compare | [Before](docs/images/ui-v3/before-model-compare.jpg) | [After](docs/images/ui-v3/after-model-compare.jpg) |

Additional images cover laptop, mobile and Present mode. The landing capture shows the settled arrival with the inspector closed.

## Local history and handoff

Section commits: `dd290440` (floating layout), `5e856aa1` (diorama and arrival), `d167e8e8` (framing and rendering cost), `432bebef` (toolbar/dock/map), `639ad177` (typography and controls), followed by the final overlap/evidence commit. No remote push or merge was performed.

**Team handoff:** UI v3 is ready for README image regeneration. Use `docs/images/ui-v3/after-*.jpg`; keep the evidence labels visible when cropping.

During a temporary C: disk-full condition, the prior disposable v2 anchor QA copy was moved intact to `D:\DepthWizard\codex-ui-qa-archive\ui-v2-anchor-qa`. This supersedes its old location in the v2 notes. The user subsequently freed additional disk space. Training datasets and model files were not moved by this UI task.
