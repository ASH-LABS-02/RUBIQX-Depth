"""Explicit, offline vertical-datum conversion for absolute elevation rasters.

This module is intentionally separate from calibration and reference evaluation.
It never downloads PROJ grids, resamples pixels, or replaces an input raster.
"""
from __future__ import annotations

from contextlib import contextmanager
from datetime import datetime, timezone
import json
import math
import os
from pathlib import Path
import re
import tempfile

import numpy as np
import pyproj
from pyproj import CRS, Transformer, datadir, network
from pyproj.crs import CompoundCRS
import rasterio
from rasterio.windows import Window


DATUMS = {"ellipsoidal": None, "EGM96": 5773, "EGM2008": 3855}
_METRE_UNITS = {"m", "metre", "metres", "meter", "meters"}
_HEIGHT_METADATA = {
    "HEIGHT_KIND", "HEIGHT_TYPE", "ELEVATION_TYPE", "PRODUCT_TYPE",
    "DESCRIPTION", "VERTICAL_DATUM", "HEIGHT_DATUM", "UNITS",
}
_DATUM_METADATA = {"VERTICAL_DATUM", "HEIGHT_DATUM", "VERTICAL_EPSG"}
_AMBIGUOUS_DATUM = {
    "", "unknown", "unspecified", "none", "orthometric", "wgs84", "wgs 84",
    "mean sea level", "msl", "same as input dem", "same as supplied orthometric gcp heights",
}
_DIFFERENTIAL = re.compile(
    r"\b(?:ndsm|agl|above[\s-]*ground|uncertainty|sigma|variance|"
    r"standard[\s-]*deviation|height[\s-]*difference|differential|relative[\s-]*height)\b",
    re.IGNORECASE,
)


class VerticalDatumError(RuntimeError):
    """Conversion cannot be completed without changing its declared meaning."""


def normalize_datum(value: str) -> str:
    """Accept explicit supported datum names and vertical EPSG aliases."""
    aliases = {
        "ellipsoidal": "ellipsoidal", "egm96": "EGM96", "egm2008": "EGM2008",
        "epsg:5773": "EGM96", "5773": "EGM96", "epsg:3855": "EGM2008", "3855": "EGM2008",
    }
    try:
        return aliases[str(value).strip().lower()]
    except KeyError as exc:
        raise VerticalDatumError("Datum must be ellipsoidal, EGM96 (EPSG:5773), or EGM2008 (EPSG:3855)") from exc


def _horizontal_crs(crs: CRS) -> CRS:
    if crs.is_compound:
        parts = [part for part in crs.sub_crs_list if not part.is_vertical]
        if len(parts) != 1:
            raise VerticalDatumError("Raster must have exactly one geographic or projected horizontal CRS")
        crs = parts[0]
    horizontal = crs.to_2d()
    if not (horizontal.is_geographic or horizontal.is_projected):
        raise VerticalDatumError("Geocentric, engineering, or missing horizontal CRSs are unsupported")
    return horizontal


def _crs_datum(crs: CRS) -> str | None:
    if crs.is_compound:
        vertical = [part for part in crs.sub_crs_list if part.is_vertical]
        if len(vertical) != 1:
            raise VerticalDatumError("Compound raster CRS must contain exactly one vertical CRS")
        code = vertical[0].to_epsg()
        if code not in {5773, 3855}:
            raise VerticalDatumError(f"Unsupported source vertical CRS: {vertical[0].name}; only EGM96/EGM2008 are supported")
        if not math.isclose(vertical[0].axis_info[0].unit_conversion_factor, 1.0):
            raise VerticalDatumError("Source vertical CRS must express upward heights in metres")
        return "EGM96" if code == 5773 else "EGM2008"
    if len(crs.axis_info) == 3:
        axis = crs.axis_info[2]
        if "ellipsoidal" not in axis.name.lower() or axis.direction.lower() != "up":
            raise VerticalDatumError("Only ellipsoidal three-dimensional source CRSs are supported")
        if not math.isclose(axis.unit_conversion_factor, 1.0):
            raise VerticalDatumError("Source ellipsoidal height axis must use metres")
        return "ellipsoidal"
    return None


def _tag_datum(value: str) -> str | None:
    value = str(value).lower()
    found = set()
    if "egm2008" in value or re.search(r"\b3855\b", value):
        found.add("EGM2008")
    if "egm96" in value or re.search(r"\b5773\b", value):
        found.add("EGM96")
    if re.search(r"\bellipsoid(?:al)?\b", value):
        found.add("ellipsoidal")
    if len(found) > 1:
        raise VerticalDatumError(f"Source datum metadata is contradictory: {value}")
    return next(iter(found), None)


