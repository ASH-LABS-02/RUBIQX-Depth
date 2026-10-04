import json
import io
import threading
import numpy as np
import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from rasterio.transform import from_origin

from depthwizard.coordinates import lonlat
from depthwizard.validation import categorical,stratified
from depthwizard.rainfall import simulate_rainfall
from depthwizard.disaster import evacuation_route
from app.calibration_history import calibration_edit,undo_calibration
from app.security import install_security,credentials

def _raster(path, values, transform=None):
    import rasterio
    with rasterio.open(path,'w',driver='GTiff',width=values.shape[1],height=values.shape[0],count=1,
                       dtype='float32',crs='EPSG:32643',transform=transform or from_origin(500000,1400000,1,1),nodata=float('nan')) as dst:
        dst.write(values.astype(np.float32),1)


def test_terrain_tiles_share_edges_and_bound_requests(tmp_path):
    from PIL import Image
    from app.terrain_api import create_terrain_router
    (tmp_path/'viewer').mkdir()
    y,x=np.mgrid[:257,:257];_raster(tmp_path/'dsm.tif',x+2*y)
    Image.new('RGB',(257,257),'green').save(tmp_path/'viewer/texture.jpg')
    app=FastAPI();app.include_router(create_terrain_router(lambda _:tmp_path,threading.Lock()))
    client=TestClient(app)
    left=client.get('/api/scenes/s/tiles/1/0/0/height?size=129')
    right=client.get('/api/scenes/s/tiles/1/1/0/height?size=129')
    assert left.status_code==right.status_code==200
    a=np.frombuffer(left.content,'<f4').reshape(129,129);b=np.frombuffer(right.content,'<f4').reshape(129,129)
    assert np.allclose(a[:,-1],b[:,0],atol=1e-5)
    assert client.get('/api/scenes/s/tiles/8/0/0/height').status_code==422
    assert client.get('/api/scenes/s/tiles/0/0/0/height?size=4096').status_code==422
    texture=client.get('/api/scenes/s/tiles/1/0/0/texture')
    assert Image.open(io.BytesIO(texture.content)).size==(128,128)


def test_route_upload_requires_exact_grid_and_binary_values(tmp_path):
    from app.mission_api import create_mission_router
    _raster(tmp_path/'dtm.tif',np.zeros((8,8)))
    app=FastAPI();app.include_router(create_mission_router(lambda _:tmp_path,threading.Lock()))
    client=TestClient(app)
    path=tmp_path/'mask.tif';_raster(path,np.ones((8,8)))
    response=client.post('/api/scenes/s/route-input/access',files={'mask':('mask.tif',path.read_bytes(),'image/tiff')})
    assert response.status_code==200 and response.json()['known_cells']==64
    assert (tmp_path/'route_access.tif').is_file()
    _raster(path,np.ones((8,8))*2)
    assert client.post('/api/scenes/s/route-input/roads',files={'mask':('mask.tif',path.read_bytes())}).status_code==422
    _raster(path,np.ones((8,8)),from_origin(500001,1400000,1,1))
    assert client.post('/api/scenes/s/route-input/roads',files={'mask':('mask.tif',path.read_bytes())}).status_code==422


def test_cancelled_job_retries_saved_inputs_and_missing_input_is_atomic(monkeypatch,tmp_path):
    import app.server as srv
    from types import SimpleNamespace
    monkeypatch.setattr(srv,'JOBS',tmp_path);monkeypatch.setattr(srv,'_status',{})
    monkeypatch.setattr(srv,'_queue_order',[])
    queued=[];monkeypatch.setattr(srv,'_jobs_q',SimpleNamespace(put=queued.append))
    old=tmp_path/'old';(old/'inputs').mkdir(parents=True)
    image=old/'inputs/image.png';image.write_bytes(b'original input')
    (old/'request.json').write_text(json.dumps({'image_path':str(image),'out_dir':str(old)}))
    (old/'job.json').write_text(json.dumps({'name':'test'}))
    srv._status['old']={'state':'queued','log':[]};srv._queue_order.append('old')
    client=TestClient(srv.app)
    assert client.post('/api/jobs/old/cancel').json()['state']=='cancelled'
    assert srv._queue_order==[]
    new=client.post('/api/jobs/old/retry').json()['id']
    assert queued[0][0]==new and queued[0][1]['out_dir']==str(tmp_path/new)
    assert (tmp_path/new/'inputs/image_path/image.png').read_bytes()==b'original input'
    before=set(tmp_path.iterdir());image.unlink()
    assert client.post('/api/jobs/old/retry').status_code==409
    assert set(tmp_path.iterdir())==before


