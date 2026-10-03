# Independent benchmark and frozen scoring

Status: executable preflight/scoring workflow, 3 October 2026. A small official
NRW candidate was scored once as a diagnostic; no training run, labelled
semantic dataset, accuracy improvement or independent validation is claimed.
`evaluation/benchmark-manifest.example.json` is an intentionally incomplete
template. It must fail preflight until real sources and audit evidence replace
the placeholders.

## What this workflow checks

`scripts/validate_benchmark.py` validates explicit development and blind-test
scene groups, geographic bounds and a declared separation buffer. Crops from
one image mosaic, survey, LiDAR strip or AOI belong to the same scene group.
Development/test bounds cannot intersect; repeated input/reference content
hashes across those splits are rejected. Known training bounds cannot overlap
either split. Include the base height model, fine-tuned height model, semantic
model, height scaling, uncertainty fitting and configuration selection in the
audit. Unknown training overlap blocks an independent claim.

The manifest records a frozen checkpoint file, revision, licence and run
configuration; every asset has a SHA-256. Inputs and references require source,
licence and acquisition evidence. Date ranges describe uncertainty honestly;
do not turn a known year into a claimed exact flight date. `change_review`
records construction, vegetation season, temporal mismatch and registration
decisions. Record all prior reference use, including inspecting its errors to
choose a version or threshold. A previously exposed test set is a diagnostic
set for the next iteration.

Preflight validates declarations and supplied footprints. It cannot prove an
unreported training corpus, past reference access, an incorrect licence claim,
or that weights used to produce predictions match the declared checkpoint.
Keep the audit evidence and inference logs with the frozen configuration.
For checkpoints with multiple weight shards or optional semantic weights,
include all file hashes in the pinned configuration and retain that inventory.

An explicitly declared `benchmark_kind: "diagnostic"` permits only diagnostic
scenes and always reports `independent_validation: false`. It can retain
`training_audit.status: "incomplete"` and `acquisition: {"status": "unresolved"}`
with concrete evidence and warnings. Hashes, freeze, licence, native datum,
grid and target checks still apply. The default independent mode keeps the
complete training-audit and development/blind-test requirements. Diagnostic
mode makes provenance limits visible; it does not waive them for a blind claim.

## Protected DC references

Never fit weights, scale, anchors, masks, thresholds, uncertainty constants or
select configurations using `samples/dc_lidar/*/lidar_dsm_2024.tif`. They remain
scoring-only references. The validator recognises their resolved paths and the
hashes in `samples/dc_lidar/provenance.json`, including byte-identical renamed
copies. It rejects them as inference/calibration inputs and permits them only
under `diagnostic`. Derived/resampled copies also remain protected; their
lineage must be declared in the audit because hashes cannot infer derivation.

The current `depthwizard/uncertainty.py` explicitly says its 4 m floor and 5.5
ensemble-spread multiplier were historically fitted on the two DC evaluation
scenes. Consequently those references cannot independently validate the sigma
model. Existing DC comparisons also include repeated versions. Historical
"held out" in the benchmark document describes a scoring reference withheld
from height construction, and does not establish a new untouched benchmark
for every later model/configuration choice.

## Execute a benchmark

1. Choose real independent development and test AOIs before viewing test
   reference errors. Inventory training/selection scenes and geographic
   exclusions. Obtain imagery, independently generated surface references and
   independent rooftop/woody-canopy labels with verified permitted uses.
2. Prepare a common projected raster grid using source georeferencing. Choose
   registration, nodata, resolution and exclusion rules on development data.
   Transform vertical datums from survey/grid metadata independently of the
   height prediction. Preserve original files, preparation commands and hashes.
3. Choose the checkpoint, height scale, semantic threshold, TTA and uncertainty
   parameters on development data. Freeze the configuration and model file
   inventory. Run inference without `--ref`; keep the blind references out of
   the inference inputs. Preserve `meta.json`, the command, model inventory and
   resulting height/semantic/sigma hashes. The JSON configuration should contain
   every CLI option, environment model path, software revision and inference log.
4. Copy the template, fill the provenance, exact real AOI bounds, training audit
   and asset hashes, and run preflight. Keep source acquisition ranges and dates
   of downloads/conversions distinct. Score blind outputs once, retain the
   manifest and raw score JSON, and treat the set as exposed for subsequent tuning.