def _validate_source(source, path: Path, crs: CRS, declared: str, *, height_kind: str,
                     attest_source_datum: bool) -> dict:
    if height_kind != "absolute":
        raise VerticalDatumError("Declare height_kind='absolute'; nDSM, AGL and uncertainty are not convertible elevations")
    if source.count != 1 or np.dtype(source.dtypes[0]).kind not in "fiu":
        raise VerticalDatumError("Input must be a single real-valued elevation band")
    if source.gcps[0] or source.rpcs is not None:
        raise VerticalDatumError("Input must use an affine raster grid, rather than GCP/RPC georeferencing")
    transform = source.transform
    if not all(math.isfinite(v) for v in transform) or abs(transform.determinant) < 1e-20:
        raise VerticalDatumError("Raster affine transform must be finite and invertible")
    tags = {str(k).upper(): str(v) for k, v in source.tags().items()}
    tags.update({str(k).upper(): str(v) for k, v in source.tags(1).items()})
    semantic_text = [path.stem.replace("_", " "), *(source.descriptions or ())]
    semantic_text += [value for key, value in tags.items() if key in _HEIGHT_METADATA]
    if any(_DIFFERENTIAL.search(value or "") for value in semantic_text):
        raise VerticalDatumError("Input is marked as nDSM/AGL/differential height or uncertainty; vertical datum offsets do not apply")
    if any(tags.get(key, "").strip().lower() in {"relative", "difference", "differential", "uncertainty"}
           for key in ("HEIGHT_KIND", "HEIGHT_TYPE", "ELEVATION_TYPE", "PRODUCT_TYPE")):
        raise VerticalDatumError("Input metadata declares differential/relative heights or uncertainty")
    if any(tags.get(key, "").strip().lower() in {"relative", "pixel", "pixels"}
           for key in ("VERTICAL_DATUM", "HEIGHT_DATUM", "UNITS")):
        raise VerticalDatumError("Relative heights cannot be converted between absolute vertical datums")
    units = [unit for unit in source.units if unit]
    units += [tags[key] for key in ("UNITS", "UNITTYPE", "HEIGHT_UNITS") if tags.get(key)]
    if any(str(unit).strip().lower() not in _METRE_UNITS for unit in units):
        raise VerticalDatumError(f"Input elevation units must be metres; declared units were {units}")
    if declared == "ellipsoidal" and any("orthometric" in tags.get(key, "").lower()
                                        for key in ("VERTICAL_DATUM", "HEIGHT_DATUM", "HEIGHT_TYPE")):
        raise VerticalDatumError("The source is explicitly marked orthometric, conflicting with the declared ellipsoidal datum")
    known = [datum for datum in [_crs_datum(crs)] if datum]
    if _tag_datum(tags.get("HEIGHT_TYPE", "")) == "ellipsoidal":
        known.append("ellipsoidal")
    for key in _DATUM_METADATA:
        if tags.get(key) and _tag_datum(tags[key]) is None and tags[key].strip().lower() not in _AMBIGUOUS_DATUM:
            raise VerticalDatumError(f"Unsupported explicit source vertical datum metadata: {key}={tags[key]}; attestation cannot override it")
    known += [datum for key in _DATUM_METADATA if tags.get(key)
              for datum in [_tag_datum(tags[key])] if datum]
    if any(datum != declared for datum in known):
        raise VerticalDatumError(f"Explicit source datum {declared} conflicts with raster metadata/CRS: {sorted(set(known))}")
    if not known and not attest_source_datum:
        raise VerticalDatumError("Raster has no supported vertical datum metadata/CRS. Supply --attest-source-datum only after confirming absolute metre heights and the declared source datum")
    if source.nodata is not None and not (math.isfinite(source.nodata) or math.isnan(source.nodata)):
        raise VerticalDatumError("Infinite nodata values are unsupported")
    scale, offset = source.scales[0], source.offsets[0]
    if not (math.isfinite(scale) and math.isfinite(offset)) or scale == 0:
        raise VerticalDatumError("Input band scale/offset must be finite, with a nonzero scale")
    return {"datum_confirmed_by": "metadata_or_crs" if known else "explicit_attestation",
            "attest_source_datum": attest_source_datum, "height_kind": height_kind,
            "input_scale": scale, "input_offset": offset, "input_units": units or ["metre (declared)"]}


