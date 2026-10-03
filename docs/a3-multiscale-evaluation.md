# Two-resolution prediction check — 3 October 2026

Checkpoint: `da2-gamus-full`. All 30 existing GAMUS test pairs, TTA 4.
Compare the normal 0.33 m RGB prediction with a fixed 50:50 blend of that
prediction and an area-resampled 0.66 m RGB prediction, returned to the original
grid. Height labels are used only for scoring. No new training or per-tile fit.

Values below are means over tiles; the tall band is reference AGL ≥15 m.

| Prediction | RMSE (m) | MAE (m) | Bias (m) | Tall-band RMSE (m) |
|---|---:|---:|---:|---:|
| Single resolution | 2.552 | 1.431 | −0.010 | 6.313 |
| Two resolutions, equal blend | 2.560 | 1.442 | −0.006 | 6.318 |

**Decision:** retain single-resolution inference. The tested blend adds an
inference pass and worsens both overall and tall-object error. This result
does not rule out other multiscale methods; the existing relative-model global
and tiled prediction path is unchanged.

Reproduce:

```powershell
& D:\DepthWizard\venv\Scripts\python.exe scripts/evaluate_multiscale.py --root D:\DepthWizard\GAMUS --model D:\DepthWizard\checkpoints\da2-gamus-full --out D:\DepthWizard\evaluation\a3-multiscale-20261003
```

The output directory contains per-tile `results.csv` and `summary.json`.