```powershell
$py = 'D:\DepthWizard\venv\Scripts\python.exe'
# The supplied example intentionally returns nonzero with actionable errors.
& $py scripts/validate_benchmark.py evaluation/benchmark-manifest.example.json
# For a completed real manifest, first review metadata, then verify file hashes.
& $py scripts/validate_benchmark.py evaluation/independent.json --check-files --out evaluation/preflight.json
# This reads frozen predictions and references; it never runs or fits a model.
& $py scripts/validate_benchmark.py evaluation/independent.json --score --split blind_test --out evaluation/blind-scores.json
```

All paths are relative to the manifest. `--score` implies file/hash verification
and refuses scoring if any preflight error remains. The scorer requires exact
matching single-band GeoTIFF grids (shape, CRS and transform), including labels
and sigma; it performs no automatic resampling or registration. It uses the
existing `_core` height metrics and `classification_metrics` class metrics.
It checks the declared WGS84 scene bounds against the actual image extent and
prediction coverage at a 1e-5 degree tolerance during scoring.
Nodata/NaN height pixels are excluded; valid fraction and counts are retained.
Fewer than ten valid pixels produces no slice metric (null), never zero error.
It emits per-scene scores, without pixel pooling, affine fitting or fabricated
aggregates. Compare scene means and spatially independent uncertainty intervals
in a separate, explicitly specified analysis.

Supported assets: `image`, `prediction`, `reference`, `calibration_dem`, `gcp`,
`anchors`, `reference_labels`, `reference_ndsm`, `semantic_prediction`,
`uncertainty`. Declare each calibration asset and source provenance; do not
hide a reference raster under an unspecified role. For non-raster GCP/anchor
assets, acquisition means the survey/evidence dates.

## DSM, nDSM and tall-object slices

Set `protocol.height_target` to `DSM` for absolute surface elevation or `nDSM`
for height above ground. Both prediction and reference must declare that exact
target and metre units. GAMUS AGL is nDSM; it cannot validate an absolute DSM.
An nDSM declares `vertical_datum: "AGL"`. A DSM requires the same explicit
datum on prediction and reference and documented source/transform evidence.

An optional `reference_ndsm` is an independently prepared DSM-minus-surveyed-DTM
height raster, with `height_type: "nDSM"`, `units: "metre"`,
`vertical_datum: "AGL"`, `usage: "scoring_only"` and the same full reference
provenance fields. Its hash and grid are checked. Do not subtract the predicted
terrain from reference DSM to define truth, or infer true ground with a
morphological opening and call it measured AGL. The older pipeline
`structure_ndsm`/height-band scores use morphological ground estimates; they
are useful diagnostics with a different meaning.

The fixed `protocol.tall_threshold_m` (template: 15 m) is chosen before test
scoring. For nDSM, true AGL defines tall objects; for DSM this requires
`reference_ndsm`. The scorer reports tall-object height error, and, when labels
exist, separate tall-building and tall-canopy error. `height_by_reference_class`
reports all rooftop and woody-canopy pixel error. For per-building attributes,
provide independent footprint/instance labels and a declared object-matching
rule in a future extension; current slices are pixel metrics, not object recall
or building height validation.

`reference_labels` needs `label_schema: "depthwizard_canonical_v1"`, its own
source/licence/acquisition evidence and `usage: "scoring_only"`. Codes are
0/255 ignore, 1 rooftop/building, 2 woody canopy, 3 ground/low vegetation,
4 water, 5 road. Prepare categorical labels with nearest-neighbour mapping on
the frozen grid; document any dataset-to-canonical mapping and mark unsupported
classes ignore. `semantic_prediction` is the saved model class GeoTIFF. Unknown
predictions count as misses on known truth. IoU/precision/recall and classified
coverage score the segmentation independently of height errors. The existing
`compare_semantic.py` can generate masks and cached-detector comparison panels;
its PNG/TIFF path checks dimensions but does not independently register labels.

`uncertainty` needs metre units and `calibration_provenance`, plus its file hash.
Frozen scoring reports raw-error coverage at one/two sigma, including bias,
with no fitted parameter or error-centering. `uncertainty_coverage.py` now also
reports raw coverage and separately labelled bias-centred diagnostics; it no
longer derives a fitted sigma multiplier. Its legacy resampling path lacks the
strict datum/split gate, so use this manifest workflow for benchmark claims.
Ensemble disagreement ranks consistency; it does not by itself capture shared
bias, DEM error or domain shift. Fit a future sigma model on independent
development scenes only and assess coverage by landscape/calibration route
on the frozen test set. Current provisional DC constants remain unchanged.