def _height_crs(horizontal: CRS, datum: str) -> CRS:
    if datum == "ellipsoidal":
        # This is the ellipsoid of the input horizontal geodetic datum. It is
        # deliberately not replaced by WGS84 when the raster has another CRS.
        return horizontal.to_3d()
    return CompoundCRS(name=f"{horizontal.name} + {datum} height",
                       components=[horizontal, CRS.from_epsg(DATUMS[datum])])


@contextmanager
def _offline_proj(grid_dir: Path | None):
    previous_network = network.is_network_enabled()
    previous_data = datadir.get_data_dir()
    try:
        network.set_network_enabled(False)
        if grid_dir is not None:
            grid_dir = Path(grid_dir).resolve(strict=True)
            if not grid_dir.is_dir():
                raise VerticalDatumError("--grid-dir must name an existing local PROJ grid directory")
            datadir.append_data_dir(str(grid_dir))
        yield
    finally:
        datadir.set_data_dir(previous_data)
        network.set_network_enabled(previous_network)


def _accurate_transformer(source: CRS, target: CRS) -> Transformer:
    if tuple(int(v) for v in pyproj.proj_version_str.split(".")[:2]) < (9, 2):
        raise VerticalDatumError("PROJ >= 9.2 is required to enforce only_best=True")
    if tuple(int(v) for v in pyproj.__version__.split(".")[:2]) < (3, 5):
        raise VerticalDatumError("pyproj >= 3.5 is required to enforce only_best=True")
    try:
        transformer = Transformer.from_crs(source, target, always_xy=True,
                                           allow_ballpark=False, only_best=True)
        if transformer.is_network_enabled:
            raise VerticalDatumError("PROJ networking remained enabled; refusing an offline conversion")
        return transformer
    except Exception as exc:
        raise VerticalDatumError(f"Cannot create an accurate offline vertical conversion. Install the required geoid/reference-frame grids in the PROJ data path or --grid-dir. No approximate conversion was used. PROJ: {exc}") from exc


def _audit_operation(transformer: Transformer) -> dict:
    try:
        selected = transformer.get_last_used_operation()
    except pyproj.exceptions.ProjError:
        selected = transformer
    definition = selected.definition
    if not re.search(r"\bproj=vgridshift\b", definition):
        raise VerticalDatumError("PROJ did not select a real vertical grid shift; refusing a silent identity or approximate height conversion")
    grid_names = [name for names in re.findall(r"\bgrids=([^\s]+)", definition)
                  for name in names.split(",")]
    if not grid_names or any(name.startswith("@") or name.lower() == "null" for name in grid_names):
        raise VerticalDatumError("PROJ selected an optional/identity grid fallback; refusing the conversion")
    grids = {}

    def collect(operation):
        if operation.has_ballpark_transformation:
            raise VerticalDatumError("PROJ selected a ballpark operation; refusing the conversion")
        for grid in operation.grids:
            grids[grid.short_name] = {"name": grid.short_name, "available": grid.available,
                                      "path": grid.full_name, "url": grid.url}
        for part in operation.operations:
            collect(part)

    for operation in selected.operations:
        collect(operation)
    return {"description": selected.description, "definition": definition,
            "accuracy_m": selected.accuracy, "grid_names": grid_names,
            "grids": list(grids.values())}


def _windows(width: int, height: int, size: int):
    for row in range(0, height, size):
        for col in range(0, width, size):
            yield Window(col, row, min(size, width - col), min(size, height - row))


