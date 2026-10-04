"""Opt-in, reference-free pipeline receipts. References are used by scorers only."""
from __future__ import annotations

import json
from pathlib import Path

import numpy as np

from .io import write_dsm


class StageAudit:
    def __init__(self, output, image, enabled=False):
        self.path = Path(output) / "stages"
        self.image, self.enabled, self.records = image, enabled, []
        if enabled:
            self.path.mkdir(parents=True, exist_ok=True)

    def capture(self, name, values, units, *, scale=1.0):
        if not self.enabled or values is None:
            return
        arr = np.asarray(values, dtype=np.float32) * scale
        finite = arr[np.isfinite(arr)]
        record = {"stage": name, "file": name + ".tif", "units": units,
                  "valid_pixels": int(finite.size), "scale_to_units": float(scale)}
        if finite.size:
            record.update(mean=float(finite.mean()), p50=float(np.median(finite)),
                          p95=float(np.percentile(finite, 95)), p99=float(np.percentile(finite, 99)))
        write_dsm(self.path / record["file"], arr, self.image, units=units,
                  description=f"Diagnostic pipeline stage: {name}", compound_vertical=False)
        self.records.append(record)
        (self.path / "manifest.json").write_text(json.dumps({"reference_used": False,
            "stages": self.records}, indent=2), encoding="utf-8")

    def buildings(self, buildings):
        if self.enabled:
            (self.path / "roof-extraction.json").write_text(json.dumps(buildings, indent=2), encoding="utf-8")
