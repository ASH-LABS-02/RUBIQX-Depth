import sys
import json
from pathlib import Path
import numpy as np

def main():
    if len(sys.argv) < 4:
        print("Usage: python benchmark_anchors.py <base_dir> <lidar_dsm> <anchored_dir> [anchor_ids...]")
        sys.exit(1)
        
    base_dir = Path(sys.argv[1])
    lidar_path = sys.argv[2]
    anchored_dir = Path(sys.argv[3])
    anchor_ids = [int(x) for x in sys.argv[4:]]

    # Need to get the LiDAR reference on the same grid.
    from depthwizard.io import read_image, read_raster
    from depthwizard.metrics import reference_on_grid
    
    img = read_image(base_dir / "rgb.tif") if (base_dir / "rgb.tif").exists() else read_image("samples/dc_lidar/glover_park/rgb.tif")
    ref = reference_on_grid(lidar_path, img)
    dtm, _ = read_raster(base_dir / "dtm.tif")
    labels = np.load(base_dir / "building_labels.npy")
    
    ref_ndsm = ref - dtm
    
    b_ids = np.unique(labels)
    b_ids = b_ids[b_ids > 0]
    
    ref_heights = {}
    for bid in b_ids:
        mask = (labels == bid)
        h = np.nanpercentile(ref_ndsm[mask], 70)
        if not np.isnan(h):
            ref_heights[bid] = h
            
    if not anchor_ids:
        # Pick 3 anchors: mid-sized, spread out.
        # Filter 6 to 20m.
        candidates = [bid for bid, h in ref_heights.items() if 6 <= h <= 20]
        # Just pick 3 arbitrary spread out ones from the list.
        anchor_ids = [candidates[len(candidates)//4], candidates[len(candidates)//2], candidates[3*len(candidates)//4]]
        
        print("Selected 3 anchors automatically:")
        cmd = ""
        for bid in anchor_ids:
            print(f"  ID {bid}: {ref_heights[bid]:.2f} m")
            cmd += f" --anchor {bid}:{ref_heights[bid]:.2f}"
        print(f"\nRun this command:\npython -m depthwizard samples/dc_lidar/glover_park/rgb.tif --dem samples/dc_lidar/glover_park/dtm_2018_32m.tif --ref samples/dc_lidar/glover_park/lidar_dsm_2024.tif --scene urban -o {anchored_dir} {cmd}\n")
        return

    # Evaluate anchored
    if not anchored_dir.exists() or not (anchored_dir / "dsm.tif").exists():
        print(f"{anchored_dir} not ready yet.")
        return
        
    dsm, _ = read_raster(anchored_dir / "dsm.tif")
    dtm_anc, _ = read_raster(anchored_dir / "dtm.tif")
    est_ndsm = dsm - dtm_anc
    
    est_heights = {}
    for bid in b_ids:
        if bid in anchor_ids:
            continue
        mask = (labels == bid)
        h = np.nanpercentile(est_ndsm[mask], 70)
        if not np.isnan(h):
            est_heights[bid] = h

    # Compute stats for non-anchors
    errs = []
    refs = []
    ests = []
    for bid, est in est_heights.items():
        if bid in ref_heights:
            r = ref_heights[bid]
            errs.append(est - r)
            refs.append(r)
            ests.append(est)
            
    errs = np.array(errs)
    rmse = np.sqrt(np.mean(errs**2))
    bias = np.mean(errs)
    
    # Read pixel stats
    metrics = json.loads((anchored_dir / "metrics.json").read_text())
    
    out = {
        "pixel": {
            "rmse": metrics["absolute"]["rmse"],
            "mae": metrics["absolute"]["mae"]
        },
        "buildings_non_anchor": {
            "n": len(errs),
            "rmse": float(rmse),
            "bias": float(bias),
            "median_est": float(np.median(ests)),
            "median_ref": float(np.median(refs))
        }
    }
    
    print(json.dumps(out, indent=2))
    (anchored_dir / "anchor_benchmark.json").write_text(json.dumps(out, indent=2))

if __name__ == "__main__":
    main()
