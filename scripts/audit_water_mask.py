"""Audit predicted water against aligned human/source labels; never fit thresholds."""
import argparse
import json
from pathlib import Path
import sys
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import numpy as np
from PIL import Image
from depthwizard.analysis import water_mask
from depthwizard.io import read_image
from depthwizard.validation import categorical


def main():
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("image", type=Path)
    ap.add_argument("--labels", type=Path, required=True, help="0/255 ignore, 1 nonwater, 4 water; exact RGB grid")
    ap.add_argument("--gsd", type=float, default=1)
    ap.add_argument("--label-source", required=True)
    ap.add_argument("--out", type=Path, required=True)
    a = ap.parse_args()
    img = read_image(a.image)
    labels = np.array(Image.open(a.labels))
    if labels.shape != img.shape or not np.isin(labels, [0, 1, 4, 255]).all():
        raise ValueError("Water labels must share the RGB grid and contain only 0,1,4,255")
    mask = water_mask(img.rgb, img.pixel_size_m or a.gsd)
    a.out.mkdir(parents=True, exist_ok=True)
    Image.fromarray(mask.astype(np.uint8) * 255).save(a.out / "water-mask.png")
    receipt = {"label_source": a.label_source, "policy": "existing RGB smoothness heuristic",
        "scores": categorical(np.where(mask, 4, 1), labels, classes=(1, 4)),
        "labelled_fraction": float(((labels != 0) & (labels != 255)).mean()),
        "fitted": False, "input": str(a.image.resolve())}
    (a.out / "score.json").write_text(json.dumps(receipt, indent=2), encoding="utf-8")
    print(json.dumps(receipt, indent=2))


if __name__ == "__main__":
    main()
