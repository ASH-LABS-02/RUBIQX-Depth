"""Export a (fine-tuned) Depth Anything V2 checkpoint to ONNX for faster CPU /
TensorRT inference.

    python scripts/export_onnx.py D:\\DepthWizard\\checkpoints\\da2-gamus-full -o models/da2-gamus.onnx

Verify with onnxruntime; DepthWizard's Python pipeline keeps using PyTorch by
default, the ONNX file is for deployment targets without PyTorch.
"""
import argparse

import torch
from transformers import AutoModelForDepthEstimation


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("checkpoint")
    ap.add_argument("-o", "--out", default="models/depthwizard.onnx")
    ap.add_argument("--size", type=int, default=518)
    a = ap.parse_args()
    model = AutoModelForDepthEstimation.from_pretrained(a.checkpoint).eval()

    class Wrap(torch.nn.Module):
        def __init__(self, m):
            super().__init__()
            self.m = m

        def forward(self, x):
            return self.m(pixel_values=x).predicted_depth

    x = torch.randn(1, 3, a.size, a.size)
    torch.onnx.export(Wrap(model), x, a.out, input_names=["pixel_values"], output_names=["depth"],
                      dynamic_axes={"pixel_values": {0: "batch", 2: "h", 3: "w"}, "depth": {0: "batch", 1: "h", 2: "w"}},
                      opset_version=17)
    print("wrote", a.out)


if __name__ == "__main__":
    main()
