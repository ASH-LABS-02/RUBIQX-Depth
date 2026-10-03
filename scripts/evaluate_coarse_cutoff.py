"""Compare fine and Copernicus surface modes at 1.5-3 m on the two DC scenes.

Only RGB is resampled; the 2024 LiDAR DSM is passed to scoring only.
Both modes calibrate with the real cached/downloaded Copernicus GLO-30.
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

import numpy as np
import rasterio
from rasterio.enums import Resampling
from rasterio.transform import Affine

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from depthwizard import pipeline


def main():
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument('--samples', type=Path, default=Path(__file__).resolve().parents[1] / 'samples/dc_lidar')
    ap.add_argument('--model', required=True)
    ap.add_argument('--out', type=Path, required=True)
    args = ap.parse_args()
    args.out.mkdir(parents=True, exist_ok=True)
    rows = []
    original_cutoff = pipeline.COARSE_GSD_M
    try:
        for scene in ['glover_park', 'capitol_hill_east']:
            folder = args.samples / scene
            for gsd in [1.5, 2.0, 2.5, 3.0]:
                image = args.out / f'{scene}-{gsd}m-rgb.tif'
                if not image.exists():
                    with rasterio.open(folder / 'rgb.tif') as src:
                        factor = gsd / abs(src.transform.a)
                        w, h = round(src.width / factor), round(src.height / factor)
                        rgb = src.read(out_shape=(src.count, h, w), resampling=Resampling.average)
                        profile = src.profile.copy()
                        profile.update(width=w, height=h, transform=src.transform * Affine.scale(src.width / w, src.height / h), compress='deflate')
                    with rasterio.open(image, 'w', **profile) as dst:
                        dst.write(rgb)
                for mode, cutoff in [('fine', float('inf')), ('surface', 0.0)]:
                    pipeline.COARSE_GSD_M = cutoff
                    out = args.out / f'{scene}-{gsd}m-{mode}'
                    meta_path = out / 'meta.json'
                    if meta_path.exists():
                        meta = json.loads(meta_path.read_text())
                    else:
                        meta = pipeline.run(image, out, reference=folder / 'lidar_dsm_2024.tif',
                                            model=args.model, scene='urban', fetch_dem=True,
                                            allow_fallback=False, tta=4, cop_scale=False)
                    metrics = meta['metrics']['absolute']
                    rows.append(dict(scene=scene, gsd=gsd, actual_gsd=meta.get('gsd_m', gsd), mode=mode,
                                     rmse=metrics['rmse'], mae=metrics['mae'], bias=metrics['bias']))
                    (args.out / 'results.json').write_text(json.dumps(rows, indent=2))
                    print(f'{scene} {gsd}m {mode}: RMSE {metrics["rmse"]:.3f} m', flush=True)
    finally:
        pipeline.COARSE_GSD_M = original_cutoff
    summary = {}
    for cutoff in [1.5, 2.0, 2.5, 3.0]:
        chosen = [r for r in rows if r['mode'] == ('surface' if r['actual_gsd'] >= cutoff else 'fine')]
        summary[str(cutoff)] = {'mean_rmse': float(np.mean([r['rmse'] for r in chosen])),
                                'mean_mae': float(np.mean([r['mae'] for r in chosen])),
                                'per_scene_rmse': {scene: float(np.mean([r['rmse'] for r in chosen if r['scene'] == scene]))
                                                   for scene in ['glover_park', 'capitol_hill_east']}}
    summary['protocol'] = 'RGB area-resampled; real Copernicus calibration; 2024 LiDAR scoring only; TTA 4; no Copernicus height-scale correction.'
    (args.out / 'summary.json').write_text(json.dumps(summary, indent=2))
    print(json.dumps(summary, indent=2), flush=True)


if __name__ == '__main__':
    main()