def test_cancel_during_failure_cannot_terminate_worker(monkeypatch,tmp_path):
    import app.server as srv
    monkeypatch.setattr(srv,'JOBS',tmp_path);(tmp_path/'broken').mkdir()
    state={'state':'queued','log':[]};monkeypatch.setattr(srv,'_status',{'broken':state})
    def run(**_):
        state['cancel_requested']=True
        raise RuntimeError('inference failure')
    monkeypatch.setattr(srv,'run',run)
    srv._run_job('broken',{})
    assert state['state']=='error' and 'inference failure' in state['error']


def test_unlicensed_semantics_fail_closed_in_production(monkeypatch,tmp_path):
    from depthwizard.semantic import SemanticSegmenter
    monkeypatch.setenv('DEPTHWIZARD_PRODUCTION','1')
    with pytest.raises(ValueError,match='licen'):
        SemanticSegmenter(tmp_path)


def test_metric_normalisation_preserves_default_and_opt_in_tail():
    from depthwizard.depth import DepthBackbone
    model=DepthBackbone.__new__(DepthBackbone)
    model.agl=True
    metric=np.arange(10000,dtype=np.float32).reshape(100,100)/100
    model.predict_agl_metric=lambda *_args,**_kwargs:(metric,np.ones_like(metric),.65)
    rgb=np.zeros((100,100,3),dtype=np.uint8)
    low,high=np.percentile(metric,(.5,99.5))
    expected=np.clip((metric-low)/(high-low),0,1).astype(np.float32)
    default=model.predict(rgb,gsd=.65)
    assert np.array_equal(default,expected)
    assert 'metric_before_normalisation' not in model.info
    tail=model.predict(rgb,gsd=.65,preserve_metric_tail=True,audit_raw=True)
    assert tail.max()>1 and tail.min()==0
    assert np.array_equal(model.info['metric_before_normalisation'],metric)


def test_stage_audit_is_opt_in_and_reference_free(monkeypatch,tmp_path):
    import depthwizard.stage_audit as module
    recorded=[]
    monkeypatch.setattr(module,'write_dsm',lambda path,arr,_image,**kw:recorded.append((path,arr.copy(),kw)))
    values=np.array([[1,np.nan],[3,5]],dtype=np.float32)
    disabled=module.StageAudit(tmp_path,None)
    disabled.capture('raw',values,'m')
    assert not recorded and not (tmp_path/'stages').exists()
    enabled=module.StageAudit(tmp_path,None,enabled=True)
    enabled.capture('raw',values,'m',scale=2)
    manifest=json.loads((tmp_path/'stages/manifest.json').read_text())
    assert manifest['reference_used'] is False
    assert manifest['stages'][0]['valid_pixels']==3
    assert manifest['stages'][0]['p50']==6
    assert np.nanmax(recorded[0][1])==10


def test_mission_reads_integer_mask_nodata(tmp_path):
    import rasterio
    from app.mission_api import _read_raster
    path=tmp_path/'integer-mask.tif'
    with rasterio.open(path,'w',driver='GTiff',width=2,height=2,count=1,dtype='uint8',
                       transform=from_origin(500000,1400000,1,1),nodata=255) as dst:
        dst.write(np.array([[1,0],[255,1]],dtype=np.uint8),1)
    values,_=_read_raster(path)
    assert values.dtype==np.float32 and np.isnan(values[1,0])


def test_build_identity_refreshes_changed_ui_hash(monkeypatch,tmp_path):
    from app.identity import build_identity
    monkeypatch.setenv('DEPTHWIZARD_BUILD_SHA','test-build')
    (tmp_path/'web').mkdir()
    path=tmp_path/'web/app.js';path.write_text('first')
    first=build_identity(str(tmp_path))
    path.write_text('second, longer')
    second=build_identity(str(tmp_path))
    assert first['commit']==second['commit']=='test-build'
    assert first['ui_sha256']!=second['ui_sha256']


