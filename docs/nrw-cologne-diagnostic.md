# One frozen NRW surface-height diagnostic

On 3 October 2026, the current local v6a checkpoint was run once on a small
official Cologne RGB/terrain subset. Frozen outputs were scored once against
the official LiDAR surface DSM. The run exposes a substantial tall-object
underestimate. It is a diagnostic, with `independent_validation: false`.

## What was fixed before scoring

The 256 × 256 pixel, 1 m subset uses the EPSG:25832 bounds
`[356750,5645250,357006,5645506]`. Three WCS raster responses total
631,377 bytes and are outside the repository in
`D:/DepthWizard/independent-candidates/nrw-cologne-256m-20261003/`.
The image is the official RGBI product; only bands 1, 2 and 3 (R/G/B) were
copied to the inference input. NIR was excluded. The native DOP is 0.1 m and
the requested WCS subset is 1 m; that preparation remains part of provenance.

The official [DOM1 product page](https://www.bezreg-koeln.nrw.de/geobasis-nrw/produkte-und-dienste/hoehenmodelle/digitale-oberflaechenmodelle/digitales)
and [DGM1 product page](https://www.bezreg-koeln.nrw.de/geobasis-nrw/produkte-und-dienste/hoehenmodelle/digitale-gelaendemodelle/digitales-gelaendemodell)
both declare DHHN2016 heights, EPSG:7837, with metre units. The DGM1 catalogue
also identifies EPSG:7837. Copied terrain values were tagged with the verified
source datum; no vertical transformation, constant offset fit or reference
alignment was performed. All three WCS capability documents explicitly state
[DL-DE-Zero 2.0](https://www.govdata.de/dl-de/zero-2-0).

The frozen checkpoint is `D:/DepthWizard/checkpoints/da2-gamus-full`, whose
configuration records the v5→v6a Urban3D training ancestry. Full weight SHA-256:
`3f8d8835ad8082adb0050db689484185ab1787f617c0221ef0a9b07c74146272`.
`frozen-inference.json` pins every checkpoint file, all `depthwizard/*.py`
source files (including `pipeline.py` and `__main__.py`), input hashes and
configuration. The base weights are labelled [CC BY-NC 4.0 by the publisher](https://huggingface.co/depth-anything/Depth-Anything-V2-Base-hf/tree/main);
the local fine-tune remains a research derivative.

The public pipeline used `scene=urban`, four TTA passes, CUDA, supplied
`dem_kind=terrain`, verified native DHHN2016 metadata and learned metric scale.
It had no `reference`, GCPs, height anchors, semantic checkpoint, fallback,
DEM fetch, Copernicus height scale or automatic anchors. Inference took
**18.18 s**. Its outputs, hashes and metadata were retained before surface
reference values were read for scoring. The normal RGB water heuristic
flattened 21.2% of the tile to terrain; that behavior was retained and no
threshold was adjusted after inspecting errors. Five extracted building
candidates are a pipeline diagnostic count, not five validated buildings.

## Frozen scores

The scorer required identical georeferenced prediction/reference grids and
verified the actual image/prediction extent against the declared scene bounds.
All 65,536 pixels had finite prediction/reference values. No affine alignment,
registration, sigma fitting or reference-derived model selection was performed.

| Measure | All DSM pixels | Truth AGL ≥15 m slice |
|---|---:|---:|
| Pixels | 65,536 | 8,613 |
| RMSE | 6.2855 m | 16.4861 m |
| MAE | 2.5953 m | 14.4594 m |
| Mean bias (prediction − truth) | −2.1966 m | −14.4594 m |
| Pearson r | 0.7830 | 0.6552 |
| Within 1 m | 70.29% | 0.0116% |
| Within 5 m | 83.97% | 7.6861% |

All-pixel 95th-percentile absolute error was **14.9580 m**. The fixed 15 m
threshold was set before scoring. The reference slice uses native DOM1 minus
source DGM1, prepared only after the prediction was frozen, without clipping
or fitting. It contains tall objects; independent rooftop/canopy class labels
were unavailable, so separate building/tree accuracy is not claimed.

The existing, unchanged provisional sigma model covered **85.51%** of raw
errors within one sigma and **96.63%** within two sigma. These include bias and
are unadjusted observations on one terrain-conditioned tile. The constants
were historically fitted on two DC scenes and were not recalibrated here.
Overall coverage and ground-dominated pixel fractions do not establish reliable
intervals for tall objects or a new geographic domain.

## Limits and retained artifacts

Specific local imagery and LiDAR acquisition dates remain unresolved. The
source products may represent different construction or vegetation seasons.
The DGM1 terrain and DOM1 surface may share source LiDAR survey lineage, so
surface scores are conditional on the supplied high-resolution terrain and
do not test independently estimated terrain elevation. Complete base-model
pretraining AOI provenance is unavailable. One tiny, now exposed AOI cannot
establish scene-disjoint independent generalization or a performance gain.
No settings, weights or scale were changed to improve these scores.

Small repository records:

- `evaluation/nrw-candidate-provenance.json`: request URLs, raw hashes, native
  datum evidence, controls, runtime versions and external artifact paths.
- `evaluation/nrw-cologne-diagnostic-manifest.json`: explicit diagnostic mode,
  incomplete training audit, unresolved acquisition ranges and frozen assets.
- `evaluation/nrw-cologne-diagnostic-scores.json`: raw numeric results, warnings
  and `independent_validation: false`. Citation URLs were corrected after
  scoring; the unchanged original scorer output and its hash remain external
  as `diagnostic-scores.raw.json`.

External files include saved WCS metadata/catalogues and official product HTML
snapshots, `acquire.py`, `run_diagnostic.py`,
`prepare_diagnostic_manifest.py`, `frozen-inference.json`, `inference.log`,
`frozen-output-inventory.json`, and `inference-v6a/` rasters/viewer assets.
The inference runner refuses to overwrite its retained output directory.

To inspect or rescore those retained frozen outputs:

```powershell
$py = 'D:\DepthWizard\venv\Scripts\python.exe'
& $py scripts/validate_benchmark.py evaluation/nrw-cologne-diagnostic-manifest.json --check-files --out D:/DepthWizard/independent-candidates/nrw-cologne-256m-20261003/inspection-preflight.json
& $py scripts/validate_benchmark.py evaluation/nrw-cologne-diagnostic-manifest.json --score --split diagnostic --out D:/DepthWizard/independent-candidates/nrw-cologne-256m-20261003/rescore.json
```

This reuses existing predictions; it does not run inference or tune anything.
The current model directory must still match its full hash inventory before a
new inference is considered equivalent. Future accuracy work needs separate
development AOIs, resolved temporal/datum provenance, spatial training-overlap
audit and independent rooftop/woody-canopy labels before a new blind test.
