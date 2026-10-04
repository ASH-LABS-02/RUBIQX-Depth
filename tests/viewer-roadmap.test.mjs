import {test} from 'node:test';
import assert from 'node:assert/strict';
import {createFrameBudget} from '../web/frame-budget.js';
import {coordinateAt,coordinateGrid,createCoordinateProbe} from '../web/coordinates.js';

test('frame budget degrades gradually and recovers with hysteresis',()=>{
  const b=createFrameBudget({windowSize:3,targetMs:30});
  assert.equal(b.record(60,0),null);b.record(60,1);
  assert.equal(b.record(60,2).level,1);
  b.record(60,5003);b.record(60,5004);assert.equal(b.record(60,5005).level,2);
  b.record(10,10006);b.record(10,10007);assert.equal(b.record(10,10008).level,1);
  b.reset();assert.equal(b.level,0);
});
test('idle and hidden gaps cannot downgrade quality',()=>{
  const b=createFrameBudget({windowSize:3});
  for(let i=0;i<100;i++){assert.equal(b.record(100,i,false),null);assert.equal(b.record(500,i),null);}
  assert.equal(b.level,0);
});
test('coordinate grid respects a rotated source affine',()=>{
  const meta={georeferenced:true,crs:'EPSG:4326',src_w:100,src_h:100,transform:[.01,.002,70,.001,-.01,20]};
  const p=coordinateAt(meta,.5,.5);assert.equal(p.E,70.6);assert.equal(p.N,19.55);
  assert.ok(coordinateGrid(meta).every(l=>l.uv.length===2));
});
test('coordinate probe discards results after a scene switch',async()=>{
  let id='before',received=0;
  const p=createCoordinateProbe({getScene:()=>id,onResult:()=>received++,fetchApi:async()=>{
    id='after';return {ok:true,json:async()=>({points:[[70,20]]})};
  }});
  p.probe(.5,.5);await new Promise(resolve=>setTimeout(resolve,120));
  assert.equal(received,0);p.reset();
});
