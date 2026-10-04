# Contributing

[Project overview](README.md) · [Architecture](docs/ARCHITECTURE.md)

## Development setup

Use Python 3.12 for the documented setup and CI environment. Create a virtual
environment, install `requirements.txt`, and start `python run.py`. Configure a
fine-tuned checkpoint through `DEPTHWIZARD_CHECKPOINT` or place its Hugging Face
checkpoint directory in `models/da2-gamus-full`. Do not commit model weights or
full training datasets. `DEPTHWIZARD_TRAINING_ROOT` can identify a separate local
training directory; see [TRAINING.md](TRAINING.md).

```powershell
python -m venv .venv
.venv/Scripts/python -m pip install -r requirements.txt
.venv/Scripts/python -m pytest -q
```

Keep changes focused and document evidence, provenance and limitations. Run the
relevant existing checks when changing implementation. The last recorded complete
Python run on 4 October 2026 passed 90 tests; this is a dated result, not a guarantee
for an edited checkout. CI configuration is in `.github/workflows/ci.yml`.

## Evaluation integrity

Never train, fit calibration, tune parameters or select checkpoints against
`samples/dc_lidar/*/lidar_dsm_2024.tif`. Those rasters are reserved for scoring.
These related-domain scenes have historical inspection and are not fresh blind
geographic holdouts. Keep training, validation and independent test manifests
separate. Disclose shared-survey calibration, date mismatch, licensing and possible
geographic overlap. See [independent evaluation](docs/independent-benchmark.md).

Changes intended to improve accuracy need measured before/after results and must
report regressions by height band/domain. Keep failed experiments documented and
experimental paths off by default until their acceptance criteria are met.

## Repository data and releases

Some gallery jobs and small test samples are intentionally tracked; new processing
jobs are generally ignored. Preserve unrelated local data changes and stage only
files belonging to your task. Do not add private imagery, credentials or large
checkpoints. Synthetic scenes test plumbing, not real-world accuracy.

Repository code is MIT licensed. Dataset and model licences are separate; do not
assume the code licence permits redistribution of all training assets. Experimental
semantic weights require verified licensing and provenance before production use.

Before a release, review [release readiness](docs/release-readiness.md), update the
README only with verified claims, and identify the deployed revision separately
from the source revision. Local passing tests do not prove live AWS availability.
