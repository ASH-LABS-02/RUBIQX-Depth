"""Generate a print-ready technical evidence dossier / report for a DepthWizard scene."""
from __future__ import annotations

import base64
from datetime import datetime, timezone
from html import escape
import json
import math
from pathlib import Path


def _text(value, default="–") -> str:
    """Escape metadata and user-supplied names before embedding in a report."""
    return escape(str(value if value is not None and value != "" else default), quote=True)


def _finite(value) -> float | None:
    try:
        number = float(value)
        return number if math.isfinite(number) else None
    except (TypeError, ValueError):
        return None


def _metric(value, digits=2, suffix="") -> str:
    number = _finite(value)
    return f"{number:.{digits}f}{suffix}" if number is not None else "–"


def _anchor_provenance(meta: dict) -> tuple[str, str]:
    """Describe applied automatic anchors without promoting estimates to truth."""
    record = meta.get("auto_anchors") or meta.get("automatic_anchors") or {}
    if not isinstance(record, dict):
        record = {}
    applied = meta.get("height_anchor") or {}
    if not isinstance(applied, dict):
        applied = {}
    anchors = record.get("anchors") or applied.get("anchors") or []
    if not isinstance(anchors, list):
        anchors = []
    diag = record.get("diagnostics") or record.get("stats") or {}
    if not isinstance(diag, dict):
        diag = {}
    osm_diag = diag.get("osm") or {}
    shadow_diag = diag.get("shadow") or {}
    osm_count = sum("openstreetmap" in str(a.get("source", "")).lower() for a in anchors if isinstance(a, dict))
    shadow_count = sum("shadow" in str(a.get("source", "")).lower() for a in anchors if isinstance(a, dict))
    if not anchors:
        osm_count = int(_finite(osm_diag.get("accepted")) or 0)
        shadow_count = int(_finite(shadow_diag.get("accepted")) or 0)
    applied_count = (int(_finite((applied.get("stats") or {}).get("n_used")) or 0)
                     if applied.get("source") == "automatic" and record.get("applied") else 0)
    if not anchors and not applied_count and not osm_count and not shadow_count:
        return "No automatic building-height anchors recorded.", ""
    parts = []
    if osm_count:
        parts.append(f"{osm_count} OSM building tag candidate{'s' if osm_count != 1 else ''}")
    if shadow_count:
        parts.append(f"{shadow_count} shadow estimate candidate{'s' if shadow_count != 1 else ''}")
    if applied_count:
        parts.append(f"{applied_count} applied to scale calibration")
    elif anchors or osm_count or shadow_count:
        parts.append("none applied to scale calibration")
    direct = sum(a.get("osm_tag") == "height" for a in anchors if isinstance(a, dict))
    levels = sum(a.get("osm_tag") == "building:levels" for a in anchors if isinstance(a, dict))
    if direct or levels:
        parts.append(f"{direct} mapped heights; {levels} levels-based estimates")
    note = ("OSM heights are community mapped and unverified here. Levels use an assumed "
            "3 m per floor. Shadow geometry depends on solar angle, segmentation and clear ground. "
            "These anchors are calibration inputs and cannot also serve as independent validation.")
    return "; ".join(parts) + ".", note


