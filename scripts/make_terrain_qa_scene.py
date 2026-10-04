"""Create a clearly labelled synthetic large scene for streaming QA, never a benchmark."""
import argparse
import json
from pathlib import Path
import sys
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import numpy as np
from rasterio.crs import CRS
from rasterio.transform import from_origin
from depthwizard.io import InputImage,write_dsm,export_viewer_assets,save_preview

def main():
    ap=argparse.ArgumentParser(description=__doc__)
    ap.add_argument('--out',type=Path,required=True);ap.add_argument('--size',type=int,default=3072)
    a=ap.parse_args()
    if not 2048<a.size<=4096 or a.out.exists():
        raise SystemExit('Use a new output directory and size 2049–4096')
    a.out.mkdir(parents=True)
    y,x=np.mgrid[:a.size,:a.size].astype(np.float32)/a.size
    z=(100+70*np.exp(-((x-.6)**2+(y-.4)**2)*15)+30*x).astype(np.float32)
    rgb=np.stack([70+100*x,90+100*y,60+40*((np.floor(x*16)+np.floor(y*16))%2)],axis=-1).astype(np.uint8)
    image=InputImage(rgb=rgb,path=a.out/'synthetic.tif',georeferenced=True,crs=CRS.from_epsg(32643),
                     transform=from_origin(500000,1400000,1,1),pixel_size_m=1)
    meta={'input':'Synthetic terrain QA (not model inference)','input_kind':'elevation_raster','units':'metre',
          'georeferenced':True,'crs':'EPSG:32643','transform':list(image.transform)[:6],
          'vertical_datum':'synthetic arbitrary heights; not surveyed','backbone':'none: synthetic QA',
          'calibration':{'method':'input-dem','evidence_level':'synthetic QA only'},'scene':'terrain'}
    write_dsm(a.out/'dsm.tif',z,image,units='metre',description='Synthetic streaming QA surface',compound_vertical=False)
    write_dsm(a.out/'dtm.tif',z,image,units='metre',description='Synthetic streaming QA ground',compound_vertical=False)
    export_viewer_assets(a.out/'viewer',image,z,meta,dtm=z)
    save_preview(a.out/'preview.png',z)
    (a.out/'meta.json').write_text(json.dumps(meta,indent=2))
    (a.out/'job.json').write_text(json.dumps({'name':'Synthetic streaming QA · not an estimate'}))
    print(a.out.resolve())

if __name__=='__main__':main()