def convert_raster_vertical_datum(input_path: str | Path, output_path: str | Path, *,
                                  source_datum: str, target_datum: str, height_kind: str,
                                  attest_source_datum: bool = False,
                                  grid_dir: str | Path | None = None, block_size: int = 512) -> dict:
    """Create a new Float64 GeoTIFF of absolute metre elevations.

    Input must have one band, an affine grid, and a geographic/projected CRS.
    Datums are explicit. Missing source vertical metadata needs attestation;
    contradictory metadata and differential heights cannot be overridden.
    Network/data-path settings are restored afterward. Call serially because
    PROJ configuration is temporarily changed for offline operation.
    """
    source_datum, target_datum = normalize_datum(source_datum), normalize_datum(target_datum)
    if source_datum == target_datum:
        raise VerticalDatumError("Source and target datums must differ; this command does not relabel or make identity copies")
    if not isinstance(block_size, int) or isinstance(block_size, bool) or not 16 <= block_size <= 4096:
        raise VerticalDatumError("block_size must be an integer from 16 through 4096")
    input_path = Path(input_path).resolve(strict=True)
    output_path = Path(output_path).absolute()
    if output_path.exists() or output_path.is_symlink():
        raise VerticalDatumError(f"Output already exists and will not be overwritten: {output_path}")
    if output_path.suffix.lower() not in {".tif", ".tiff"}:
        raise VerticalDatumError("Output must be a new .tif or .tiff GeoTIFF")
    output_path.parent.mkdir(parents=True, exist_ok=True)
    temporary = None
    try:
        with _offline_proj(Path(grid_dir) if grid_dir else None), rasterio.Env(
                GDAL_TIFF_INTERNAL_MASK=True, PROJ_NETWORK="OFF"), rasterio.open(input_path) as source:
            if source.crs is None:
                raise VerticalDatumError("Input raster requires an explicit horizontal CRS; this command cannot infer one")
            input_crs = CRS.from_wkt(source.crs.to_wkt(version="WKT2_2019"))
            horizontal = _horizontal_crs(input_crs)
            source_info = _validate_source(source, input_path, input_crs, source_datum,
                                           height_kind=height_kind, attest_source_datum=attest_source_datum)
            conversion_source = _height_crs(horizontal, source_datum)
            conversion_target = _height_crs(horizontal, target_datum)
            transformer = _accurate_transformer(conversion_source, conversion_target)
            fd, name = tempfile.mkstemp(prefix=f".{output_path.stem}.vertical-", suffix=".tif", dir=output_path.parent)
            os.close(fd)
            temporary = Path(name)
            profile = dict(driver="GTiff", width=source.width, height=source.height, count=1,
                           dtype="float64", nodata=source.nodata, transform=source.transform,
                           crs=rasterio.crs.CRS.from_wkt(conversion_target.to_wkt()),
                           tiled=True, blockxsize=256, blockysize=256, compress="deflate",
                           predictor=3, BIGTIFF="IF_SAFER", GEOTIFF_VERSION="1.1")
            operations = {}
            converted_pixels = 0
            minimum, maximum, delta_sum = math.inf, -math.inf, 0.0
            input_tags, input_band_tags = source.tags(), source.tags(1)
            # Read any legitimate source PAM metadata, but require the new
            # output CRS/mask/provenance to be embedded in the GeoTIFF itself.
            with rasterio.Env(GDAL_PAM_ENABLED=False), rasterio.open(temporary, "w", **profile) as destination:
                for window in _windows(source.width, source.height, block_size):
                    raw = source.read(1, window=window, masked=True, out_dtype="float64")
                    heights = np.asarray(raw.data, dtype=np.float64) * source.scales[0] + source.offsets[0]
                    valid = ~np.ma.getmaskarray(raw) & np.isfinite(heights)
                    values = np.full(raw.shape, source.nodata if source.nodata is not None else np.nan, dtype=np.float64)
                    if valid.any():
                        rows, cols = np.nonzero(valid)
                        cols, rows = cols + window.col_off + .5, rows + window.row_off + .5
                        transform = source.transform
                        x = transform.a * cols + transform.b * rows + transform.c
                        y = transform.d * cols + transform.e * rows + transform.f
                        z = heights[valid]
                        try:
                            tx, ty, tz = transformer.transform(x, y, z, errcheck=True)
                        except pyproj.exceptions.ProjError as exc:
                            raise VerticalDatumError(f"Accurate offline transformation failed at block row={int(window.row_off)}, col={int(window.col_off)}. Required geoid/reference-frame grids may be missing or outside coverage. Install the PROJ-reported grids locally; no output was published. PROJ: {exc}") from exc
                        tx, ty, tz = np.asarray(tx), np.asarray(ty), np.asarray(tz, dtype=np.float64)
                        if not (np.isfinite(tx).all() and np.isfinite(ty).all() and np.isfinite(tz).all()):
                            raise VerticalDatumError("PROJ returned nonfinite coordinates/heights; no output was published")
                        tolerance = 1e-8 if horizontal.is_geographic else 1e-4
                        if not (np.allclose(tx, x, rtol=0, atol=tolerance) and np.allclose(ty, y, rtol=0, atol=tolerance)):
                            raise VerticalDatumError("PROJ changed horizontal positions; refusing to attach converted heights to the unchanged raster grid")
                        operation = _audit_operation(transformer)
                        operations[operation["definition"]] = operation
                        if source.nodata is not None and math.isfinite(source.nodata) and np.any(tz == source.nodata):
                            raise VerticalDatumError("A converted valid height equals the preserved nodata sentinel; choose a source with a noncolliding nodata value")
                        values[valid] = tz
                        delta = tz - z
                        minimum, maximum = min(minimum, float(delta.min())), max(maximum, float(delta.max()))
                        delta_sum += float(delta.sum(dtype=np.float64))
                        converted_pixels += int(tz.size)
                    destination.write(values, 1, window=window)
                    destination.write_mask(valid.astype(np.uint8) * 255, window=window)
                if converted_pixels == 0:
                    raise VerticalDatumError("Input contains no valid absolute elevations to convert")
                source_stat = input_path.stat()
                report = {
                    "source": str(input_path), "output": str(output_path), "source_datum": source_datum,
                    "target_datum": target_datum, "height_units": "metre", "height_kind": "absolute",
                    "converted_pixels": converted_pixels, "correction_m": {
                        "min": minimum, "max": maximum, "mean": delta_sum / converted_pixels},
                    "created_utc": datetime.now(timezone.utc).isoformat(), "source_size_bytes": source_stat.st_size,
                    "source_mtime_ns": source_stat.st_mtime_ns, "pyproj_version": pyproj.__version__,
                    "proj_version": pyproj.proj_version_str, "network_enabled": False,
                    "only_best": True, "allow_ballpark": False, "pixel_location": "center",
                    "grid_dir": str(Path(grid_dir).resolve()) if grid_dir else None,
                    "horizontal_crs_wkt": horizontal.to_wkt(), "source_crs_wkt": conversion_source.to_wkt(),
                    "target_crs_wkt": conversion_target.to_wkt(), "operations": list(operations.values()),
                    "source_declaration": source_info,
                }
                retained = {key: value for key, value in input_tags.items()
                            if not key.upper().startswith(("STATISTICS_", "VERTICAL_"))
                            and key.upper() not in {"HEIGHT_DATUM", "HEIGHT_TYPE", "UNITS", "DESCRIPTION"}}
                destination.update_tags(**retained)
                destination.update_tags(VERTICAL_DATUM=target_datum,
                                        VERTICAL_EPSG=str(DATUMS[target_datum] or ""),
                                        VERTICAL_CRS_WKT=conversion_target.to_wkt(),
                                        UNITS="metre", HEIGHT_KIND="absolute",
                                        HEIGHT_TYPE="ellipsoidal" if target_datum == "ellipsoidal" else "orthometric",
                                        SOFTWARE="DepthWizard vertical datum conversion",
                                        DESCRIPTION=f"Absolute elevations converted from {source_datum} to {target_datum}")
                destination.update_tags(ns="DEPTHWIZARD_VERTICAL_DATUM", PROVENANCE=json.dumps(report),
                                        SOURCE_METADATA=json.dumps(input_tags), SOURCE_BAND_METADATA=json.dumps(input_band_tags),
                                        SOURCE_BAND_DESCRIPTION=source.descriptions[0] or "")
                destination.set_band_description(1, f"Absolute {target_datum} elevation (metre)")
                destination.set_band_unit(1, "metre")
            # Fail if this GDAL/libgeotiff build discarded the vertical CRS.
            with rasterio.Env(GDAL_PAM_ENABLED=False), rasterio.open(temporary) as written:
                written_crs = CRS.from_wkt(written.crs.to_wkt(version="WKT2_2019")) if written.crs else None
                if written_crs is None or not _horizontal_crs(written_crs).equals(horizontal, ignore_axis_order=True):
                    raise VerticalDatumError("GeoTIFF writer did not preserve the horizontal CRS")
                if _crs_datum(written_crs) != target_datum:
                    raise VerticalDatumError("GeoTIFF writer did not retain the requested vertical CRS; GDAL with GeoTIFF 1.1 support is required")
                if written.transform != source.transform or (written.width, written.height) != (source.width, source.height):
                    raise VerticalDatumError("GeoTIFF writer did not preserve the source pixel grid")
        # A hard link is atomic and cannot replace an existing destination.
        # The temporary is on the same filesystem as the requested output.
        os.link(temporary, output_path)
        temporary.unlink()
        temporary = None
        return report
    finally:
        if temporary is not None:
            temporary.unlink(missing_ok=True)
