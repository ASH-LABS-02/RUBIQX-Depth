"""Fine-tune Depth Anything V2 for overhead height on GAMUS (or any RGB + height dataset).

This closes the natural-image -> nadir-satellite domain gap. The fine-tuned
model still predicts *relative* height (scale-and-shift-invariant loss), so
it drops straight into the existing calibration step:

    python -m depthwizard image.tif --dem dem.tif --model checkpoints/da2-gamus

Data layout: any folder where RGB and height rasters can be paired by file
stem. GAMUS HDF5 files are supported directly:
    --rgb "GAMUS/images/train/*.h5" --height "GAMUS/heights/train/*.h5"
Height files: HDF5/PNG/TIFF; set --height-scale if needed. Non-finite and
GeoTIFF nodata pixels are masked. Exact -5 values in GAMUS HDF5 are voids.

GPU strongly recommended; use batch 1 and gradient accumulation 4 on 8 GB VRAM.

v2 options (all off by default, so the original recipe is unchanged):
  --model base                 Depth Anything V2 Base instead of Small
  --target metric              add a metric loss so the network learns real
                               heights (metres = 1.0 x network-pixel size x
                               output); no fitted 0.674 constant needed
  --net-gsd 0.65               train at the same ground resolution the app
                               runs at (GAMUS 0.33 m tiles are resampled)
  --scale-jitter 0.15          +-15 % random resolution around --net-gsd
  --tall-weight 0.5            up-weight tall pixels in the metric loss
  --sat-aug                    satellite-style blur, haze and noise
  --select absolute            keep the checkpoint with the best *absolute*
                               (unfitted) validation error
"""
import argparse
import glob
import json
import math
import os
import random
import re
import time
from pathlib import Path

import numpy as np
import torch
import torch.nn.functional as F
from PIL import Image
from torch.utils.data import DataLoader, Dataset

MODEL_ALIASES = {"small": "depth-anything/Depth-Anything-V2-Small-hf",
                 "base": "depth-anything/Depth-Anything-V2-Base-hf",
                 "large": "depth-anything/Depth-Anything-V2-Large-hf"}
LEGACY_PIXEL_HEIGHT = 0.674   # fitted constant for SSI-only checkpoints (depth.py)
MEAN = torch.tensor([0.485, 0.456, 0.406]).view(3, 1, 1)
STD = torch.tensor([0.229, 0.224, 0.225]).view(3, 1, 1)


def read_height(path):
    if str(path).lower().endswith(".h5"):
        import h5py
        with h5py.File(path, "r") as file:
            height = file["image"][()].astype(np.float32)
        height[height == -5.0] = np.nan
        return height
    if str(path).lower().endswith((".tif", ".tiff")):
        import rasterio
        with rasterio.open(path) as s:
            a = s.read(1).astype(np.float32)
            if s.nodata is not None:
                a[a == s.nodata] = np.nan
            return a
    return np.asarray(Image.open(path)).astype(np.float32)


def read_rgb(path):
    if str(path).lower().endswith(".h5"):
        import h5py
        with h5py.File(path, "r") as file:
            return file["image"][()].astype(np.uint8)
    return np.asarray(Image.open(path).convert("RGB"))


