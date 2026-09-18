// Regional cloud tiles for the globe: when the view is zoomed past what the 4096-wide base clip can
// show, the tiles a satellite resolved at 2.4 km (level 5) or 4.9 km (level 4) are decoded with
// WebCodecs and composited into one atlas that the planet shader samples in place of the base clip.
//
// How it fits together (see scripts/cloud_tiles.py for the producer):
//   tiles/L{5,4}.json      one index per level: codec, frame names, and for every tile the byte offset
//                          of every access unit in its Annex B stream and which of them are keyframes
//   tiles/L5/{tx}_{ty}.h264  one elementary stream per tile, 512 px, keyframe every 12 frames, no
//                          B-frames; luma = opacity, chroma = the motion to the next frame (128 = still)
// The page keeps one VideoDecoder per visible tile, fetches a keyframe group (one second of playback)
// at a time by Range request, and copies each decoded frame's planes straight into textures
// (VideoFrame.copyTo, so the browser's YUV->RGB never touches the numbers). An atlas of G x G tiles,
// two of them (the older frame and the newer), is redrawn from those textures whenever something
// changes, and the planet shader warps and mixes it exactly as it does the base clip. Where a tile
// is missing or not yet decoded the atlas is transparent and the base clip shows through, so the
// worst case is the picture we already had, never a hole. The layer fades in over 300 ms once every
// tile in view has a frame, and fades out when the level changes or the zoom no longer needs it.
(function () {
  'use strict';
  const TILE = 512, G = 4, GOP = 12, MAX_DEC = 16, RING = 3, FADE_MS = 300, LINGER_MS = 2500, BUF_CAP = 160e6;

  window.CloudTiles = function (o) {
    const renderer = o.renderer, planet = o.planet, cam = o.cam, stage = o.stage, U = o.U, base = o.base || 'tiles/';
    const levels = o.levels || [5, 4];
    const supported = !!(window.VideoDecoder && window.EncodedVideoChunk && window.VideoFrame && VideoFrame.prototype.copyTo && renderer.capabilities.isWebGL2);
    const st = { tiles: 0, decoders: 0, bytes: 0, decoded: 0, dropped: 0, sharpMs: null, level: 0, on: 0, err: null, formats: {}, updMax: 0, compMax: 0, makeMax: 0, feedMax: 0 };
    const api = { supported, stats: st, idx: {}, finestWidth: () => 4096, update: () => {}, view: () => null };
    if (!supported) return api;

    // ---- the indexes
    const idx = api.idx;
    const loading = {};
    function loadIndex(L) {
      if (loading[L]) return loading[L];
      loading[L] = fetch(`${base}L${L}.json`).then(r => r.ok ? r.json() : null).then(j => {
        if (j) { j.keySet = {}; for (const k in j.tiles) j.keySet[k] = new Set(j.tiles[k].keys); j.N = 1 << L; }
        idx[L] = j;
        if (j && j.codec) VideoDecoder.isConfigSupported({ codec: j.codec }).then(s => { if (!s.supported) { st.err = `codec ${j.codec} unsupported`; idx[L] = null; } });
        return j;
      }).catch(() => { idx[L] = null; });
      return loading[L];
    }
    levels.forEach(loadIndex);
    function has(L, tx, ty) { const j = idx[L]; return !!(j && j.tiles[`${((tx % j.N) + j.N) % j.N}_${ty}`]); }

    // ---- the atlas: G x G tiles, r = opacity, gb = flow, a = 1 where a tile has been drawn
    const rtOpts = { format: THREE.RGBAFormat, type: THREE.UnsignedByteType, minFilter: THREE.LinearFilter, magFilter: THREE.LinearFilter, depthBuffer: false, stencilBuffer: false, generateMipmaps: false };
    const rtA = new THREE.WebGLRenderTarget(G * TILE, G * TILE, rtOpts), rtB = new THREE.WebGLRenderTarget(G * TILE, G * TILE, rtOpts);
    const atlasScene = new THREE.Scene(), atlasCam = new THREE.OrthographicCamera(0, G, G, 0, 0, 1);
    const clearCol = new THREE.Color(0, 0.5, 0.5), savedCol = new THREE.Color();
    const quad = new THREE.PlaneGeometry(1, 1);
    function tileMaterial() {
      return new THREE.ShaderMaterial({
        uniforms: { tY: { value: null }, tUV: { value: null } }, depthTest: false, depthWrite: false,
        vertexShader: `varying vec2 u; void main(){ u=uv; gl_Position=projectionMatrix*modelViewMatrix*vec4(position,1.0); }`,
        // the planes are stored top row first (north); the quad's v runs south to north
        fragmentShader: `uniform sampler2D tY, tUV; varying vec2 u; void main(){ vec2 p=vec2(u.x,1.0-u.y); gl_FragColor=vec4(texture2D(tY,p).r, texture2D(tUV,p).rg, 1.0); }`
      });
    }
    function plane(w, h, channels) {
      const t = new THREE.DataTexture(new Uint8Array(w * h * channels), w, h, channels === 1 ? THREE.RedFormat : THREE.RGFormat, THREE.UnsignedByteType);
      t.minFilter = THREE.LinearFilter; t.magFilter = THREE.LinearFilter; t.generateMipmaps = false; t.unpackAlignment = 1; t.flipY = false;
      t.wrapS = THREE.ClampToEdgeWrapping; t.wrapT = THREE.ClampToEdgeWrapping; return t;
    }
    U.uTileA.value = rtA.texture; U.uTileB.value = rtB.texture;

    // ---- one tile: a decoder, a ring of decoded frames as textures, the bytes it has, its quad in the atlas
    const tiles = new Map();                                 // "L/tx_ty" -> tile
    let scratch = new Uint8Array(TILE * TILE * 3);
    function makeTile(L, tx, ty) {
      const j = idx[L], key = `${tx}_${ty}`;
      const t = { L, tx, ty, key, id: `${L}/${key}`, info: j.tiles[key], keys: j.keySet[key], n: j.frames.length,
        ring: [], next: -1, want: 0, dec: null, bufs: new Map(), pending: new Set(), bytes: 0, chain: Promise.resolve(), lastWanted: performance.now(), mesh: null };
      for (let r = 0; r < RING; r++) t.ring.push({ i: -1, y: plane(TILE, TILE, 1), uv: plane(TILE / 2, TILE / 2, 2) });
      t.mesh = new THREE.Mesh(quad, tileMaterial()); t.mesh.visible = false; t.mesh.frustumCulled = false; atlasScene.add(t.mesh);
      configure(t);
      tiles.set(t.id, t);
      return t;
    }
    function configure(t) {
      const j = idx[t.L];
      if (t.dec) { try { t.dec.close(); } catch (e) { /* already closed */ } }
      t.dec = new VideoDecoder({ output: f => onFrame(t, f), error: e => { st.err = String(e); t.dec = null; t.next = -1; } });
      t.dec.configure({ codec: j.codec, optimizeForLatency: true });
      t.next = -1;
    }
    function dropTile(t) {
      if (t.dec) { try { t.dec.close(); } catch (e) { /* closed */ } t.dec = null; }
      atlasScene.remove(t.mesh); t.mesh.material.dispose();
      for (const s of t.ring) { s.y.dispose(); s.uv.dispose(); }
      tiles.delete(t.id);
    }

    // a decoded frame: copy its planes into the oldest ring slot (Y as R8, U/V interleaved as RG8)
    function onFrame(t, frame) {
      const i = Math.round(frame.timestamp * idx[t.L].fps / 1e6);
      if (i < t.want - 1 || !tiles.has(t.id)) { frame.close(); st.dropped++; return; }           // pre-roll from the keyframe, or the tile went away
      t.chain = t.chain.then(async () => {
        try {
          const fmt = frame.format, w = frame.codedWidth, h = frame.codedHeight;
          st.formats[fmt] = (st.formats[fmt] || 0) + 1;
          const need = frame.allocationSize();
          if (scratch.byteLength < need) scratch = new Uint8Array(need);
          const layout = await frame.copyTo(scratch);
          if (!tiles.has(t.id)) return;
          let slot = t.ring[0]; for (const s of t.ring) if (s.i < slot.i) slot = s;
          copyPlane(scratch, layout[0].offset, layout[0].stride, w, h, slot.y.image.data, w);
          if (fmt === 'NV12') copyPlane(scratch, layout[1].offset, layout[1].stride, w, h >> 1, slot.uv.image.data, w);
          else if (fmt === 'I420' || fmt === 'I420A') interleave(scratch, layout[1], layout[2], w >> 1, h >> 1, slot.uv.image.data);
          else throw new Error('pixel format ' + fmt);
          slot.y.needsUpdate = true; slot.uv.needsUpdate = true; slot.i = i; st.decoded++; dirty = true;
        } catch (e) { st.err = String(e); }
        finally { frame.close(); }
      });
    }
    function copyPlane(src, off, stride, w, h, dst, dw) {
      if (stride === dw) { dst.set(src.subarray(off, off + dw * h)); return; }
      for (let r = 0; r < h; r++) dst.set(src.subarray(off + r * stride, off + r * stride + dw), r * dw);
    }
    function interleave(src, lu, lv, w, h, dst) {
      for (let r = 0; r < h; r++) { let o = r * w * 2, su = lu.offset + r * lu.stride, sv = lv.offset + r * lv.stride;
        for (let c = 0; c < w; c++) { dst[o++] = src[su + c]; dst[o++] = src[sv + c]; } }
    }

    // ---- bytes: one keyframe group at a time, by Range request
    let bufTotal = 0;
    function gopOf(k) { return Math.floor(k / GOP); }
    function ensureBytes(t, g) {
      if (g < 0 || g * GOP >= t.n || t.bufs.has(g) || t.pending.has(g)) return;
      const off = t.info.offsets, a = off[g * GOP], b = off[Math.min(t.n, (g + 1) * GOP)];
      t.pending.add(g);
      fetch(`${base}L${t.L}/${t.key}.h264`, { headers: { Range: `bytes=${a}-${b - 1}` } }).then(async r => {
        if (!r.ok) throw new Error(`tile ${t.key} ${r.status}`);
        let ab = await r.arrayBuffer();
        if (r.status === 200) ab = ab.slice(a, b);                                            // a host that ignored the Range
        if (!tiles.has(t.id)) return;
        t.bufs.set(g, new Uint8Array(ab)); t.bytes += ab.byteLength; bufTotal += ab.byteLength; st.bytes += ab.byteLength; dirtyFeed = true;
      }).catch(e => { st.err = String(e); }).finally(() => t.pending.delete(g));
    }
    function chunk(t, k) {
      const g = gopOf(k), buf = t.bufs.get(g);
      if (!buf) { ensureBytes(t, g); return null; }
      const o0 = t.info.offsets[g * GOP];
      return buf.subarray(t.info.offsets[k] - o0, t.info.offsets[k + 1] - o0);
    }
    function keyAtOrBefore(t, k) { return k - (k % GOP); }

    // feed the decoder so that frames i-1, i and i+1 come out; restart from a keyframe when the clock jumped
    function feed(t, i) {
      if (!t.dec) return;
      t.want = i;
      const hi = Math.min(t.n - 1, i + 1);
      if (t.next < 0 || t.next > i + 1 || (i - 1) - t.next > GOP * 2) {
        if (t.next >= 0) configure(t);
        t.next = keyAtOrBefore(t, Math.max(0, i - 1));
      }
      while (t.next <= hi) {
        const k = t.next, data = chunk(t, k);
        if (!data) break;                                                                       // in flight; try again when it lands
        if (t.dec.decodeQueueSize > 16) break;
        t.dec.decode(new EncodedVideoChunk({ type: t.keys.has(k) ? 'key' : 'delta', timestamp: Math.round(k * 1e6 / idx[t.L].fps), data }));
        t.next = k + 1;
      }
      ensureBytes(t, gopOf(hi) + 1);
    }

    // ---- the view: which tiles at which level
    const inv = new THREE.Matrix4(), ro = new THREE.Vector3(), rd = new THREE.Vector3(), hp = new THREE.Vector3();
    const view = { lon: 0, lat: 0, dlon: [0, 0], lat0: 0, lat1: 0, hits: 0, texel3: 0 };
    function hit(sx, sy, out) {
      rd.set(sx, sy, 0.5).unproject(cam).sub(hp.setFromMatrixPosition(cam.matrixWorld)).normalize().transformDirection(inv);
      ro.setFromMatrixPosition(cam.matrixWorld).applyMatrix4(inv);
      const b = ro.dot(rd), c = ro.dot(ro) - 1, d = b * b - c;
      if (d < 0) return false;
      hp.copy(ro).addScaledVector(rd, -b - Math.sqrt(d));
      let lon = Math.atan2(hp.z, -hp.x) * 180 / Math.PI - 180; if (lon <= -180) lon += 360;
      out[0] = lon; out[1] = Math.asin(Math.max(-1, Math.min(1, hp.y))) * 180 / Math.PI;
      return true;
    }
    const pt = [0, 0];
    function measureView() {
      planet.updateMatrixWorld(); cam.updateMatrixWorld(); inv.copy(planet.matrixWorld).invert();   // the camera may have moved this tick
      if (!hit(0, 0, pt)) { view.hits = 0; return; }
      view.lon = pt[0]; view.lat = pt[1];
      let a = 0, b = 0, lat0 = pt[1], lat1 = pt[1], hits = 1;
      for (let y = -1; y <= 1; y += 0.5) for (let x = -1; x <= 1; x += 0.5) {
        if ((x === 0 && y === 0) || !hit(x, y, pt)) continue;
        let dl = pt[0] - view.lon; if (dl > 180) dl -= 360; if (dl < -180) dl += 360;
        a = Math.min(a, dl); b = Math.max(b, dl); lat0 = Math.min(lat0, pt[1]); lat1 = Math.max(lat1, pt[1]); hits++;
      }
      view.dlon[0] = a; view.dlon[1] = b; view.lat0 = lat0; view.lat1 = lat1; view.hits = hits;
      const dist = cam.position.length(), Hcss = stage.clientHeight;
      view.texel3 = (2 * Math.PI / 4096) * Hcss / (2 * Math.tan(cam.fov / 2 * Math.PI / 180) * Math.max(dist - 1, 1e-3));   // CSS px per base texel at the disc centre
    }
    // the atlas window at a level: G x G tiles centred on the view centre. The screen's edges see the sphere
    // foreshortened almost to the limb when the zoom is modest, so the window does not try to cover them; the
    // shader fades the tiles out at the window's edge and the base clip carries on beneath. Wanted tiles are
    // the window's tiles that the view actually touches.
    function rectFor(L) {
      const j = idx[L]; if (!j || view.hits < 12) return null;
      const N = j.N, tdeg = 360 / N;
      const fx = (view.lon + 180) / tdeg, fy = (90 - view.lat) / tdeg;
      const tx0 = Math.round(fx - G / 2), ty0 = Math.max(0, Math.min(N / 2 - G, Math.round(fy - G / 2)));
      const vx0 = Math.floor((view.lon + view.dlon[0] + 180) / tdeg), vx1 = Math.floor((view.lon + view.dlon[1] + 180) / tdeg);
      const vy0 = Math.max(0, Math.floor((90 - view.lat1) / tdeg)), vy1 = Math.min(N / 2 - 1, Math.floor((90 - view.lat0) / tdeg));
      return { L, N, tx0, ty0, w: G, h: G, vx0, vx1, vy0, vy1 };
    }
    api.finestWidth = function () {                          // for the zoom cap: the finest level with a tile under the view centre
      for (const L of levels) { const j = idx[L]; if (!j) continue; const tdeg = 360 / j.N;
        if (has(L, Math.floor((view.lon + 180) / tdeg), Math.floor((90 - view.lat) / tdeg))) return TILE << L; }
      return 4096;
    };
    api.view = () => view;
    api._tiles = tiles;                                     // for measurement scripts only

    function setEq(a, b) { if (a.size !== b.size) return false; for (const x of a) if (!b.has(x)) return false; return true; }
    // ---- the loop
    let active = null;                                       // {L, N, tx0, ty0, w, h, ids:Set}
    let dirty = false, dirtyFeed = false, lastI = -1, on = 0, wantOn = false, changedAt = 0;
    api.update = function (i, dt) {
      const tU = performance.now();
      measureView();
      // which level: the finest whose texel is not much smaller than a CSS pixel and whose tiles fit
      let rect = null;
      if (view.texel3 > 1.25) for (const L of levels) {
        if (view.texel3 / (1 << (L - 3)) < 0.6) continue;                 // a level may be minified up to ~1.7x; finer than that is wasted decoding
        rect = rectFor(L); if (rect) break;
      }
      const now = performance.now();
      if (rect) {
        const ids = new Set();
        for (let ty = Math.max(rect.ty0, rect.vy0); ty < Math.min(rect.ty0 + G, rect.vy1 + 1); ty++)
          for (let tx = Math.max(rect.tx0, rect.vx0); tx < Math.min(rect.tx0 + G, rect.vx1 + 1); tx++) {
            const txm = ((tx % rect.N) + rect.N) % rect.N;
            if (has(rect.L, txm, ty) && ids.size < MAX_DEC) ids.add(`${rect.L}/${txm}_${ty}`);
          }
        rect.ids = ids;
        const same = active && active.L === rect.L && active.tx0 === rect.tx0 && active.ty0 === rect.ty0 && setEq(active.ids, ids);
        if (!same) {
          const levelChange = !active || active.L !== rect.L;
          if (levelChange) { on = Math.min(on, 0.999); wantOn = false; }              // fade to the base while the new level warms up
          active = rect; changedAt = now; st.sharpMs = null; dirty = true; dirtyFeed = true;   // feed even when the clock is paused
          const tM = performance.now();
          for (const id of ids) if (!tiles.has(id)) { const [L, k] = id.split('/'); const [tx, ty] = k.split('_').map(Number); makeTile(+L, tx, ty); }
          st.makeMax = Math.max(st.makeMax, performance.now() - tM);
        }
        for (const id of ids) tiles.get(id).lastWanted = now;
      } else if (active) { active = null; wantOn = false; }
      // decoders only for wanted tiles; others linger a moment (a pan back), then go
      for (const t of tiles.values()) {
        const wanted = active && active.ids.has(t.id);
        if (!wanted) { if (t.dec) { try { t.dec.close(); } catch (e) { /* closed */ } t.dec = null; t.next = -1; }
          if (now - t.lastWanted > LINGER_MS) dropTile(t); }
        else if (!t.dec) { configure(t); dirtyFeed = true; }
      }
      if (bufTotal > BUF_CAP) for (const t of tiles.values()) { if (active && active.ids.has(t.id)) continue; bufTotal -= t.bytes; t.bytes = 0; t.bufs.clear(); if (bufTotal < BUF_CAP * 0.7) break; }
      // feed for this frame
      if (active && (i !== lastI || dirtyFeed)) { const tF = performance.now(); for (const id of active.ids) feed(tiles.get(id), i); dirtyFeed = false; st.feedMax = Math.max(st.feedMax, performance.now() - tF); }
      if (i !== lastI) { lastI = i; dirty = true; }
      // ready = every wanted tile has a frame at or next to the clock
      if (active) {
        let ready = active.ids.size > 0;
        for (const id of active.ids) { const t = tiles.get(id); if (!t.ring.some(s => s.i === i || s.i === i - 1)) { ready = false; break; } }
        if (ready && !wantOn) { wantOn = true; if (st.sharpMs === null) st.sharpMs = Math.round(now - changedAt); }
        if (!ready && on === 0) wantOn = false;
      }
      on += ((wantOn ? 1 : 0) - on) * Math.min(1, dt * 1000 / FADE_MS);
      if (Math.abs(on - (wantOn ? 1 : 0)) < 0.01) on = wantOn ? 1 : 0;
      if (dirty && active) { const tC = performance.now(); composite(i); st.compMax = Math.max(st.compMax, performance.now() - tC); }
      dirty = false;
      // uniforms
      U.uTileOn.value = active ? on : 0;
      if (active) {
        const N = active.N;
        U.uTileRect.value.set((((active.tx0 % N) + N) % N) / N, 1 - 2 * (active.ty0 + G) / N, N / G, N / (2 * G));
        const lpt = idx[active.L].flow_levels_per_texel, f = 127.5 / lpt / (TILE * G);
        U.uTileFlow.value.set(f, -f);
      }
      st.level = active ? active.L : 0; st.on = +on.toFixed(2); st.tiles = tiles.size;
      let d = 0; for (const t of tiles.values()) if (t.dec) d++; st.decoders = d;
      st.updMax = Math.max(st.updMax, performance.now() - tU);
    };

    // draw every wanted tile that has a frame into both atlases: A gets the frame before the clock, B the frame at it
    function composite(i) {
      renderer.getClearColor(savedCol); const savedAlpha = renderer.getClearAlpha();
      renderer.setClearColor(clearCol, 0);
      for (const pass of [0, 1]) {
        const want = i - 1 + pass;
        for (const t of tiles.values()) {
          const m = t.mesh;
          if (!active.ids.has(t.id)) { m.visible = false; continue; }
          let s = null; for (const r of t.ring) if (r.i === want) s = r;
          if (!s) { let best = -1; for (const r of t.ring) if (r.i >= 0 && Math.abs(r.i - want) <= 1 && (best < 0 || Math.abs(r.i - want) < Math.abs(t.ring[best].i - want))) best = t.ring.indexOf(r); if (best >= 0) s = t.ring[best]; }
          if (!s) { m.visible = false; continue; }
          const N = active.N, cx = (((t.tx - active.tx0) % N) + N) % N, cy = G - 1 - (t.ty - active.ty0);
          m.position.set(cx + 0.5, cy + 0.5, 0); m.visible = true;
          m.material.uniforms.tY.value = s.y; m.material.uniforms.tUV.value = s.uv;
        }
        renderer.setRenderTarget(pass ? rtB : rtA); renderer.render(atlasScene, atlasCam);
      }
      renderer.setRenderTarget(null); renderer.setClearColor(savedCol, savedAlpha);
    }
    return api;
  };
})();
