"""Pre-download Depth Anything V2 weights so the app runs fully offline later.
    python scripts/download_models.py small base
"""
import os
import sys
from pathlib import Path
from transformers import AutoImageProcessor, AutoModelForDepthEstimation
MODELS = {"small": "depth-anything/Depth-Anything-V2-Small-hf",
          "base": "depth-anything/Depth-Anything-V2-Base-hf",
          "large": "depth-anything/Depth-Anything-V2-Large-hf"}
CACHE = Path(os.environ.get("DEPTHWIZARD_MODEL_CACHE",
                            Path(__file__).resolve().parents[1] / "models" / "cache"))
CACHE.mkdir(parents=True, exist_ok=True)
for k in (sys.argv[1:] or ["small"]):
    n = MODELS.get(k, k)
    print("downloading", n)
    AutoImageProcessor.from_pretrained(n, cache_dir=CACHE)
    AutoModelForDepthEstimation.from_pretrained(n, cache_dir=CACHE)
print(f"done – weights cached in {CACHE}")
