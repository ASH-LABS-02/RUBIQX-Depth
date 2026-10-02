import os
import glob
import h5py
import numpy as np
import rasterio
from pathlib import Path

def process_urban3d():
    source_dir = Path(r"D:\DepthWizard\Urban3D")
    dest_dir = Path(r"D:\DepthWizard\Urban3D_h5")
    
    splits = {
        "train": ["01-Provisional_Train/Inputs", "04-Unused_Data/Inputs"],
        "val": ["02-Provisional_Test/Inputs"],
        "test": ["03-Sequestered_Test/Inputs"]
    }
    
    previews = []
    
    for split_name, folders in splits.items():
        print(f"Processing split: {split_name}")
        out_img_dir = dest_dir / "images" / split_name
        out_hgt_dir = dest_dir / "heights" / split_name
        out_img_dir.mkdir(parents=True, exist_ok=True)
        out_hgt_dir.mkdir(parents=True, exist_ok=True)
        
        all_agl = []
        valid_count = 0
        total_pixels = 0
        gt_3m_count = 0
        
        for folder in folders:
            rgb_files = glob.glob(str(source_dir / folder / "*_RGB.tif"))
            for rgb_path in rgb_files:
                base = rgb_path.replace("_RGB.tif", "")
                dsm_path = base + "_DSM.tif"
                dtm_path = base + "_DTM.tif"
                
                if not os.path.exists(dsm_path) or not os.path.exists(dtm_path):
                    print(f"Missing DSM or DTM for {rgb_path}")
                    continue
                
                with rasterio.open(rgb_path) as src_rgb, \
                     rasterio.open(dsm_path) as src_dsm, \
                     rasterio.open(dtm_path) as src_dtm:
                     
                    if src_rgb.shape != src_dsm.shape or src_dsm.shape != src_dtm.shape:
                        print(f"Shape mismatch: {base}")
                        continue
                    if src_rgb.crs != src_dsm.crs or src_dsm.crs != src_dtm.crs:
                        print(f"CRS mismatch: {base}")
                        continue
                    if src_rgb.transform != src_dsm.transform or src_dsm.transform != src_dtm.transform:
                        print(f"Transform mismatch: {base}")
                        continue
                        
                    rgb = src_rgb.read((1,2,3)).transpose(1, 2, 0)
                    dsm = src_dsm.read(1)
                    dtm = src_dtm.read(1)
                    
                    nodata_mask = (dsm == src_dsm.nodata) | (dtm == src_dtm.nodata) | np.isnan(dsm) | np.isnan(dtm)
                    
                    agl = dsm - dtm
                    agl[nodata_mask] = np.nan
                    agl[agl < 0] = 0
                    agl[agl > 300] = np.nan
                    
                    h, w = agl.shape
                    crops = [
                        (0, 0, 1024, 1024),
                        (0, 1024, 1024, 2048),
                        (1024, 0, 2048, 1024),
                        (1024, 1024, 2048, 2048)
                    ]
                    
                    aoi_tile = Path(base).name
                    
                    for k, (y1, x1, y2, x2) in enumerate(crops):
                        if y2 > h or x2 > w:
                            continue
                        
                        agl_crop = agl[y1:y2, x1:x2]
                        rgb_crop = rgb[y1:y2, x1:x2]
                        
                        valid = ~np.isnan(agl_crop)
                        valid_pct = valid.sum() / (1024*1024)
                        
                        if valid_pct < 0.5:
                            continue
                            
                        # stats for reporting
                        # Subsample agl for memory efficiency
                        valid_agl = agl_crop[valid]
                        if len(valid_agl) > 0:
                            all_agl.append(np.random.choice(valid_agl, size=min(10000, len(valid_agl)), replace=False))
                        
                        valid_count += valid.sum()
                        total_pixels += (1024*1024)
                        gt_3m_count += (valid_agl > 3.0).sum()
                        
                        agl_save = agl_crop.copy()
                        agl_save[~valid] = -5.0
                        
                        out_img = out_img_dir / f"{aoi_tile}_{k}_RGB.h5"
                        out_hgt = out_hgt_dir / f"{aoi_tile}_{k}_AGL.h5"
                        
                        with h5py.File(out_img, 'w') as f:
                            f.create_dataset("image", data=rgb_crop, compression="lzf")
                        with h5py.File(out_hgt, 'w') as f:
                            f.create_dataset("image", data=agl_save.astype(np.float32), compression="lzf")
                            
                        if len(previews) < 3 and np.random.rand() < 0.05:
                            previews.append((rgb_crop, agl_crop))
                            
        if len(all_agl) > 0:
            all_agl_concat = np.concatenate(all_agl)
            p50 = np.percentile(all_agl_concat, 50)
            p95 = np.percentile(all_agl_concat, 95)
            p99 = np.percentile(all_agl_concat, 99)
            print(f"Stats for {split_name}: AGL p50={p50:.2f}m, p95={p95:.2f}m, p99={p99:.2f}m")
            print(f"  % valid: {valid_count / total_pixels * 100:.2f}%")
            print(f"  % > 3m: {gt_3m_count / valid_count * 100:.2f}%")

    if previews:
        import matplotlib.pyplot as plt
        fig, axes = plt.subplots(3, 2, figsize=(10, 15))
        for i, (rgb, agl) in enumerate(previews):
            axes[i, 0].imshow(rgb)
            axes[i, 0].axis('off')
            axes[i, 1].imshow(agl, cmap='magma', vmin=0, vmax=30)
            axes[i, 1].axis('off')
        plt.tight_layout()
        plt.savefig(dest_dir / "preview.png")

if __name__ == "__main__":
    process_urban3d()
