# Coarse-image cutoff check — 3 October 2026

Checkpoint: `da2-gamus-full`, TTA 4. Area-resample the two DC RGB scenes to
1.5, 2, 2.5 and 3 m. Compare Mode C (Copernicus terrain proxy plus learned
structure) with Copernicus surface calibration. Use the real Copernicus GLO-30;
the 2024 LiDAR DSM is supplied only to scoring. No structure scale correction,
reference-derived DEM or reference regression is used for estimation.

## RMSE per scene and resolution (metres)

| Scene | GSD | Mode C / fine | Surface mode |
|---|---:|---:|---:|
| Glover Park | 1.5 | 5.019 | 7.236 |
| Glover Park | 2.0 | 4.853 | 7.133 |
| Glover Park | 2.5 | 4.929 | 7.058 |
| Glover Park | 3.0 | 4.905 | 6.987 |
| Capitol Hill East | 1.5 | 3.223 | 5.257 |
| Capitol Hill East | 2.0 | 3.779 | 5.168 |
| Capitol Hill East | 2.5 | 3.864 | 5.092 |
| Capitol Hill East | 3.0 | 3.907 | 5.025 |

## Candidate cutoffs

At or above the cutoff choose surface mode; below it choose Mode C. Each
aggregate is the mean of the eight scene/resolution RMSEs, not pooled pixels.

| Cutoff (m) | Mean RMSE (m) | Mean MAE (m) | Glover mean RMSE | Capitol mean RMSE |
|---:|---:|---:|---:|---:|
| 1.5 | 6.120 | 4.690 | 7.104 | 5.136 |
| 2.0 | 5.588 | 4.280 | 6.549 | 4.627 |
| 2.5, current | 5.130 | 3.941 | 5.979 | 4.280 |
| 3.0 | 4.710 | 3.637 | 5.447 | 3.973 |

**Decision:** 3 m is a promising candidate in this DC-only exploratory sweep,
but retain the 2.5 m production cutoff. These two LiDAR scenes are reserved
for blind scoring; selecting a production threshold from them would make
them tuning data. A separate validation dataset with independent terrain
and surface references is needed before promoting 3 m. The UI's requested
2.5 m terrain overview rule stays unchanged.

Reproduce:

```powershell
& D:\DepthWizard\venv\Scripts\python.exe scripts/evaluate_coarse_cutoff.py --model D:\DepthWizard\checkpoints\da2-gamus-full --out D:\DepthWizard\evaluation\a4-cutoff-20261003
```

The output directory contains resampled RGB, separate jobs for both modes,
`results.json` and `summary.json`. Original sample files are unchanged.
