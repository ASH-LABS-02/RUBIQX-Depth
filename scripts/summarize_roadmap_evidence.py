"""Summarise the measured development experiments; never train, fit or promote a model."""
import argparse
import hashlib
import json
import math
from pathlib import Path

def sha(path):
    h=hashlib.sha256()
    with path.open('rb') as src:
        while block:=src.read(1024*1024):h.update(block)
    return h.hexdigest()

def resolution(rows):
    output=[]
    for gsd in sorted({r['gsd_m'] for r in rows}):
        group=[r for r in rows if r['gsd_m']==gsd and r['stage']=='post_water']
        bands={}
        for band in group[0]['height_bands_m']:
            entries=[r['height_bands_m'][band] for r in group if r['height_bands_m'][band]['n']]
            n=sum(e['n'] for e in entries)
            bands[band]={'n':n,'rmse':math.sqrt(sum(e['rmse']**2*e['n'] for e in entries)/n)} if n else {'n':0}
        output.append({'gsd_m':gsd,'scene_mean_agl_rmse_m':sum(r['all']['rmse'] for r in group)/len(group),
            'pooled_height_bands':bands,'water_fraction_max':max(r['water_fraction'] for r in group)})
    return output

def main():
    ap=argparse.ArgumentParser(description=__doc__)
    ap.add_argument('--root',type=Path,default=Path('D:/DepthWizard'));ap.add_argument('--out',type=Path,required=True)
    a=ap.parse_args();ev=a.root/'evaluation'
    original=json.loads((ev/'roadmap-20261004-development/results.json').read_text())
    tail=json.loads((ev/'metric-tail-20261004/results.json').read_text())
    checkpoint=a.root/'checkpoints/da2-roadmap-tall-development-20261004'
    baseline=json.loads((checkpoint/'baseline.json').read_text())
    history=[json.loads(line) for line in (checkpoint/'history.jsonl').read_text().splitlines()]
    def selected_score(row):
        bands=row['val_height_bands']['GAMUS'];tall=[bands[k] for k in ('15-30','30-inf') if bands[k]['n']]
        return (row['val_absolute_rmse_m']+math.sqrt(sum(v['rmse']**2*v['n'] for v in tall)/sum(v['n'] for v in tall)))/2
    selected=min((r for r in history if r['val_absolute_rmse_m']<=baseline['absolute']+.05),key=selected_score)
    onnx=json.loads((ev/'onnx-20261004/benchmark.json').read_text())
    onnx['scope']='fixed 518 input; CPU forward timing excludes shared preprocessing; development parity only'
    report={'date':'2026-10-04','production_model_replaced':False,'reserved_references_used_for_fitting':False,
      'development_resolution':{'seed':original['seed'],'tiles':sorted({r['tile'] for r in original['results']}),
        'scope':'Six GAMUS validation centre crops, not an independent/domain-wide benchmark; no class labels',
        'current':resolution(original['results']),'experimental_tail':resolution(tail['results']),
        'tail_default_enabled':False,'decision':'Mean RMSE does not improve at the tested resolutions; keep OFF'},
      'candidate_training':{'split':'GAMUS train + development/val; no test tiles or DC reserved DSM fitting',
        'train_tiles':128,'validation_tiles':24,'epochs_run':len(history),'selected_hf_epoch':selected['epoch'],
        'selected_weights_sha256':sha(checkpoint/'model.safetensors'),'baseline':baseline,'selected':selected,
        'scope':'Small development experiment; height classes are AGL bands, not independent building/tree labels',
        'deployment_decision':'Not promoted: 2.5–15 m pooled RMSE worsened and canopy/independent sites remain unvalidated',
        'last_pt_note':'last.pt contains epoch 2 model/optimizer state, not the selected epoch 1 HF weights'},
      'onnx':onnx,
      'geodesy':{k:v for k,v in json.loads((ev/'geodesy-20261004/verification.json').read_text()).items() if k!='conversions'},
      'stage_audit':json.loads((ev/'stage-audit-glover-20261004/audit-scores.json').read_text())}
    report['stage_audit']['scope']='Historical related-domain Glover diagnostic only; 1 m 2024 LiDAR bilinearly aligned to 0.5 m RGB, bands use estimated/calibration DTM. Scoring only, not selection.'
    report['stage_audit']['stage_manifest_sha256']=sha(ev/'stage-audit-glover-20261004/stages/manifest.json')
    report['stage_audit']['metric_reference_sha256']=sha(ev/'stage-audit-glover-20261004/reference_scoring_grid.tif')
    a.out.parent.mkdir(parents=True,exist_ok=True)
    a.out.write_text(json.dumps(report,indent=2,allow_nan=False)+'\n',encoding='utf-8')
    (checkpoint/'selected-hf-receipt.json').write_text(json.dumps(report['candidate_training'],indent=2)+'\n')
    print(a.out.resolve())

if __name__=='__main__':main()
