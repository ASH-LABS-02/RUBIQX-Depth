// Standalone frontend regression checks: node --test tests/import-metadata.test.mjs
import {test} from 'node:test';
import assert from 'node:assert/strict';
import {inspectTiff} from '../web/import-metadata.js';

function fixture({little=true,big=false,bands=3,crs=32643,unit=9001,scale=.6,geographic=false,spatial=true}={}) {
  const keys=[1,1,0,3,1024,0,1,geographic?2:1,geographic?2048:3072,0,1,crs,geographic?2054:3076,0,1,unit];
  const tags=[[256,4,[1024]],[257,4,[512]],[277,3,[bands]]];
  if(spatial)tags.push([33550,12,[scale,scale,0]],[33922,12,[0,0,0,78,28,0]],[34735,3,keys]);
  const buf=new ArrayBuffer(1024),v=new DataView(buf),ifd=big?16:8,size=big?20:12,offsetSize=big?8:4;
  v.setUint16(0,little?0x4949:0x4d4d);v.setUint16(2,big?43:42,little);
  if(big){v.setUint16(4,8,little);v.setBigUint64(8,16n,little);v.setBigUint64(ifd,BigInt(tags.length),little);}else{v.setUint32(4,ifd,little);v.setUint16(ifd,tags.length,little);}
  let next=ifd+(big?8:2)+tags.length*size+8;
  tags.forEach(([tag,type,values],i)=>{
    const at=ifd+(big?8:2)+i*size,bytes=type===3?2:type===4?4:8;
    v.setUint16(at,tag,little);v.setUint16(at+2,type,little);
    if(big)v.setBigUint64(at+4,BigInt(values.length),little);else v.setUint32(at+4,values.length,little);
    let dest=at+(big?12:8);
    if(values.length*bytes>offsetSize){if(big)v.setBigUint64(dest,BigInt(next),little);else v.setUint32(dest,next,little);dest=next;next+=values.length*bytes;}
    values.forEach((n,j)=>{if(type===3)v.setUint16(dest+j*bytes,n,little);else if(type===4)v.setUint32(dest+j*bytes,n,little);else v.setFloat64(dest+j*bytes,n,little);});
  });
  return new Blob([buf]);
}
for(const little of [true,false])for(const big of [true,false])test(`${little?'little':'big'} endian ${big?'BigTIFF':'classic TIFF'}`,async()=>{
  const m=await inspectTiff(fixture({little,big}));assert.equal(m.crs,'EPSG:32643');assert.equal(m.width,1024);assert.equal(m.height,512);assert.equal(m.gsd.x,.6);assert.equal(m.inputDem,false);
});
test('single-band input DEM',async()=>assert.equal((await inspectTiff(fixture({bands:1}))).inputDem,true));
test('plain TIFF remains relative',async()=>{const m=await inspectTiff(fixture({spatial:false}));assert.equal(m.georeferenced,false);assert.equal(m.gsd,null);});
test('feet are converted to metres',async()=>assert.equal((await inspectTiff(fixture({crs:26985,unit:9002,scale:2}))).gsd.x,.6096));
test('degrees get approximate ground spacing',async()=>{const m=await inspectTiff(fixture({geographic:true,crs:4326,unit:9102,scale:.00001}));assert.equal(m.gsd.approximate,true);assert.ok(m.gsd.x>.9&&m.gsd.x<1.1);});
test('unknown CRS units are not labelled metres',async()=>assert.equal((await inspectTiff(fixture({crs:32700,unit:0}))).gsd,null));
test('truncated header rejects',async()=>assert.rejects(()=>inspectTiff(new Blob([new Uint8Array([0,0,0,0])]))));