class Pairs(Dataset):
    def __init__(self, rgb_glob, h_glob, size=518, scale=1.0, train=True,
                 limit=0, seed=42, gsd=0.33, net_gsd=0.0, scale_jitter=0.0, sat_aug=False,
                 res_range=None):
        rgbs = {Path(p).stem: p for p in glob.glob(rgb_glob)}
        hs = {Path(p).stem: p for p in glob.glob(h_glob)}
        # GAMUS uses both xxx_RGB/xxx_AGL and xxx_IMG/xxx_AGL.
        norm = lambda s: re.sub(r"_(rgb|img|agl|height|ndsm)$", "", s.lower())
        hn = {norm(k): v for k, v in hs.items()}
        self.items = sorted((v, hn[norm(k)]) for k, v in rgbs.items() if norm(k) in hn)
        if not self.items:
            raise SystemExit("no RGB/height pairs matched – check the globs")
        if limit and len(self.items) > limit:
            self.items = sorted(random.Random(seed).sample(self.items, limit))
        self.size, self.scale, self.train = size, scale, train
        self.gsd, self.net_gsd, self.jitter, self.sat_aug = gsd, net_gsd, scale_jitter, sat_aug
        self.res_range = res_range

    def __len__(self):
        return len(self.items)

    def __getitem__(self, i):
        rp, hp = self.items[i]
        rgb = read_rgb(rp)
        h = read_height(hp) * self.scale
        if h.shape != rgb.shape[:2]:
            h = np.asarray(Image.fromarray(h).resize(rgb.shape[1::-1], Image.NEAREST))
        s = self.size
        H, W = h.shape
        if self.net_gsd:
            # resolution-matched: crop the ground footprint the app feeds the
            # network and resample it to s pixels (net GSD ~ --net-gsd)
            f = random.uniform(1 - self.jitter, 1 + self.jitter) if self.train else 1.0
            c = int(np.clip(round(s * self.net_gsd / self.gsd * f), s // 2, min(H, W)))
            y = random.randint(0, H - c) if self.train else (H - c) // 2
            x = random.randint(0, W - c) if self.train else (W - c) // 2
            rgb = np.asarray(Image.fromarray(np.ascontiguousarray(rgb[y:y + c, x:x + c])).resize((s, s), Image.LANCZOS))
            h = _resize_heights(h[y:y + c, x:x + c], s)
            eff_gsd = self.gsd * c / s
        else:
            if min(H, W) < s:  # upscale small tiles
                f = s / min(H, W)
                rgb = np.asarray(Image.fromarray(rgb).resize((int(W * f) + 1, int(H * f) + 1), Image.BICUBIC))
                h = np.asarray(Image.fromarray(h).resize((int(W * f) + 1, int(H * f) + 1), Image.NEAREST))
                H, W = h.shape
            y = random.randint(0, H - s) if self.train else (H - s) // 2
            x = random.randint(0, W - s) if self.train else (W - s) // 2
            rgb, h = rgb[y:y + s, x:x + s], h[y:y + s, x:x + s]
            eff_gsd = self.gsd
        if self.train:  # nadir imagery is rotation invariant
            k = random.randint(0, 3)
            rgb, h = np.rot90(rgb, k), np.rot90(h, k)
            if random.random() < 0.5:
                rgb, h = rgb[:, ::-1], h[:, ::-1]
            if self.res_range:
                rgb = _source_resolution_aug(np.ascontiguousarray(rgb), eff_gsd, *self.res_range)
            if self.sat_aug:
                rgb = _satellite_aug(np.ascontiguousarray(rgb))
        t = torch.from_numpy(np.ascontiguousarray(rgb)).permute(2, 0, 1).float() / 255
        if self.train:
            t = (t * random.uniform(0.8, 1.2) + random.uniform(-0.08, 0.08)).clamp(0, 1)
        return (t - MEAN) / STD, torch.from_numpy(np.ascontiguousarray(h)), torch.tensor(eff_gsd, dtype=torch.float32)


def _resize_heights(h, size):
    """Area-average resampling that ignores voids (NaN)."""
    valid = np.isfinite(h).astype(np.float32)
    filled = np.where(valid > 0, h, 0).astype(np.float32)
    num = np.asarray(Image.fromarray(filled * valid).resize((size, size), Image.BOX))
    den = np.asarray(Image.fromarray(valid).resize((size, size), Image.BOX))
    out = num / np.maximum(den, 1e-6)
    out[den < 0.5] = np.nan
    return out.astype(np.float32)


def _source_resolution_aug(rgb, eff_gsd, lo, hi, p=0.7):
    """Simulate a coarser sensor: re-sample the image as if it had been
    captured at a ground resolution between ``lo`` and ``hi`` m/px (log-uniform),
    then bring it back to the network grid. The height target is unchanged, so
    the model learns to predict fine-grid heights from blurrier imagery - what
    the app does when it upsamples a 1-2.5 m scene to the training resolution."""
    if random.random() > p:
        return rgb
    r = math.exp(random.uniform(math.log(lo), math.log(hi)))
    k = r / eff_gsd
    if k <= 1.05:
        return rgb
    s = rgb.shape[0]
    small = max(8, int(round(s / k)))
    img = Image.fromarray(rgb).resize((small, small), Image.BOX)
    return np.asarray(img.resize((s, s), random.choice((Image.BILINEAR, Image.BICUBIC))))


def _satellite_aug(rgb):
    """Make aerial tiles look more like satellite imagery: softer optics,
    atmospheric haze and sensor noise. Geometry (and so the height target)
    is unchanged."""
    from PIL import ImageFilter
    img = Image.fromarray(rgb)
    if random.random() < 0.5:                       # coarser optics / resampling
        f = random.uniform(1.2, 2.0)
        w, hh = img.size
        img = img.resize((max(8, int(w / f)), max(8, int(hh / f))), Image.BILINEAR).resize((w, hh), Image.BICUBIC)
    if random.random() < 0.3:
        img = img.filter(ImageFilter.GaussianBlur(random.uniform(0.3, 1.0)))
    a = np.asarray(img).astype(np.float32)
    if random.random() < 0.3:                       # haze
        k = random.uniform(0.05, 0.25)
        a = a * (1 - k) + k * random.uniform(170, 230)
    if random.random() < 0.3:                       # sensor noise
        a = a + np.random.normal(0, random.uniform(1, 4), a.shape)
    return np.clip(a, 0, 255).astype(np.uint8)


def _fit(p, g):
    """Closed-form least-squares scale/shift (differentiable, O(N) memory)."""
    p, g = p.float(), g.float()
    pm, gm = p.mean(), g.mean()
    a = ((p - pm) * (g - gm)).sum() / ((p - pm) ** 2).sum().clamp(min=1e-8)
    return a, gm - a * pm


def ssi_loss(pred, gt):
    """Scale-and-shift-invariant L1 (MiDaS) + multi-scale gradient matching."""
    mask = torch.isfinite(gt)
    gt = torch.nan_to_num(gt)
    loss = 0
    for p, g, m in zip(pred, gt, mask):
        if m.sum() < 100:
            continue
        a, b = _fit(p[m], g[m])
        aligned = p * a + b
        diff = (aligned - g) * m
        loss = loss + diff.abs().sum() / m.sum()
        for k in range(4):  # gradient matching at 4 scales – sharp building edges
            d = diff[:: 2 ** k, :: 2 ** k]
            mm = m[:: 2 ** k, :: 2 ** k].float()
            gx = (d[:, 1:] - d[:, :-1]).abs() * mm[:, 1:] * mm[:, :-1]
            gy = (d[1:] - d[:-1]).abs() * mm[1:] * mm[:-1]
            loss = loss + 0.5 * (gx.sum() + gy.sum()) / mm.sum().clamp(min=1)
    return loss / len(pred)


def metric_loss(pred, gt, gsd, pixel_height=1.0, tall_weight=0.0):
    """Absolute height error in metres (no fitting), optionally up-weighting
    tall pixels, where single-image models tend to under-estimate."""
    loss, used = 0, 0
    for p, g, s in zip(pred, gt, gsd):
        m = torch.isfinite(g)
        if m.sum() < 100:
            continue
        gm = g[m]
        w = 1 + tall_weight * (gm / 10).clamp(0, 3)
        loss = loss + (w * (p[m] * pixel_height * s - gm).abs()).sum() / w.sum()
        used += 1
    return loss / max(used, 1)


@torch.no_grad()
def evaluate(model, loader, device, pixel_height=LEGACY_PIXEL_HEIGHT):
    """Per-tile mean RMSE, both affine-aligned (shape) and absolute (no fitting)."""
    model.eval()
    aff, ab, n, skipped = 0.0, 0.0, 0, 0
    for x, h, g in loader:
        x, h, g = x.to(device), h.to(device), g.to(device)
        p = model(pixel_values=x).predicted_depth
        p = F.interpolate(p[:, None], size=h.shape[-2:], mode="bilinear", align_corners=False)[:, 0]
        for pi, hi, gi in zip(p, h, g):
            m = torch.isfinite(hi) & torch.isfinite(pi)
            if m.sum() < 100:
                skipped += 1
                continue
            a, b = _fit(pi[m], hi[m])
            aff += torch.sqrt(((pi[m] * a + b - hi[m]) ** 2).mean()).item()
            ab += torch.sqrt(((pi[m] * pixel_height * gi - hi[m]) ** 2).mean()).item()
            n += 1
    model.train()
    print(f"Validation: {n} usable tiles, {skipped} skipped", flush=True)
    if not n:
        raise RuntimeError("No usable validation tiles")
    return {"affine": aff / n, "absolute": ab / n}


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--rgb", required=True)
    ap.add_argument("--height", required=True)
    ap.add_argument("--val-rgb")
    ap.add_argument("--val-height")
    ap.add_argument("--height-scale", type=float, default=1.0)
    ap.add_argument("--model", default="small", help="small | base | large | HF id | local checkpoint")
    ap.add_argument("--target", choices=("ssi", "metric"), default="ssi",
                    help="ssi: shape only (original); metric: also learn real heights")
    ap.add_argument("--metric-weight", type=float, default=1.0)
    ap.add_argument("--tall-weight", type=float, default=0.0)
    ap.add_argument("--gsd", type=float, default=0.33, help="dataset ground sampling distance (m/px)")
    ap.add_argument("--net-gsd", type=float, default=0.0,
                    help="train at this network-pixel size (0 = native crops, original recipe)")
    ap.add_argument("--scale-jitter", type=float, default=0.0)
    ap.add_argument("--sat-aug", action="store_true")
    ap.add_argument("--res-range", type=float, nargs=2, metavar=("MIN", "MAX"),
                    help="simulate source imagery between MIN and MAX m/px (e.g. 0.35 2.5)")
    ap.add_argument("--select", choices=("affine", "absolute"), default="affine",
                    help="validation score that picks the saved checkpoint")
    ap.add_argument("--out", default="checkpoints/da2-gamus")
    ap.add_argument("--epochs", type=int, default=10)
    ap.add_argument("--batch", type=int, default=4)
    ap.add_argument("--grad-accum", type=int, default=1,
                    help="accumulate gradients across this many batches")
    ap.add_argument("--amp-dtype", choices=("bf16", "fp16"), default="bf16",
                    help="CUDA autocast type; bf16 is more stable on RTX 40-series GPUs")
    ap.add_argument("--size", type=int, default=518)
    ap.add_argument("--lr", type=float, default=5e-6, help="encoder LR (decoder uses 10x)")
    ap.add_argument("--max-steps", type=int, default=0, help="stop early (smoke test)")
    ap.add_argument("--max-train-samples", type=int, default=0)
    ap.add_argument("--max-val-samples", type=int, default=0)
    ap.add_argument("--seed", type=int, default=42)
    ap.add_argument("--resume", action="store_true", help="resume from OUT/last.pt")
    ap.add_argument("--require-cuda", action="store_true", help="fail if a CUDA GPU is unavailable")
    a = ap.parse_args()
    a.model = MODEL_ALIASES.get(a.model, a.model)
    pixel_height = 1.0 if a.target == "metric" else LEGACY_PIXEL_HEIGHT

    from transformers import AutoImageProcessor, AutoModelForDepthEstimation
    device = "cuda" if torch.cuda.is_available() else "cpu"
    if a.require_cuda and device != "cuda":
        raise SystemExit("CUDA unavailable in this Python environment")
    if a.batch < 1 or a.grad_accum < 1:
        raise SystemExit("--batch and --grad-accum must be positive")
    random.seed(a.seed); np.random.seed(a.seed); torch.manual_seed(a.seed)
    cache = Path(os.environ.get("DEPTHWIZARD_MODEL_CACHE",
                                Path(__file__).resolve().parents[1] / "models" / "cache"))
    cache.mkdir(parents=True, exist_ok=True)
    model = AutoModelForDepthEstimation.from_pretrained(a.model, cache_dir=cache).to(device)
    if device == "cuda":
        torch.backends.cuda.matmul.allow_tf32 = True
        print("GPU:", torch.cuda.get_device_name(0), flush=True)
    enc = [p for n, p in model.named_parameters() if n.startswith("backbone")]
    dec = [p for n, p in model.named_parameters() if not n.startswith("backbone")]
    opt = torch.optim.AdamW([{"params": enc, "lr": a.lr}, {"params": dec, "lr": a.lr * 10}], weight_decay=0.01)

    train_data = Pairs(a.rgb, a.height, a.size, a.height_scale,
                       limit=a.max_train_samples, seed=a.seed, gsd=a.gsd, net_gsd=a.net_gsd,
                       scale_jitter=a.scale_jitter, sat_aug=a.sat_aug, res_range=a.res_range)
    train = DataLoader(train_data, batch_size=a.batch,
                       shuffle=True, num_workers=0 if os.name == "nt" else 4)
    val = DataLoader(Pairs(a.val_rgb, a.val_height, a.size, a.height_scale,
                           train=False, limit=a.max_val_samples, seed=a.seed,
                           gsd=a.gsd, net_gsd=a.net_gsd),
                     batch_size=a.batch) if a.val_rgb else None
    total = a.epochs * math.ceil(len(train) / a.grad_accum)
    total = max(total, 10)
    # at least 2 warm-up steps, so very short (smoke-test) runs don't divide by zero
    sched = torch.optim.lr_scheduler.OneCycleLR(opt, max_lr=[a.lr, a.lr * 10], total_steps=total,
                                                pct_start=max(0.05, 3.0 / total))
    amp_dtype = torch.bfloat16 if a.amp_dtype == "bf16" else torch.float16
    scaler = torch.amp.GradScaler("cuda", enabled=device == "cuda" and a.amp_dtype == "fp16")
    best, step, start_epoch = float("inf"), 0, 0
    out = Path(a.out)
    out.mkdir(parents=True, exist_ok=True)
    if a.resume:
        state = torch.load(out / "last.pt", map_location="cpu", weights_only=False)
        model.load_state_dict(state["model"])
        opt.load_state_dict(state["optimizer"])
        sched.load_state_dict(state["scheduler"])
        scaler.load_state_dict(state["scaler"])
        best, step, start_epoch = state["best"], state["step"], state["next_epoch"]
        print(f"Resuming from epoch {start_epoch + 1}, step {step}", flush=True)
    print(f"Training {len(train_data)} tiles, validation {len(val.dataset) if val else 0} tiles; "
          f"batch {a.batch}, accumulation {a.grad_accum}, {a.epochs} epochs", flush=True)
    for ep in range(start_epoch, a.epochs):
        started = time.time()
        if device == "cuda":
            torch.cuda.reset_peak_memory_stats()
        opt.zero_grad(set_to_none=True)
        for batch_index, (x, h, g) in enumerate(train):
            x, h, g = x.to(device), h.to(device), g.to(device)
            with torch.autocast(device_type=device, enabled=device == "cuda", dtype=amp_dtype):
                p = model(pixel_values=x).predicted_depth
            p = F.interpolate(p[:, None].float(), size=h.shape[-2:], mode="bilinear", align_corners=False)[:, 0]
            loss = ssi_loss(p, h)
            if a.target == "metric":
                loss = loss + a.metric_weight * metric_loss(p, h, g, 1.0, a.tall_weight)
            scaler.scale(loss / a.grad_accum).backward()
            if (batch_index + 1) % a.grad_accum == 0 or batch_index + 1 == len(train):
                scaler.unscale_(opt)
                torch.nn.utils.clip_grad_norm_(model.parameters(), 1.0)
                old_scale = scaler.get_scale()
                scaler.step(opt); scaler.update()
                opt.zero_grad(set_to_none=True)
                if scaler.get_scale() < old_scale:
                    print(f"ep {ep + 1}: skipped an overflowed optimizer step", flush=True)
                    continue
                sched.step()
                step += 1
                if step % 50 == 0 or step == 1:
                    print(f"ep {ep + 1} step {step}/{total} loss {loss.item():.4f}", flush=True)
                if a.max_steps and step >= a.max_steps:
                    break
        scores = evaluate(model, val, device, pixel_height) if val else None
        score = scores[a.select] if scores else -ep
        minutes = (time.time() - started) / 60
        peak_gib = torch.cuda.max_memory_allocated() / 2**30 if device == "cuda" else 0
        print((f"epoch {ep + 1}: val affine-RMSE {scores['affine']:.3f} m, absolute-RMSE "
               f"{scores['absolute']:.3f} m (selecting on {a.select})" if val else f"epoch {ep + 1} done")
              + f" in {minutes:.1f} min; peak VRAM {peak_gib:.2f} GiB", flush=True)
        with (out / "history.jsonl").open("a") as history:
            history.write(json.dumps({"epoch": ep + 1, "optimizer_steps": step,
                                      "val_affine_rmse_m": scores["affine"] if val else None,
                                      "val_absolute_rmse_m": scores["absolute"] if val else None,
                                      "minutes": minutes, "peak_vram_gib": peak_gib}) + "\n")
        if score < best:
            best = score
            model.save_pretrained(out)
            try:  # mark as an above-ground-height checkpoint for DepthWizard calibration
                import json as _json
                cfg_path = out / "config.json"
                cfg = _json.loads(cfg_path.read_text())
                cfg["depthwizard_target"] = "agl"
                if a.target == "metric":
                    cfg["depthwizard_pixel_height"] = 1.0
                if a.net_gsd:
                    cfg["depthwizard_train_net_gsd"] = a.net_gsd
                if a.res_range:
                    cfg["depthwizard_res_range"] = list(a.res_range)
                cfg["depthwizard_training"] = {k: v for k, v in vars(a).items()
                                               if k in ("model", "target", "metric_weight", "tall_weight", "gsd",
                                                        "net_gsd", "scale_jitter", "sat_aug", "res_range", "select",
                                                        "epochs", "lr", "size")}
                cfg_path.write_text(_json.dumps(cfg, indent=2))
            except Exception:  # noqa: BLE001
                pass
            try:
                AutoImageProcessor.from_pretrained(a.model, cache_dir=cache).save_pretrained(out)
            except Exception:  # noqa: BLE001
                pass
            print(f"  saved -> {out}")
        state = {"model": model.state_dict(), "optimizer": opt.state_dict(),
                 "scheduler": sched.state_dict(), "scaler": scaler.state_dict(),
                 "best": best, "step": step, "next_epoch": ep + 1}
        tmp = out / "last.pt.tmp"
        torch.save(state, tmp)
        tmp.replace(out / "last.pt")
        if a.max_steps and step >= a.max_steps:
            break


if __name__ == "__main__":
    main()
