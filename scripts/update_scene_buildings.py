import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from PIL import Image
import numpy as np
import rasterio
from scipy import ndimage
from depthwizard.buildings import extract_buildings

for scene in ['dc-glover-park', 'dc-capitol-hill', 'quesenbank-north-calibrated-v2', 'quesenbank-south-calibrated-v2']:
    p = Path('data/jobs') / scene
    if not (p / 'dsm.tif').exists():
        continue
    with rasterio.open(p / 'dsm.tif') as src:
        dsm = src.read(1).astype(np.float32)
    meta = json.loads((p / 'viewer' / 'meta.json').read_text())
    gsd = float(meta.get('gsd_m', 0.5))
    gw, gh = float(meta['ground_w_m']), float(meta['ground_h_m'])

    filter_size = max(3, int(round(60.0 / gsd)))
    ground = ndimage.grey_opening(dsm, size=(filter_size, filter_size))
    dtm = ndimage.gaussian_filter(ground, max(1.0, 15.0 / gsd))

    mw, mh = int(meta['grid_w']), int(meta['grid_h'])
    dtm_down = np.asarray(Image.fromarray(dtm).resize((mw, mh), Image.BILINEAR)).astype('<f4')
    dtm_down.tofile(p / 'viewer' / 'dtm.bin')

    diff = np.abs(dsm - ndimage.gaussian_filter(dsm, 2.0))
    conf = np.clip(1.0 - (diff / max(float(np.percentile(diff, 95)), 1e-4)), 0.0, 1.0).astype(np.float32)
    conf_down = np.asarray(Image.fromarray(conf).resize((mw, mh), Image.BILINEAR)).astype('<f4')
    conf_down.tofile(p / 'viewer' / 'confidence.bin')

    b_data = extract_buildings(dsm, dtm=dtm, gsd=gsd, world_w=gw, world_h=gh)
    (p / 'viewer' / 'buildings.json').write_text(json.dumps(b_data, indent=2))

    meta['has_dtm'] = True
    meta['has_confidence'] = True
    meta['buildings_count'] = b_data['count']
    meta['vertical_datum'] = 'EGM2008'
    (p / 'viewer' / 'meta.json').write_text(json.dumps(meta, indent=2))
    print(f"{scene}: {b_data['count']} buildings extracted")
