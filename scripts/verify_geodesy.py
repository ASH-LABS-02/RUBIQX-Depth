"""Numerical geoid sign/interpolation and raster round-trip checks with official PROJ grids."""
import argparse
import json
from pathlib import Path
import sys
import urllib.request
sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
import numpy as np
import rasterio
from rasterio.transform import from_origin
from pyproj import Transformer,datadir,network
from depthwizard.vertical_datum import convert_raster_vertical_datum


def bilinear(src,lon,lat):
    col,row = ~src.transform * (lon,lat)
    col,row = col-.5,row-.5
    c,r = int(np.floor(col)),int(np.floor(row))
    arr = src.read(1,window=rasterio.windows.Window(c,r,2,2))
    x,y=col-c,row-r
    return float(arr[0,0]*(1-x)*(1-y)+arr[0,1]*x*(1-y)+arr[1,0]*(1-x)*y+arr[1,1]*x*y)


def main():
    ap=argparse.ArgumentParser(description=__doc__)
    ap.add_argument('--grid-dir',type=Path,required=True)
    ap.add_argument('--out',type=Path,required=True)
    ap.add_argument('--download',action='store_true',help='download two official geoid grids (~80 MB)')
    a=ap.parse_args();a.grid_dir.mkdir(parents=True,exist_ok=True);a.out.mkdir(parents=True,exist_ok=True)
    for name in ['us_nga_egm96_15.tif','us_nga_egm08_25.tif']:
        target=a.grid_dir/name
        if not target.is_file() and a.download:
            print('Downloading official PROJ grid',name,flush=True)
            with urllib.request.urlopen('https://cdn.proj.org/'+name,timeout=30) as src,target.with_suffix('.part').open('wb') as dst:
                while block:=src.read(1024*1024): dst.write(block)
            target.with_suffix('.part').replace(target)
        if not target.is_file(): raise SystemExit('Missing '+name)
    datadir.append_data_dir(str(a.grid_dir));network.set_network_enabled(False)
    points=[(-77.07,38.92),(77.59,12.97),(79.65,30.61),(6.95,50.94)]
    results=[]
    for epsg,name in [(5773,'us_nga_egm96_15.tif'),(3855,'us_nga_egm08_25.tif')]:
        tr=Transformer.from_crs('EPSG:4979',f'EPSG:4326+{epsg}',always_xy=True,allow_ballpark=False,only_best=True)
        with rasterio.open(a.grid_dir/name) as src:
            for lon,lat in points:
                _,_,height=tr.transform(lon,lat,100,errcheck=True)
                expected=100-bilinear(src,lon,lat)
                error=abs(height-expected)
                if error>1e-4: raise RuntimeError(f'Geoid sign/interpolation mismatch: {error} m')
                results.append({'lon':lon,'lat':lat,'datum':epsg,'height_m':height,'manual_bilinear_error_m':error})
    raster=a.out/'ellipsoid.tif'
    if raster.exists(): raise SystemExit('Use a fresh output directory')
    values=np.arange(16,dtype=np.float64).reshape(4,4)+100
    with rasterio.open(raster,'w',driver='GTiff',height=4,width=4,count=1,dtype='float64',crs='EPSG:4979',transform=from_origin(77.58,12.98,.005,.005)) as dst:
        dst.write(values,1);dst.update_tags(UNITS='metre',VERTICAL_DATUM='ellipsoidal')
    reports=[]
    reports.append(convert_raster_vertical_datum(raster,a.out/'egm2008.tif',source_datum='ellipsoidal',target_datum='EGM2008',height_kind='absolute',grid_dir=a.grid_dir))
    reports.append(convert_raster_vertical_datum(a.out/'egm2008.tif',a.out/'roundtrip.tif',source_datum='EGM2008',target_datum='ellipsoidal',height_kind='absolute',grid_dir=a.grid_dir))
    with rasterio.open(a.out/'roundtrip.tif') as src:
        error=float(np.max(np.abs(src.read(1)-values)))
    if error>1e-6: raise RuntimeError('Raster round-trip exceeds 1 micrometre')
    (a.out/'verification.json').write_text(json.dumps({'points':results,'roundtrip_max_error_m':error,
        'conversions':reports,'scope':'numerical consistency, not survey/control-point certification'},indent=2))
    print('Geodesy checks passed; roundtrip max error',error,flush=True)


if __name__=='__main__':main()