## Actual datum support and remaining work

The pipeline reads supplied DEM `VERTICAL_DATUM` tags, can record explicit
datum metadata, and exports EGM2008/EGM96 compound CRS where supported. Raw
WGS84 ellipsoidal GCP heights can convert to EGM2008 using the PROJ geoid grid;
the code fails if that conversion cannot be performed and rejects combining
those converted GCPs with a DEM not explicitly labelled EGM2008.

Calibration and reference scoring do not reconcile arbitrary raster datums.
`--vertical-datum` is a declaration, not a height transformation.
`metrics.reference_on_grid` reprojects horizontally and does not reconcile
vertical datums. The DC preparer writes horizontal EPSG:26985 but discards
source vertical tags; its provenance alone does not resolve the reference/DTM
vertical datum. Establish datum/unit metadata from the source survey and
reprepare/transform those assets before an absolute accuracy claim. Do not
estimate a datum offset by fitting the protected DSM reference.

For explicitly documented ellipsoidal/EGM96/EGM2008 absolute rasters, use the
separate `scripts/convert_vertical_datum.py` preprocessing workflow described
in [vertical-datum.md](vertical-datum.md). It requires real PROJ transformation
grids and refuses unavailable/ballpark conversions. nDSM/AGL is a height
difference and must not undergo an absolute datum conversion. The supported
converter datum list does not establish a DHHN2016/NAVD88 conversion.

## Source candidates to investigate

These are data access routes. The bounded NRW candidate below was downloaded;
the other routes were inspected without dataset downloads. No route is yet a
verified independent benchmark pair for the current checkpoint.
Confirm the specific AOI, dates, spectral bands, licences and training overlap
before choosing scenes. Start with a few manageable AOIs in new geography,
including tall buildings and leaf-on/leaf-off canopy; do not begin with a full
national or multi-gigabyte dataset download.