def test_geographic_affine_rotation():
    meta={'src_w':100,'src_h':100,'transform':[.01,.001,70,.002,-.01,20],'crs':'EPSG:4326'}
    assert lonlat(meta,[(.5,.5)])[0]==pytest.approx([70.55,19.6])
    with pytest.raises(ValueError):lonlat(meta,[(1.1,.5)])


def test_projected_crs_not_corner_interpolation():
    meta={'src_w':2000,'src_h':2000,'transform':[1000,0,0,0,-1000,8000000],'crs':'EPSG:3857'}
    center=lonlat(meta,[(.5,.5)])[0]
    corners=lonlat(meta,[(0,0),(1,1)])
    assert abs(center[1]-(corners[0][1]+corners[1][1])/2)>.1


def test_unknown_semantic_predictions_are_misses():
    scores=categorical(np.array([0,4,1]),np.array([4,4,1]),classes=[4])
    assert scores['4']['recall']==.5


def test_uncertainty_coverage_includes_bias_and_classes():
    truth=np.array([0.,20.,35.]);pred=truth+5
    result=stratified(pred,truth,agl=truth,labels=np.array([3,1,2]),sigma=np.ones(3))
    assert result['uncertainty_coverage']['all']['one_sigma']==0
    assert result['classes']['2']['bias']==5


def test_rainfall_volume_and_drainage_conservation():
    z=np.zeros((8,8))
    result=simulate_rainfall(z,2,rainfall_mm=50,duration_min=60,infiltration_mm_hr=10,drainage_mm_hr=5)
    assert result['stored_volume_m3']==pytest.approx(.035*64*4,abs=1e-7)
    assert abs(result['mass_balance_error_m3'])<1e-7
    assert result['depth'].min()>=0


def test_runoff_conservation_and_nodata_barrier():
    z=np.tile(np.arange(12),(12,1)).astype(float)
    z[:,6]=np.nan
    result=simulate_rainfall(z,1,rainfall_mm=200,infiltration_mm_hr=0,steps=60)
    assert abs(result['mass_balance_error_m3'])<1e-7
    assert np.all(result['depth'][:,6]==0)
    assert result['depth'][0,0]>result['depth'][0,5]


def test_route_access_and_uncertainty_explain_rejection():
    terrain=np.tile(np.linspace(0,5,20),(20,1))
    access=np.ones(terrain.shape,bool);access[10,10]=False
    r=evacuation_route(terrain,(10,10),-1,1,access_mask=access)
    assert r['status']=='blocked_start' and r['blocked_access_cells']==1
    r=evacuation_route(terrain,(10,10),-1,1,uncertainty_m=np.full(terrain.shape,10))
    assert r['status']=='blocked_start' and r['blocked_uncertainty_cells']==400


def test_calibration_undo_and_failed_edit_roll_back(tmp_path):
    (tmp_path/'meta.json').write_text('before')
    with calibration_edit(tmp_path):(tmp_path/'meta.json').write_text('after')
    assert undo_calibration(tmp_path)['remaining']==0
    assert (tmp_path/'meta.json').read_text()=='before'
    with pytest.raises(RuntimeError):
        with calibration_edit(tmp_path):
            (tmp_path/'meta.json').write_text('broken');raise RuntimeError('stop')
    assert (tmp_path/'meta.json').read_text()=='before'


def test_auth_and_cross_origin_guard(monkeypatch):
    monkeypatch.setenv('DEPTHWIZARD_AUTH_USER','judge')
    monkeypatch.setenv('DEPTHWIZARD_AUTH_PASSWORD','a-long-test-password')
    app=FastAPI();install_security(app)
    @app.post('/edit')
    def edit():return {'ok':True}
    client=TestClient(app)
    assert client.post('/edit').status_code==401
    assert client.post('/edit',auth=('judge','a-long-test-password')).status_code==200
    assert client.post('/edit',auth=('judge','a-long-test-password'),headers={'Origin':'https://evil.example'}).status_code==403
    monkeypatch.setenv('DEPTHWIZARD_PRODUCTION','1');monkeypatch.setenv('DEPTHWIZARD_AUTH_PASSWORD','short')
    with pytest.raises(RuntimeError):credentials()
