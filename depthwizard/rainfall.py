"""Mass-conserving four-neighbour surface runoff screening (not shallow-water hydraulics)."""
import math
import numpy as np


def simulate_rainfall(dtm, gsd_m, *, rainfall_mm=50, duration_min=60,
                      infiltration_mm_hr=5, drainage_mm_hr=0, manning_n=.05, steps=120):
    z = np.asarray(dtm, dtype=np.float64)
    if z.ndim != 2 or min(z.shape) < 2 or z.size > 262144:
        raise ValueError("Rainfall screening grid must contain 2..262144 cells")
    values = [gsd_m, rainfall_mm, duration_min, infiltration_mm_hr, drainage_mm_hr, manning_n]
    if not all(math.isfinite(v) for v in values) or gsd_m <= 0 or duration_min <= 0 or manning_n <= 0 or min(values[1::]) < 0 or not 1 <= steps <= 600:
        raise ValueError("Invalid rainfall, duration, infiltration, drainage or grid settings")
    valid = np.isfinite(z)
    if not valid.any():
        raise ValueError("Terrain has no valid cells")
    z = np.where(valid,z,0)
    depth = np.zeros(z.shape, np.float64)
    dt = duration_min*60/steps
    rain = rainfall_mm/1000/steps
    loss_rate = (infiltration_mm_hr+drainage_mm_hr)/1000/3600
    added = removed = 0.0
    history = []
    for step in range(steps):
        depth[valid] += rain
        added += rain * valid.sum()*gsd_m**2
        loss = np.minimum(depth,loss_rate*dt)
        depth -= loss
        removed += loss.sum()*gsd_m**2
        head = z+depth
        transfers = []
        total_out = np.zeros_like(depth)
        for left,right in ((np.s_[:,:-1],np.s_[:,1:]),(np.s_[:-1,:],np.s_[1:,:])):
            delta = head[left]-head[right]
            donor_depth = np.where(delta >= 0,depth[left],depth[right])
            flux = np.sign(delta)*donor_depth**(5/3)/manning_n*np.sqrt(np.abs(delta)/gsd_m)*dt/gsd_m
            flux *= valid[left]&valid[right]
            total_out[left] += np.maximum(flux,0)
            total_out[right] += np.maximum(-flux,0)
            transfers.append((left,right,flux))
        limiter = np.minimum(1,depth/np.maximum(total_out,1e-30))
        for left,right,flux in transfers:
            flux *= np.where(flux >= 0,limiter[left],limiter[right])
            depth[left] -= flux
            depth[right] += flux
        depth = np.maximum(depth,0)
        if step % max(1,steps//20)==0 or step==steps-1:
            history.append({"minute":(step+1)*duration_min/steps,
                            "stored_m3":float(depth.sum()*gsd_m**2), "max_depth_m":float(depth.max())})
    stored = float(depth.sum()*gsd_m**2)
    return {"depth":depth.astype(np.float32), "rain_volume_m3":float(added),
            "loss_volume_m3":float(removed), "stored_volume_m3":stored,
            "mass_balance_error_m3":float(added-removed-stored), "history":history,
            "assumptions":{"rainfall_mm":rainfall_mm,"duration_min":duration_min,
                "infiltration_mm_hr":infiltration_mm_hr,"drainage_mm_hr":drainage_mm_hr,
                "manning_n":manning_n,"boundary":"closed; no upstream inflow/outfall",
                "flow":"limited Manning-like four-neighbour diffusive surface flow",
                "validation":"analytic conservation checks only; no real-event hydraulic validation"},
            "screening_only":True}