| Route | Useful evidence | Limits to resolve |
|---|---|---|
| [Geobasis NRW source products](https://www.opengeodata.nrw.de/produkte/geobasis/hm/) and [orthophotos](https://www.opengeodata.nrw.de/produkte/geobasis/lusat/akt/dop/) | ALS DOM1/DSM, DGM1/DTM and RGBI orthophotos; official WCS subsets are practical for small new German AOIs | All three saved WCS capabilities explicitly use [DL-DE-Zero 2.0](https://www.govdata.de/dl-de/zero-2-0). Native DSM/DTM datum was verified for one diagnostic. Specific dates and complete training overlap remain unresolved; no semantic labels are included |
| [USGS 3DEP source products](https://www.usgs.gov/3d-elevation-program/about-3dep-products-services) | Lidar point clouds and original-resolution elevation products; inspect project survey metadata and classifications | A bare-earth DEM is not a canopy/roof DSM. Derive or obtain a first-return surface independently, locate appropriate imagery, and audit dates/datum/licence for the actual project |
| [NOAA Digital Coast Data Access Viewer](https://coast.noaa.gov/digitalcoast/tools/dav.html) | Search lidar, imagery and land cover by geography; inspect dataset metadata and projection/datum options | Availability and acquisition dates differ. [Coastal lidar metadata](https://coast.noaa.gov/digitalcoast/data/coastallidar.html) links survey reports; NOAA does not guarantee reported vertical/horizontal accuracies |
| [ISPRS semantic benchmark](https://www.isprs.org/resources/datasets/benchmarks/UrbanSemLab/Default.aspx) | Building/tree semantic labels and surface products; obtain actual terms and select a compatible imagery variant | [Vaihingen](https://www.isprs.org/resources/datasets/benchmarks/UrbanSemLab/2d-sem-label-vaihingen.aspx) is 9 cm NIR/red/green, with a photogrammetric DSM. This is not RGB satellite or independent LiDAR ground truth; it is also finer than the model's documented scale range |
| [LoveDA official repository](https://github.com/Junjue-Wang/LoveDA) | RGB land-cover labels and published train/validation/test organization; useful for class conversion/development audits | The current semantic candidate was reportedly fine-tuned on LoveDA and lacks a publisher split/licence card. LoveDA is restricted to academic use under stated terms; it supplies forest semantics, not individual-tree or height truth. It cannot establish independence for that candidate without the missing training audit |

## Bounded NRW candidate receipt

Raw files are outside the repository at
`D:/DepthWizard/independent-candidates/nrw-cologne-256m-20261003/`:
`rgbi.tif` (263,116 bytes), `dsm.tif` (262,748 bytes), and `dtm.tif`
(105,513 bytes), total 631,377 bytes. They cover the preselected Cologne
EPSG:25832 bounds `[356750,5645250,357006,5645506]`, on an identical 256 × 256
1 m grid. The image contains documented R/G/B/near-infrared bands; use the first
three as RGB. Native DOP is 0.1 m and the WCS requested a 1 m subset; retain that
resampling provenance. Native DOM1 and DGM1 are 1 m.

The external `acquire.py` preserves the exact bounded GetCoverage URLs,
capability/description XML and SHA-256 hashes in `provenance.json`. A receipt is
also retained in `evaluation/nrw-candidate-provenance.json`. Reproduce retrieval
in the external candidate directory (services may return newer imagery later;
compare hashes before reusing a frozen sample):

```powershell
& $py D:/DepthWizard/independent-candidates/nrw-cologne-256m-20261003/acquire.py
```

The three official endpoints are
[DOP/RGBI](https://www.wcs.nrw.de/geobasis/wcs_nw_dop?SERVICE=WCS&VERSION=2.0.1&REQUEST=GetCapabilities),
[DOM/DSM](https://www.wcs.nrw.de/geobasis/wcs_nw_dom?SERVICE=WCS&VERSION=2.0.1&REQUEST=GetCapabilities),
and [DGM/DTM](https://www.wcs.nrw.de/geobasis/wcs_nw_dgm?SERVICE=WCS&VERSION=2.0.1&REQUEST=GetCapabilities).
Each capability Fees section explicitly states the Zero 2.0 licence. Official
[DOM product metadata](https://www.govdata.de/suche/daten/digitales-oberflachenmodell-1-nwf29e4?ids=ae35f25d-5682-47ca-ac81-f80c48f4f779)
describes its ALS source and [DOP product metadata](https://www.govdata.de/suche/daten/digitale-orthophotos-nwadd54?ids=852bf419-89a0-4157-8d4b-b04ff63f7b9c)
describes the imagery. The saved DTM catalogue contains reference-system codes
25832 and 7837, while the downloaded TIFFs retain only horizontal 25832. This
was supplemented with the official [DOM1 product page](https://www.bezreg-koeln.nrw.de/geobasis-nrw/produkte-und-dienste/hoehenmodelle/digitale-oberflaechenmodelle/digitales)
and [DGM1 product page](https://www.bezreg-koeln.nrw.de/geobasis-nrw/produkte-und-dienste/hoehenmodelle/digitale-gelaendemodelle/digitales-gelaendemodell),
both explicitly declaring DHHN2016/EPSG:7837. That vertical CRS defines metre
heights. The source HTML snapshots and hashes are retained externally. No
vertical transformation or fitted datum offset was needed.

After that datum verification, one ordinary v6a inference was run with RGB
bands 1–3 and the supplied 1 m DGM1 terrain, followed by one frozen diagnostic
score against DOM1. No height reference was passed to inference; automatic
anchors, Copernicus fetch/scale and semantic inference were disabled. Model,
configuration, source code and input/output hashes were frozen before scoring.
The score showed overall DSM RMSE **6.29 m** and RMSE **16.49 m** on reference
AGL ≥15 m pixels, with **−14.46 m** bias on that tall-object slice. The settings
and weights were not changed to improve those errors.

This is new geography relative to declared GAMUS/Urban3D fine-tuning domains,
but incomplete base pretraining provenance and unresolved local source dates
prevent an independent accuracy claim. The supplied high-resolution DTM and
DOM may share source LiDAR lineage, so this is a terrain-conditioned surface
diagnostic. The sample is now exposed and remains diagnostic. See
[nrw-cologne-diagnostic.md](nrw-cologne-diagnostic.md) for raw results, controls,
provenance and reproduction commands. Do not convert DHHN2016 using an
unrelated EGM grid or use these errors to select a supposedly blind result.

Native dataset licences and model-weight licences are separate. The current
semantic publisher's missing licence/split evidence remains unresolved; a
dataset licence does not grant a licence to redistribute those weights.
