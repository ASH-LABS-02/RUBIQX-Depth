// Read TIFF metadata only. Pixels and final geospatial interpretation stay on the server.
// No decoder, network request or third-party dependency is needed for the import preview.
export async function inspectTiff(file) {
  const read = async (offset, size) => {
    if (!Number.isSafeInteger(offset) || offset < 0 || size < 0 || offset + size > file.size)
      throw new Error('Incomplete TIFF header; spatial metadata will be checked during processing.');
    return new DataView(await file.slice(offset, offset + size).arrayBuffer());
  };
  const header = await read(0, Math.min(16, file.size));
  const order = header.getUint16(0);
  if (order !== 0x4949 && order !== 0x4d4d) throw new Error('This file does not have a TIFF header.');
  const little = order === 0x4949, magic = header.getUint16(2, little);
  const big = magic === 43;
  if (magic !== 42 && magic !== 43) throw new Error('Unsupported TIFF header.');
  if (big && (header.getUint16(4, little) !== 8 || header.getUint16(6, little) !== 0))
    throw new Error('Unsupported BigTIFF layout.');
  const uint64 = (view, off) => Number(view.getBigUint64(off, little));
  const offset = big ? uint64(header, 8) : header.getUint32(4, little);
  const countView = await read(offset, big ? 8 : 2);
  const count = big ? uint64(countView, 0) : countView.getUint16(0, little);
  if (count > 4096) throw new Error('TIFF metadata directory is too large for a quick preview.');
  const entrySize = big ? 20 : 12, inlineSize = big ? 8 : 4;
  const directory = await read(offset + (big ? 8 : 2), count * entrySize);
  const wanted = new Set([256,257,258,277,339,33550,33922,34264,34735]);
  const types = {1:1,3:2,4:4,12:8,16:8};
  const tags = {};
  for (let i = 0; i < count; i++) {
    const at = i * entrySize, tag = directory.getUint16(at,little);
    if (!wanted.has(tag)) continue;
    const type = directory.getUint16(at+2,little), size = types[type];
    if (!size) continue;
    const n = big ? uint64(directory,at+4) : directory.getUint32(at+4,little);
    if (n > 8192) throw new Error('TIFF tag is too large for a quick preview.');
    const valueAt = at + (big ? 12 : 8);
    const source = n*size <= inlineSize ? directory : await read(
      big ? uint64(directory,valueAt) : directory.getUint32(valueAt,little), n*size);
    const start = source === directory ? valueAt : 0;
    tags[tag] = Array.from({length:n},(_,j) => {
      const p = start+j*size;
      return type===1 ? source.getUint8(p) : type===3 ? source.getUint16(p,little)
        : type===4 ? source.getUint32(p,little) : type===12 ? source.getFloat64(p,little) : uint64(source,p);
    });
  }
  const keys = {};
  const geo = tags[34735] || [];
  for (let i=0; i<(geo[3]||0); i++) {
    const k=4+i*4;
    if (geo[k+1]===0 && geo[k+2]===1) keys[geo[k]]=geo[k+3];
  }
  const code = keys[3072] || keys[2048];
  const crs = code && code!==32767 ? `EPSG:${code}` : null;
  const hasTransform = !!(tags[34264] || (tags[33550] && tags[33922]));
  const georeferenced = !!(hasTransform && geo.length);
  let sx = tags[33550]?.[0], sy = tags[33550]?.[1];
  const transform = tags[34264];
  if (transform) { sx = Math.hypot(transform[0],transform[4]); sy = Math.hypot(transform[1],transform[5]); }
  let gsd = null;
  if (Number.isFinite(sx) && sx>0 && Number.isFinite(sy) && sy>0 && georeferenced) {
    const geographic = keys[1024]===2;
    if (geographic && (!keys[2054] || keys[2054]===9102)) {
      const latitude = tags[33922]?.[4] ?? transform?.[7];
      if (Number.isFinite(latitude)) {
        sx *= 111320 * Math.cos(latitude*Math.PI/180); sy *= 111132;
        gsd = {x:sx,y:sy,approximate:true};
      }
    } else if (!geographic) {
      // Only report metres when the CRS unit is explicitly supported.
      const metres = keys[3076]===9001 ? 1 : keys[3076]===9002 ? .3048
        : keys[3076]===9003 ? 1200/3937 : ((code>=32601 && code<=32660) || (code>=32701 && code<=32760)) ? 1 : null;
      if (metres) gsd = {x:sx*metres,y:sy*metres,approximate:false};
    }
  }
  const bands=tags[277]?.[0]||1,format=tags[339]?.[0]||1;
  // Match the server's elevation detector; a one-band panchromatic image
  // is not a DEM merely because it has one band.
  const named=/dem|dsm|dtm|srtm|elev|height|cop30|glo30/i.test(file.name||'');
  return {width:tags[256]?.[0],height:tags[257]?.[0],bands,
    georeferenced,crs,gsd,inputDem:bands===1&&(format===3||(format===2&&named))};
}
