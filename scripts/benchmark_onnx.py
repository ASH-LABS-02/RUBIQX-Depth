"""Matched CPU export, weight quantization and unfitted development parity receipt."""
import argparse
import hashlib
import json
import os
from pathlib import Path
import sys
import time

import numpy as np
import torch
from PIL import Image
from transformers import AutoModelForDepthEstimation, AutoImageProcessor


def main():
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("checkpoint",type=Path)
    ap.add_argument("--images",type=Path,nargs="+",required=True)
    ap.add_argument("--out",type=Path,required=True)
    ap.add_argument("--threads",type=int,default=4)
    ap.add_argument("--repeats",type=int,default=3)
    ap.add_argument("--gsd",type=float,default=.65)
    a = ap.parse_args()
    import onnxruntime as ort
    from onnxruntime.quantization import quantize_dynamic,QuantType
    torch.set_num_threads(a.threads)
    a.out.mkdir(parents=True,exist_ok=True)
    model = AutoModelForDepthEstimation.from_pretrained(a.checkpoint).eval()
    processor = AutoImageProcessor.from_pretrained(a.checkpoint)
    class Wrap(torch.nn.Module):
        def __init__(self,model): super().__init__(); self.model=model
        def forward(self,x): return self.model(pixel_values=x).predicted_depth
    network = Wrap(model)
    paths = [a.out/"float.onnx",a.out/"int8-matmul.onnx"]
    if not paths[0].is_file():
        torch.onnx.export(network,torch.zeros(1,3,518,518),paths[0],input_names=["pixel_values"],
            output_names=["depth"],dynamic_axes={"pixel_values":{0:"batch"},"depth":{0:"batch"}},opset_version=17,dynamo=False)
    if not paths[1].is_file():
        quantize_dynamic(str(paths[0]),str(paths[1]),weight_type=QuantType.QInt8,
                         op_types_to_quantize=["MatMul","Gemm"])
    options = ort.SessionOptions(); options.intra_op_num_threads=a.threads; options.inter_op_num_threads=1
    sessions = [ort.InferenceSession(str(p),options,providers=["CPUExecutionProvider"]) for p in paths]
    data = [processor(images=Image.open(p).convert("RGB").resize((518,518)),return_tensors="pt")["pixel_values"] for p in a.images]
    engines = {"torch-fp32":lambda x:network(x).detach().numpy(),
               "onnx-fp32":lambda x:sessions[0].run(None,{"pixel_values":x.numpy()})[0],
               "onnx-int8-matmul":lambda x:sessions[1].run(None,{"pixel_values":x.numpy()})[0]}
    reference = []
    report = {"threads":a.threads,"gsd_m":a.gsd,"scope":"fixed 518 input; CPU forward timing excludes shared preprocessing, development parity only",
              "model_sha256":hashlib.sha256((a.checkpoint/"model.safetensors").read_bytes()).hexdigest(),"engines":{}}
    with torch.inference_mode():
        for name,forward in engines.items():
            forward(data[0])  # warmup excluded
            times=[]; outputs=[]
            for x in data:
                for repeat in range(a.repeats):
                    start=time.perf_counter(); output=forward(x); times.append(time.perf_counter()-start)
                outputs.append(output)
            if name=="torch-fp32": reference=outputs
            diff=np.concatenate([(p-r).ravel() for p,r in zip(outputs,reference)])*a.gsd
            row={"median_forward_s":float(np.median(times)),"p95_forward_s":float(np.percentile(times,95)),
                 "metric_parity_mae_m":float(np.mean(np.abs(diff))),"metric_parity_p99_m":float(np.percentile(np.abs(diff),99)),
                 "parity_max_m":float(np.max(np.abs(diff))),"parity_rmse_m":float(np.sqrt(np.mean(diff**2))),
                 "samples":len(data),"repeats":a.repeats}
            row["speedup_vs_torch"]=report["engines"].get("torch-fp32",row)["median_forward_s"]/row["median_forward_s"]
            row["accepted"] = name != "torch-fp32" and row["metric_parity_mae_m"] <= .05 and row["metric_parity_p99_m"] <= .25 and row["speedup_vs_torch"] > 1.1
            report["engines"][name]=row
            (a.out/"benchmark.json").write_text(json.dumps(report,indent=2))
            print(name,row,flush=True)
    report["production_enabled"]=False
    report["deployment_gate"]="Raw forward parity does not establish DSM/reference accuracy or dynamic-shape pipeline compatibility"
    (a.out/"benchmark.json").write_text(json.dumps(report,indent=2))


if __name__=="__main__": main()
