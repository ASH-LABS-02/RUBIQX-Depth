import glob
import h5py
import numpy as np
import random
from PIL import Image

rgb_files = glob.glob(r"D:\DepthWizard\Urban3D_h5\images\train\*_RGB.h5")
random.shuffle(rgb_files)
selected = rgb_files[:3]

images = []
for f in selected:
    with h5py.File(f, 'r') as file:
        rgb = file["image"][()]
    
    hgt_file = f.replace('images', 'heights').replace('_RGB.h5', '_AGL.h5')
    with h5py.File(hgt_file, 'r') as file:
        agl = file["image"][()]
    
    # Normalize AGL for visualization (0-30m)
    agl[agl < 0] = 0
    agl = np.clip(agl / 30.0 * 255, 0, 255).astype(np.uint8)
    
    # Grayscale to RGB
    agl_colored = np.stack((agl,)*3, axis=-1)
    
    # Concatenate side by side
    combined = np.concatenate((rgb, agl_colored), axis=1)
    images.append(combined)

# Stack vertically
final_image = np.concatenate(images, axis=0)
Image.fromarray(final_image).save(r"D:\DepthWizard\Urban3D_h5\preview.png")