def generate_html_report(scene_dir: Path) -> str:
    """Generate a comprehensive HTML/PDF evaluation report."""
    viewer_meta_path = scene_dir / "viewer" / "meta.json"
    root_meta_path = scene_dir / "meta.json"
    metrics_path = scene_dir / "metrics.json"
    buildings_path = scene_dir / "viewer" / "buildings.json"
    job_path = scene_dir / "job.json"

    meta = {}
    if root_meta_path.exists():
        try:
            meta = json.loads(root_meta_path.read_text(encoding="utf-8"))
        except Exception:
            pass
    if viewer_meta_path.exists():
        try:
            v_meta = json.loads(viewer_meta_path.read_text(encoding="utf-8"))
            v_meta.update(meta)
            meta = v_meta
        except Exception:
            pass

    job_info = {}
    if job_path.exists():
        try:
            job_info = json.loads(job_path.read_text(encoding="utf-8"))
        except Exception:
            pass

    metrics = {}
    if metrics_path.exists():
        try:
            metrics = json.loads(metrics_path.read_text(encoding="utf-8"))
        except Exception:
            pass

    buildings = {"count": 0, "total_footprint_m2": 0.0, "buildings": []}
    if buildings_path.exists():
        try:
            buildings = json.loads(buildings_path.read_text(encoding="utf-8"))
        except Exception:
            pass

    scene_name = _text(job_info.get("name") or meta.get("input") or scene_dir.name)
    cal = meta.get("calibration") or {}
    ev = meta.get("evidence_bundle") or {}
    if not isinstance(cal, dict):
        cal = {}
    if not isinstance(ev, dict):
        ev = {}

    # Base64 preview image if available
    preview_b64 = ""
    preview_file = scene_dir / "preview.png"
    if preview_file.exists():
        preview_b64 = base64.b64encode(preview_file.read_bytes()).decode("ascii")

    an = meta.get("analytics", {}) or {}
    if not isinstance(an, dict):
        an = {}
    rows = []
    bm = metrics.get("buildings")
    if isinstance(bm, dict) and _finite(bm.get("rmse")) is not None:
        rows.append(f"<tr><td>Per-building roof height vs supplied reference</td><td>n={_text(bm.get('n'))} · RMSE {_metric(bm.get('rmse'))} m · r {_metric(bm.get('r'), 3)} · bias {_metric(bm.get('bias'))} m</td></tr>")
    agg = metrics.get("aggregated_30m")
    if isinstance(agg, dict) and _finite(agg.get("rmse")) is not None:
        rows.append(f"<tr><td>DSM averaged to approximately 30 m vs supplied reference</td><td>RMSE {_metric(agg.get('rmse'))} m · MAE {_metric(agg.get('mae'))} m · r {_metric(agg.get('r'), 3)}</td></tr>")
    if isinstance(an.get("landslide"), dict):
        land = an["landslide"]
        fr = land.get("fractions") or {}
        high, very_high = _finite(fr.get("high")), _finite(fr.get("very_high"))
        if high is not None and very_high is not None:
            rows.append(f"<tr><td>Landslide susceptibility (screening only)</td><td>high {high*100:.1f} % · very high {very_high*100:.1f} % · max slope {_metric(land.get('max_slope_deg'), 0)}°</td></tr>")
    if isinstance(an.get("solar"), dict):
        solar = an["solar"]
        rows.append(f"<tr><td>Rooftop solar (indicative)</td><td>{_metric(solar.get('total_pv_mwh_yr'), 1)} MWh/yr over {_text(solar.get('buildings'))} roofs · {_text(solar.get('assumptions'))}</td></tr>")
    if isinstance(meta.get("change_stats"), dict):
        c = meta["change_stats"]
        rows.append(f"<tr><td>Change vs {_text(meta.get('change_against'))}</td><td>lowered {_metric(c.get('volume_loss_m3'), 0)} m³ · raised {_metric(c.get('volume_gain_m3'), 0)} m³ · buildings with roof loss {_text(c.get('buildings_height_loss'))}</td></tr>")
    water_fraction = _finite(meta.get("water_fraction"))
    if water_fraction is not None and water_fraction > 0:
        rows.append(f"<tr><td>Open water flattened</td><td>{water_fraction*100:.1f} % of scene</td></tr>")
    analytics_html = ("<h3>Buildings, hazards and change</h3><table><tbody>" + "".join(rows) + "</tbody></table>") if rows else ""

    tex_b64 = ""
    tex_file = scene_dir / "viewer" / "texture.jpg"
    if tex_file.exists():
        tex_b64 = base64.b64encode(tex_file.read_bytes()).decode("ascii")

    def fmt(v, d=2):
        return _metric(v, d)

    # Key metrics
    abs_m = metrics.get("absolute") or {}
    base_m = metrics.get("baseline_dem") or {}
    agg_m = metrics.get("aggregated_30m") or {}
    has_reference = _finite(abs_m.get("rmse")) is not None
    baseline_rmse = _finite(base_m.get("rmse"))
    current_rmse = _finite(abs_m.get("rmse"))
    gain_m = baseline_rmse - current_rmse if has_reference and baseline_rmse is not None else None

    rmse_val = fmt(abs_m.get("rmse"))
    dem_rmse_val = fmt(base_m.get("rmse"))
    agg_rmse_val = fmt(agg_m.get("rmse"))
    mae_val = fmt(abs_m.get("mae"))
    r_val = fmt(abs_m.get("r"), 3)
    dem_r_val = fmt(base_m.get("r"), 3)

    # Building stats
    building_list = buildings.get("buildings") or []
    if not isinstance(building_list, list):
        building_list = []
    b_count = len(building_list)
    b_area = fmt(buildings.get("total_footprint_m2"), 1)
    units = str(meta.get("units") or "relative")
    metric_scene = units == "metre"
    heights = [v for b in building_list if isinstance(b, dict)
               if (v := _finite(b.get("height_m"))) is not None]
    tallest = max(heights) if heights else None
    scene_summary = (f"{b_count} model-detected building footprint{'s' if b_count != 1 else ''}. "
                     if b_count else "No building footprints detected. ")
    if tallest is not None:
        scene_summary += (f"Tallest estimated above-ground height: {tallest:.1f} m. " if metric_scene
                          else f"Largest relative building height: {tallest:.3f}. ")
    scene_summary += (f"Calibration: {_text(cal.get('method'))} "
                      f"({_text(cal.get('evidence_level'))} evidence). ")
    if has_reference:
        scene_summary += f"RMSE against the supplied reference: {current_rmse:.2f} m."
    else:
        scene_summary += "No supplied reference was evaluated; metric accuracy is not established for this scene."
    anchor_summary, anchor_note = _anchor_provenance(meta)
    validation_summary = ("Metrics below compare this estimate to a supplied reference raster; "
                          "the report does not establish whether that reference is independent of "
                          "the calibration data." if has_reference else
                          "No validation raster or absolute-error metrics are available for this scene.")
    if gain_m is None:
        baseline_summary = "A like-for-like DEM baseline comparison is unavailable."
    elif gain_m > 0:
        baseline_summary = f"The model RMSE is {gain_m:.2f} m lower than the input DEM baseline on this supplied reference."
    elif gain_m < 0:
        baseline_summary = f"The model RMSE is {-gain_m:.2f} m higher than the input DEM baseline on this supplied reference."
    else:
        baseline_summary = "The model and input DEM have equal RMSE on this supplied reference."

    html = f"""<!doctype html>
<html lang="en">
<head>
<meta charset="utf-8">
<title>DepthWizard Scene Report — {scene_name}</title>
<style>
  @page {{ size: A4; margin: 18mm 15mm; }}
  body {{
    font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", Roboto, Helvetica, Arial, sans-serif;
    color: #1a2530;
    line-height: 1.45;
    background: #fff;
    margin: 0 auto;
    max-width: 900px;
    padding: 24px;
    font-size: 13px;
  }}
  .header {{
    display: flex;
    justify-content: space-between;
    align-items: flex-start;
    border-bottom: 2px solid #0b1826;
    padding-bottom: 12px;
    margin-bottom: 20px;
  }}
  .logo {{
    font-size: 22px;
    font-weight: 800;
    color: #0b1826;
    letter-spacing: -0.5px;
  }}
  .logo small {{
    display: block;
    font-size: 11px;
    font-weight: 500;
    color: #0284c7;
    text-transform: uppercase;
    letter-spacing: 1px;
  }}
  .dossier-tag {{
    text-align: right;
    font-size: 11px;
    color: #64748b;
  }}
  .badge {{
    display: inline-block;
    padding: 3px 8px;
    border-radius: 4px;
    font-weight: 600;
    font-size: 11px;
    background: #e0f2fe;
    color: #0369a1;
  }}
  .badge.success {{ background: #dcfce7; color: #15803d; }}
  .cards {{
    display: grid;
    grid-template-columns: repeat(4, 1fr);
    gap: 12px;
    margin-bottom: 20px;
  }}
  .card {{
    background: #f8fafc;
    border: 1px solid #e2e8f0;
    border-radius: 6px;
    padding: 12px;
    text-align: center;
  }}
  .card .v {{
    font-size: 22px;
    font-weight: 700;
    color: #0f172a;
  }}
  .card .v small {{ font-size: 12px; font-weight: normal; color: #64748b; }}
  .card .l {{
    font-size: 11px;
    color: #64748b;
    margin-top: 4px;
    text-transform: uppercase;
    letter-spacing: 0.5px;
  }}
  .section-title {{
    font-size: 15px;
    font-weight: 700;
    color: #0f172a;
    border-bottom: 1px solid #e2e8f0;
    padding-bottom: 6px;
    margin: 20px 0 12px 0;
  }}
  table.t {{
    width: 100%;
    border-collapse: collapse;
    margin-bottom: 16px;
    font-size: 12px;
  }}
  table.t th, table.t td {{
    border: 1px solid #e2e8f0;
    padding: 7px 10px;
    text-align: left;
  }}
  table.t th {{
    background: #f1f5f9;
    font-weight: 600;
    color: #334155;
  }}
  table.t tr:nth-child(even) {{ background: #fafafa; }}
  .better {{ color: #16a34a; font-weight: 600; }}
  .worse {{ color: #dc2626; }}
  .grid-2 {{
    display: grid;
    grid-template-columns: 1fr 1fr;
    gap: 16px;
  }}
  .kv {{
    display: grid;
    grid-template-columns: 140px 1fr;
    gap: 6px 12px;
    font-size: 12px;
    background: #f8fafc;
    padding: 12px;
    border-radius: 6px;
    border: 1px solid #e2e8f0;
  }}
  .kv b {{ color: #475569; }}
  .images-preview {{
    display: grid;
    grid-template-columns: 1fr 1fr;
    gap: 12px;
    margin: 16px 0;
  }}
  .img-box {{
    border: 1px solid #cbd5e1;
    border-radius: 6px;
    overflow: hidden;
    background: #000;
    text-align: center;
  }}
  .img-box img {{
    width: 100%;
    height: auto;
    display: block;
  }}
  .img-box .caption {{
    padding: 6px;
    background: #f1f5f9;
    font-size: 11px;
    font-weight: 600;
    color: #334155;
  }}
  .callout {{
    background: #eff6ff;
    border-left: 4px solid #3b82f6;
    padding: 10px 14px;
    border-radius: 0 4px 4px 0;
    font-size: 12px;
    margin: 14px 0;
  }}
  .footer {{
    margin-top: 30px;
    border-top: 1px solid #cbd5e1;
    padding-top: 12px;
    font-size: 10px;
    color: #94a3b8;
    display: flex;
    justify-content: space-between;
  }}
  @media print {{
    body {{ max-width: 100%; padding: 0; }}
    .no-print {{ display: none; }}
  }}
</style>
</head>
<body>
<div class="no-print" style="position:sticky;top:0;display:flex;justify-content:flex-end;padding:8px 0;">
  <button onclick="window.print()" style="font:600 13px system-ui;padding:8px 14px;border-radius:6px;border:1px solid #1d4d6b;background:#1d6fa5;color:#fff;cursor:pointer">Save as PDF / Print</button>
</div>
<script>if (location.search.includes('print=1')) addEventListener('load', () => setTimeout(() => window.print(), 400));</script>

<div class="header">
  <div>
    <div class="logo">DepthWizard<small>SIH 2026 · PS 26175 · Technical scene report</small></div>
    <h2 style="margin:8px 0 4px 0; font-size:18px;">Reconstruction and validation: {scene_name}</h2>
    <span class="badge success">{_text(units.upper())} {'DSM' if metric_scene else 'SURFACE'}</span>
    <span class="badge">Datum: {_text(meta.get('vertical_datum'))}</span>
    <span class="badge">Backbone: {_text(meta.get('backbone'))}</span>
  </div>
  <div class="dossier-tag">
    <div><strong>Scene ID:</strong> {_text(scene_dir.name)}</div>
    <div><strong>Generated:</strong> {datetime.now(timezone.utc).strftime('%Y-%m-%d UTC')}</div>
    <div style="margin-top:6px;"><button class="no-print" onclick="window.print()" style="padding:6px 12px; background:#0284c7; color:#fff; border:none; border-radius:4px; cursor:pointer;">Print / Save as PDF</button></div>
  </div>
</div>

<div class="cards">
  <div class="card">
    <div class="v">{rmse_val}<small>{' m' if has_reference else ''}</small></div>
    <div class="l">RMSE vs supplied reference</div>
  </div>
  <div class="card">
    <div class="v">{agg_rmse_val}<small>{' m' if _finite(agg_m.get('rmse')) is not None else ''}</small></div>
    <div class="l">Approx. 30 m aggregated RMSE</div>
  </div>
  <div class="card">
    <div class="v">{r_val}<small>{f' (DEM: {dem_r_val})' if baseline_rmse is not None else ''}</small></div>
    <div class="l">Pearson Correlation r</div>
  </div>
  <div class="card">
    <div class="v">{b_count}<small> candidates</small></div>
    <div class="l">Model-detected footprints{(' (' + b_area + ' m²)') if metric_scene else ' (area uncalibrated)'}</div>
  </div>
</div>

<div class="section-title">1. Scene summary</div>
<p>{scene_summary}</p>
<p>{validation_summary} {baseline_summary}</p>
<p><strong>Automatic building-height anchors:</strong> {_text(anchor_summary)} {_text(anchor_note, '')}</p>
<p><strong>Uncertainty:</strong> {_text(meta.get('uncertainty_status') or ('Provisional error model fitted on two DC urban scenes; other domains unvalidated.' if metric_scene and meta.get('tta', 0) > 1 and meta.get('backbone') != 'heuristic-fallback' else 'No validated error estimate available.'))} Reliability scores describe ensemble agreement or roof-height consistency, not accuracy probabilities.</p>
<p><strong>Use limits:</strong> Meshes and point clouds are sampled display products; use the DSM GeoTIFF for the full raster grid. Relative surfaces are unitless. Flood and rainfall scenarios omit drainage and upstream catchments; routes and shelters require field checks. Landslide runout omits material dynamics; relay coverage is line of sight only.</p>
{"<p><strong>Prototype only:</strong> This scene used a heuristic fallback; it does not establish model accuracy.</p>" if meta.get('backbone') == 'heuristic-fallback' else ''}

<div class="grid-2">
  <div>
    <div class="section-title">2. Calibration & Geospatial Provenance</div>
    <div class="kv">
      <b>Calibration Method</b><span>{_text(cal.get('method'))}</span>
      <b>Structure Scale k</b><span>{fmt(cal.get('scale_k'))} m / relative unit</span>
      <b>DEM Fit r</b><span>{fmt(cal.get('fit_r'))}</span>
      <b>Scale Source</b><span>{_text(cal.get('scale_source'))}</span>
      <b>Evidence Level</b><span>{_text(cal.get('evidence_level'))}</span>
      <b>Vertical Datum</b><span>{_text(meta.get('vertical_datum'))}</span>
      <b>CRS Projection</b><span>{_text(meta.get('crs'))}</span>
      <b>Pixel GSD</b><span>{fmt(meta.get('gsd_m', meta.get('assumed_gsd_m')))} m</span>
      <b>Scene Dimensions</b><span>{_text(meta.get('src_w'))} × {_text(meta.get('src_h'))} px</span>
    </div>
  </div>

  <div>
    <div class="section-title">3. Cryptographic Evidence Passport</div>
    <div class="kv">
      <b>Software Version</b><span>{_text(ev.get('software_version'))}</span>
      <b>Input Image Hash</b><span><code>{_text(ev.get('image_sha256'))}</code></span>
      <b>DEM Input Hash</b><span><code>{_text(ev.get('dem_sha256'))}</code></span>
      <b>Reference Hash</b><span><code>{_text(ev.get('reference_sha256'))}</code></span>
      <b>Model Identifier</b><span>{_text(ev.get('model_identifier', meta.get('backbone')))}</span>
      <b>Inference Latency</b><span>{_text((meta.get('timing_s') or {}).get('depth'))} s</span>
      <b>Total Pipeline</b><span>{_text((meta.get('timing_s') or {}).get('total'))} s</span>
      <b>30 m DEM consistency setting</b><span>{'Enabled' if cal.get('match_dem_30m') else 'Not recorded'}</span>
      <b>Canopy correction</b><span>{'Applied' if cal.get('dem_canopy_corrected') else 'Not recorded'}</span>
    </div>
  </div>
</div>

<div class="section-title">4. Validation against supplied reference</div>
<table class="t">
  <thead>
    <tr>
      <th>Model Surface</th>
      <th>RMSE (m)</th>
      <th>MAE (m)</th>
      <th>NMAD (m)</th>
      <th>Pearson r</th>
      <th>|Error| ≤ 2m</th>
    </tr>
  </thead>
  <tbody>
    <tr>
      <td><strong>DepthWizard estimated surface</strong></td>
      <td>{rmse_val}</td>
      <td>{mae_val}</td>
      <td>{fmt(abs_m.get('nmad'))}</td>
      <td>{r_val}</td>
      <td>{_metric(_finite(abs_m.get('within_2m')) * 100 if _finite(abs_m.get('within_2m')) is not None else None, 1)}{'%' if _finite(abs_m.get('within_2m')) is not None else ''}</td>
    </tr>
    {"<tr><td>Input DEM baseline on same reference</td><td>" + dem_rmse_val + "</td><td>" + fmt(base_m.get('mae')) + "</td><td>" + fmt(base_m.get('nmad')) + "</td><td>" + dem_r_val + "</td><td>" + _metric(_finite(base_m.get('within_2m')) * 100 if _finite(base_m.get('within_2m')) is not None else None, 1) + ("%" if _finite(base_m.get('within_2m')) is not None else "") + "</td></tr>" if dem_rmse_val != "–" else ""}
    {"<tr><td>DSM averaged to approximately 30 m</td><td>" + agg_rmse_val + "</td><td>" + fmt(agg_m.get('mae')) + "</td><td>" + fmt(agg_m.get('nmad')) + "</td><td>" + fmt(agg_m.get('r'), 3) + "</td><td>" + _metric(_finite(agg_m.get('within_2m')) * 100 if _finite(agg_m.get('within_2m')) is not None else None, 1) + ("%" if _finite(agg_m.get('within_2m')) is not None else "") + "</td></tr>" if agg_rmse_val != "–" else ""}
  </tbody>
</table>

<div class="callout">
  <strong>Interpretation:</strong> {_text(baseline_summary)} RMSE and correlation depend on the supplied reference, its quality, alignment and vertical datum. A DEM used for calibration is not an independent accuracy reference.
</div>

<div class="section-title">5. Urban Morphology & LoD1 Building Footprints</div>
<table class="t">
  <thead>
    <tr>
      <th>Total Buildings</th>
      <th>Total Built Footprint</th>
      <th>Mean Building Height</th>
      <th>Average Storeys</th>
      <th>Total Structural Volume</th>
    </tr>
  </thead>
  <tbody>
    <tr>
      <td><strong>{b_count}</strong></td>
      <td>{b_area + ' m²' if metric_scene else 'Requires metric footprint calibration'}</td>
      <td>{fmt(sum(heights) / len(heights)) if metric_scene and heights else '–'}{' m' if metric_scene and heights else ''}</td>
      <td>{fmt(sum(_finite(b.get('storeys')) or 0 for b in building_list if isinstance(b, dict)) / b_count, 1) if metric_scene and b_count and any(isinstance(b, dict) and _finite(b.get('storeys')) is not None for b in building_list) else '–'}</td>
      <td>{fmt(sum(_finite(b.get('volume_m3')) or 0 for b in building_list if isinstance(b, dict))) if metric_scene and any(isinstance(b, dict) and _finite(b.get('volume_m3')) is not None for b in building_list) else '–'}{' m³' if metric_scene and any(isinstance(b, dict) and _finite(b.get('volume_m3')) is not None for b in building_list) else ''}</td>
    </tr>
  </tbody>
</table>

{analytics_html}

<div class="images-preview">
  {"<div class='img-box'><img src='data:image/jpeg;base64," + tex_b64 + "' alt='Input optical image'><div class='caption'>Input optical image</div></div>" if tex_b64 else ""}
  {"<div class='img-box'><img src='data:image/png;base64," + preview_b64 + "' alt='Estimated surface preview'><div class='caption'>Estimated surface preview</div></div>" if preview_b64 else ""}
</div>

<div class="footer">
  <div>DepthWizard System · Smart India Hackathon (SIH26175) · SAC/ISRO Monocular Height Estimation</div>
  <div>Generated locally · Estimates require independent field validation for operational use</div>
</div>

</body>
</html>"""
    return html
