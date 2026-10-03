# Explicit vertical datum conversion

`scripts/convert_vertical_datum.py` creates a separate elevation GeoTIFF from a supplied DEM, DTM or absolute DSM. It is independent of calibration and benchmark validation. Use the resulting file deliberately as the DEM/reference in a later run; the command does not update manifests or existing scene files.

Supported height datums are `ellipsoidal`, `EGM96` (vertical EPSG:5773) and `EGM2008` (vertical EPSG:3855). Both source and target must be stated and must differ. Ellipsoidal heights refer to the ellipsoid of the raster's existing horizontal geodetic datum; a projected raster is not automatically relabelled WGS84.

The input must contain one absolute elevation band in metres, an affine pixel grid and a geographic or projected horizontal CRS. `--height-kind absolute` is an explicit declaration of those height semantics. If vertical datum metadata or a supported vertical CRS is absent, `--attest-source-datum` is also required after checking the source documentation. Conflicting metadata cannot be overridden. Rasters marked as nDSM, AGL, above ground height, differential height or uncertainty are rejected because their values are differences, rather than absolute elevations.

```powershell
python scripts/convert_vertical_datum.py supplied_dem.tif supplied_dem_egm2008.tif --source-datum EGM96 --target-datum EGM2008 --height-kind absolute
```

For a documented ellipsoidal source that carries only a horizontal CRS:

```powershell
python scripts/convert_vertical_datum.py gnss_dem.tif gnss_dem_egm2008.tif --source-datum ellipsoidal --target-datum EGM2008 --height-kind absolute --attest-source-datum --grid-dir C:\geodesy\proj-grids
```

An EGM2008 raster can also be converted to EGM96 or ellipsoidal heights by changing the explicit target. `EPSG:5773` and `EPSG:3855` are accepted aliases.

## Offline accuracy requirements

PROJ networking is disabled during conversion, including when the shell has `PROJ_NETWORK=ON`. `--grid-dir` adds an existing local grid directory for that conversion; the command never fetches geoid grids. Install the exact grids required by your PROJ database before running it. Common PROJ distributions use `us_nga_egm96_15.tif` and `us_nga_egm08_25.tif`; additional horizontal reference-frame grids may be needed for non-WGS84 input datums. Availability and the operation selected by PROJ determine whether a conversion is possible.

The transformer uses `allow_ballpark=False`, `only_best=True` and checked transformations. PROJ must be at least 9.2 and pyproj at least 3.5 to enforce that policy. Missing grids, invalid grid coverage, a ballpark operation, an identity/optional grid fallback, or altered horizontal positions cause failure. An actual vertical grid shift must appear in each selected operation. The correction is evaluated at every valid pixel center, rather than being represented by a scene mean offset. These controls follow the [pyproj transformer API](https://pyproj4.github.io/pyproj/stable/api/transformer.html), [network API](https://pyproj4.github.io/pyproj/stable/api/network.html) and [CRS API](https://pyproj4.github.io/pyproj/stable/api/crs/crs.html).

No output is published if conversion fails. The output path must be new. The completed temporary GeoTIFF is published with an atomic filesystem hard link; a filesystem supporting local hard links is required. Existing source, reference and destination files are never replaced.

## Output and provenance

The output is a compressed, tiled Float64 GeoTIFF with the original raster width, height, affine transform, horizontal CRS and nodata value. Input nodata/masks remain excluded. Band scale and offset are applied before height conversion; output samples directly contain metre heights. Nonfinite input samples are masked. Valid output heights that collide with a finite nodata sentinel are rejected.

Orthometric targets carry the original horizontal CRS combined with their EPSG vertical CRS. Ellipsoidal targets carry the corresponding three-dimensional CRS with an upward metre ellipsoidal height axis. The file uses GeoTIFF 1.1, then checks that GDAL retained the pixel grid and requested vertical CRS before publication. A GDAL/libgeotiff build that cannot preserve the vertical component fails explicitly. The relevant storage behavior is described in the [GDAL GeoTIFF driver documentation](https://gdal.org/en/stable/drivers/raster/gtiff.html).

Default tags include `VERTICAL_DATUM`, `VERTICAL_CRS_WKT`, `HEIGHT_KIND=absolute` and `UNITS=metre`, plus `VERTICAL_EPSG` for orthometric targets. Ellipsoidal height is an axis of a three-dimensional CRS, rather than a separate EPSG vertical CRS. The `DEPTHWIZARD_VERTICAL_DATUM` metadata namespace stores JSON provenance: source file path/size/modification time, explicit declarations, source/target CRS WKT, pyproj/PROJ versions, actual operation definitions and grid names, conversion policy and correction min/max/mean. Those correction statistics describe the pixelwise conversion; the mean is not used to adjust the raster. Original default and band metadata are retained inside the provenance namespace. The CLI prints the same conversion report as JSON.

The reusable API is:

```python
from depthwizard.vertical_datum import convert_raster_vertical_datum

report = convert_raster_vertical_datum(
    "input.tif", "output_egm2008.tif",
    source_datum="ellipsoidal", target_datum="EGM2008",
    height_kind="absolute", attest_source_datum=True,
    grid_dir="local_proj_grids", block_size=512,
)
```

The API changes PROJ network/data-path settings temporarily and restores them afterward; invoke it serially. It does not convert feet, infer an unknown datum, solve missing georeferencing, support GCP/RPC grids, resample a reference, or establish the accuracy of the original elevations. Raster datum conversion has not been validated against an independent geodetic control dataset in this implementation.
