import * as THREE from "three";

// ---------- seeded random and noise ----------

// Small seeded generator (mulberry32). Returns a function giving [0, 1).
function makeRng(seed) {
  let s = seed >>> 0;
  return function () {
    s = (s + 0x6d2b79f5) >>> 0;
    let t = s;
    t = Math.imul(t ^ (t >>> 15), t | 1);
    t ^= t + Math.imul(t ^ (t >>> 7), t | 61);
    return ((t ^ (t >>> 14)) >>> 0) / 4294967296;
  };
}

// Integer lattice hash to [0, 1).
function hash2(ix, iy) {
  let h = Math.imul(ix, 374761393) ^ Math.imul(iy, 668265263);
  h = Math.imul(h ^ (h >>> 13), 1274126177);
  return ((h ^ (h >>> 16)) >>> 0) / 4294967296;
}

// Smooth value noise in [0, 1).
function valueNoise(x, y) {
  const ix = Math.floor(x), iy = Math.floor(y);
  const fx = x - ix, fy = y - iy;
  const u = fx * fx * (3 - 2 * fx), v = fy * fy * (3 - 2 * fy);
  const a = hash2(ix, iy), b = hash2(ix + 1, iy);
  const c = hash2(ix, iy + 1), d = hash2(ix + 1, iy + 1);
  return a + (b - a) * u + (c - a) * v + (a - b - c + d) * u * v;
}

// Layered noise, roughly in [0.1, 0.9].
function fbm(x, y, octaves) {
  let sum = 0, amp = 0.5, norm = 0;
  for (let i = 0; i < octaves; i++) {
    sum += amp * valueNoise(x, y);
    norm += amp;
    x = x * 2.03 + 17.3;
    y = y * 2.03 + 5.1;
    amp *= 0.5;
  }
  return sum / norm;
}

function smoothstep(a, b, x) {
  const t = Math.min(1, Math.max(0, (x - a) / (b - a)));
  return t * t * (3 - 2 * t);
}

const tmpColor = new THREE.Color();

// ---------- ground ----------

export function buildGround() {
  const SIZE = 16000, SEG = 128;
  const geo = new THREE.PlaneGeometry(SIZE, SIZE, SEG, SEG);
  geo.rotateX(-Math.PI / 2);

  // Large soft colour variation, stored per vertex (not repeated anywhere).
  const pos = geo.attributes.position;
  const colors = new Float32Array(pos.count * 3);
  for (let i = 0; i < pos.count; i++) {
    const x = pos.getX(i), z = pos.getZ(i);
    const big = fbm(x / 3200 + 3.1, z / 3200 + 7.7, 3) - 0.5;
    const mid = fbm(x / 800 + 40, z / 800 + 90, 3) - 0.5;
    const red = fbm(x / 2200 + 120, z / 2200 + 30, 2) - 0.5;
    // Dry washes: thin bands where a warped noise crosses its middle value.
    const warp = fbm(x / 900 + 200, z / 900 + 300, 2);
    const wash = fbm(x / 1700 + warp * 1.5 + 60, z / 1700 + warp * 1.5 + 11, 3);
    const band = 1 - smoothstep(0.0, 0.035, Math.abs(wash - 0.5));
    let r = 0.80, g = 0.68, b = 0.50;
    const lum = 1 + big * 0.55 + mid * 0.3;
    r = r * lum + red * 0.18;
    g = g * lum + red * 0.03;
    b = b * lum - red * 0.12;
    r += band * 0.07; g += band * 0.07; b += band * 0.08; // pale wash sand
    tmpColor.setRGB(r, g, b, THREE.SRGBColorSpace);
    colors[i * 3] = tmpColor.r;
    colors[i * 3 + 1] = tmpColor.g;
    colors[i * 3 + 2] = tmpColor.b;
  }
  geo.setAttribute("color", new THREE.BufferAttribute(colors, 3));

  // Fine grain, repeated many times, near white so it only modulates colour.
  const N = 512;
  const canvas = document.createElement("canvas");
  canvas.width = canvas.height = N;
  const ctx = canvas.getContext("2d");
  const img = ctx.createImageData(N, N);
  const rng = makeRng(101);
  for (let y = 0; y < N; y++) {
    for (let x = 0; x < N; x++) {
      const n = fbm(x / 24, y / 24, 3) * 0.5 + rng() * 0.5;
      const v = 205 + n * 50;
      const o = (y * N + x) * 4;
      img.data[o] = v; img.data[o + 1] = v * 0.98; img.data[o + 2] = v * 0.95; img.data[o + 3] = 255;
    }
  }
  ctx.putImageData(img, 0, 0);
  // A few pebbles.
  for (let i = 0; i < 260; i++) {
    const shade = rng() < 0.5 ? "rgba(120,100,80,0.35)" : "rgba(255,245,225,0.35)";
    ctx.fillStyle = shade;
    ctx.beginPath();
    ctx.arc(rng() * N, rng() * N, 0.8 + rng() * 1.6, 0, Math.PI * 2);
    ctx.fill();
  }
  const tex = new THREE.CanvasTexture(canvas);
  tex.colorSpace = THREE.SRGBColorSpace;
  tex.wrapS = tex.wrapT = THREE.RepeatWrapping;
  tex.repeat.set(500, 500);
  tex.anisotropy = 8;

  const mat = new THREE.MeshLambertMaterial({ map: tex, vertexColors: true });
  const mesh = new THREE.Mesh(geo, mat);
  mesh.receiveShadow = true;
  return mesh;
}

// ---------- mountains ----------

export function buildMountains() {
  const group = new THREE.Group();
  const COLS = 288, ROWS = 8;
  // radius of the crest, tallest peak, haze tint amount, noise seed offset
  const layers = [
    { rb: 5100, maxH: 520, haze: 0.55, seed: 11 },
    { rb: 4200, maxH: 400, haze: 0.30, seed: 47 },
    { rb: 3400, maxH: 260, haze: 0.08, seed: 83 },
  ];
  const haze = new THREE.Color().setRGB(0.62, 0.68, 0.78, THREE.SRGBColorSpace);

  for (const L of layers) {
    const positions = new Float32Array(COLS * (ROWS + 1) * 3);
    const colors = new Float32Array(COLS * (ROWS + 1) * 3);
    for (let i = 0; i < COLS; i++) {
      const a = (i / COLS) * Math.PI * 2;
      const ca = Math.cos(a), sa = Math.sin(a);
      // Ridge height from noise sampled on a circle, so it wraps cleanly.
      const n = fbm(ca * 3.5 + L.seed, sa * 3.5 + L.seed * 0.7, 5);
      const h = L.maxH * (0.14 + 0.86 * smoothstep(0.3, 0.72, n));
      const width = 350 + h * 1.4;
      for (let j = 0; j <= ROWS; j++) {
        const t = j / ROWS;
        // Gullies running down the slope.
        const g = fbm(ca * 10 + t * 3.1 + L.seed, sa * 10 + t * 2.3, 3);
        const profile = t * t * (3 - 2 * t);
        const y = -8 + (h + 8) * profile * (0.82 + 0.36 * g);
        const r = L.rb - width + t * width;
        const k = (j * COLS + i) * 3;
        positions[k] = ca * r;
        positions[k + 1] = y;
        positions[k + 2] = sa * r;
        // Tan rock at the base, darker toward the ridge, darker in gullies.
        const dark = smoothstep(0.2, 1, t) * 0.4 + (0.5 - g) * 0.25;
        tmpColor.setRGB(0.74 - dark * 0.55, 0.60 - dark * 0.5, 0.43 - dark * 0.35, THREE.SRGBColorSpace);
        tmpColor.lerp(haze, L.haze);
        colors[k] = tmpColor.r;
        colors[k + 1] = tmpColor.g;
        colors[k + 2] = tmpColor.b;
      }
    }
    const index = [];
    for (let j = 0; j < ROWS; j++) {
      for (let i = 0; i < COLS; i++) {
        const i2 = (i + 1) % COLS;
        const a = j * COLS + i, b = j * COLS + i2;
        const c = (j + 1) * COLS + i, d = (j + 1) * COLS + i2;
        index.push(a, c, b, b, c, d);
      }
    }
    const geo = new THREE.BufferGeometry();
    geo.setAttribute("position", new THREE.BufferAttribute(positions, 3));
    geo.setAttribute("color", new THREE.BufferAttribute(colors, 3));
    geo.setIndex(index);
    geo.computeVertexNormals();
    const mat = new THREE.MeshLambertMaterial({ vertexColors: true, side: THREE.DoubleSide });
    group.add(new THREE.Mesh(geo, mat));
  }
  return group;
}

// ---------- scrub ----------

// Join two indexed geometries (position and normal only).
function mergeTwo(a, b) {
  const geo = new THREE.BufferGeometry();
  const pa = a.attributes.position.array, pb = b.attributes.position.array;
  const na = a.attributes.normal.array, nb = b.attributes.normal.array;
  const pos = new Float32Array(pa.length + pb.length);
  const nor = new Float32Array(na.length + nb.length);
  pos.set(pa); pos.set(pb, pa.length);
  nor.set(na); nor.set(nb, na.length);
  const offset = a.attributes.position.count;
  const index = Array.from(a.index.array).concat(Array.from(b.index.array, (v) => v + offset));
  geo.setAttribute("position", new THREE.BufferAttribute(pos, 3));
  geo.setAttribute("normal", new THREE.BufferAttribute(nor, 3));
  geo.setIndex(index);
  return geo;
}

export function buildScrub(keepOut) {
  const blocked = keepOut || [];
  const rng = makeRng(2026);
  const group = new THREE.Group();
  const dummy = new THREE.Object3D();
  const col = new THREE.Color();

  // Clump centres shared by both plant types.
  const clumps = [];
  for (let i = 0; i < 70; i++) {
    clumps.push({ x: (rng() * 2 - 1) * 1500, z: (rng() * 2 - 1) * 1500, s: 30 + rng() * 120 });
  }

  // Pick a spot, usually near a clump, that is outside every keep-out circle.
  function place(clumpShare) {
    for (let attempt = 0; attempt < 30; attempt++) {
      let x, z;
      if (rng() < clumpShare) {
        const c = clumps[Math.floor(rng() * clumps.length)];
        x = c.x + (rng() + rng() + rng() - 1.5) * c.s;
        z = c.z + (rng() + rng() + rng() - 1.5) * c.s;
      } else {
        x = (rng() * 2 - 1) * 1500;
        z = (rng() * 2 - 1) * 1500;
      }
      if (Math.abs(x) > 1500 || Math.abs(z) > 1500) continue;
      let ok = true;
      for (const k of blocked) {
        const dx = x - k.x, dz = z - k.z;
        if (dx * dx + dz * dz < k.r * k.r) { ok = false; break; }
      }
      if (ok) return { x, z };
    }
    return null;
  }

  // Creosote bushes: squat low-poly blobs.
  const BUSHES = 2500;
  const bushGeo = new THREE.IcosahedronGeometry(0.5, 0);
  const bushMat = new THREE.MeshLambertMaterial({ color: 0xffffff });
  const bushes = new THREE.InstancedMesh(bushGeo, bushMat, BUSHES);
  let nb = 0;
  for (let i = 0; i < BUSHES; i++) {
    const p = place(0.8);
    if (!p) continue;
    const w = 0.15 + rng() * 0.15;
    dummy.position.set(p.x, w * 0.3, p.z);
    dummy.rotation.set(0, rng() * Math.PI * 2, 0);
    dummy.scale.set(w, w * (0.55 + rng() * 0.25), w * (0.8 + rng() * 0.3));
    dummy.updateMatrix();
    bushes.setMatrixAt(nb, dummy.matrix);
    const v = rng();
    col.setRGB(0.20 + v * 0.1, 0.26 + v * 0.1, 0.12 + v * 0.05, THREE.SRGBColorSpace);
    bushes.setColorAt(nb, col);
    nb++;
  }
  bushes.count = nb;
  bushes.instanceMatrix.needsUpdate = true;
  bushes.instanceColor.needsUpdate = true;
  bushes.castShadow = true;
  group.add(bushes);

  // Saguaros: a column plus a bent arm on some of them.
  const CACTI = 150;
  const trunkGeo = new THREE.CylinderGeometry(0.045, 0.05, 1, 8, 1);
  trunkGeo.translate(0, 0.5, 0);
  const armH = new THREE.CylinderGeometry(0.03, 0.03, 0.12, 6, 1);
  armH.rotateZ(Math.PI / 2);
  armH.translate(0.06, 0, 0);
  const armV = new THREE.CylinderGeometry(0.03, 0.03, 0.3, 6, 1);
  armV.translate(0.12, 0.15, 0);
  const armGeo = mergeTwo(armH, armV);
  const cactusMat = new THREE.MeshLambertMaterial({ color: 0x3a6a3a });
  const trunks = new THREE.InstancedMesh(trunkGeo, cactusMat, CACTI);
  const arms = new THREE.InstancedMesh(armGeo, cactusMat, CACTI);
  let nt = 0, na = 0;
  for (let i = 0; i < CACTI; i++) {
    const p = place(0.5);
    if (!p) continue;
    const h = 0.4 + rng() * 0.5;
    const yaw = rng() * Math.PI * 2;
    dummy.position.set(p.x, 0, p.z);
    dummy.rotation.set(0, yaw, 0);
    dummy.scale.set(h, h, h);
    dummy.updateMatrix();
    trunks.setMatrixAt(nt++, dummy.matrix);
    if (rng() < 0.6) {
      dummy.position.set(p.x, h * 0.45, p.z);
      dummy.updateMatrix();
      arms.setMatrixAt(na++, dummy.matrix);
    }
  }
  trunks.count = nt;
  arms.count = na;
  trunks.instanceMatrix.needsUpdate = true;
  arms.instanceMatrix.needsUpdate = true;
  trunks.castShadow = true;
  arms.castShadow = true;
  group.add(trunks, arms);
  return group;
}

// ---------- crop textures ----------

function hashString(s) {
  let h = 2166136261;
  for (let i = 0; i < s.length; i++) h = Math.imul(h ^ s.charCodeAt(i), 16777619);
  return h >>> 0;
}

export function drawCrop(ctx, size, crop) {
  const rng = makeRng(hashString(String(crop)) + 7);
  const S = size / 512; // everything below is drawn for 512 px, then scaled
  const soils = { lettuce: "#2b2018", citrus: "#5b4630", alfalfa: "#3a2f1f", cotton: "#4d3b28" };
  const soil = soils[crop] || "#33271b";

  ctx.save();
  ctx.scale(S, S);
  const W = 512;

  // Narrow dirt border, then the bare headland strip inside it.
  ctx.fillStyle = "#8c7350";
  ctx.fillRect(0, 0, W, W);
  ctx.fillStyle = "#765f43";
  ctx.fillRect(6, 6, W - 12, W - 12);
  for (let i = 0; i < 900; i++) {
    ctx.fillStyle = rng() < 0.5 ? "rgba(60,45,30,0.25)" : "rgba(170,145,110,0.25)";
    ctx.fillRect(6 + rng() * (W - 12), 6 + rng() * (W - 12), 1 + rng() * 2, 1 + rng() * 2);
  }
  // Irrigation ditch in the headland along the top edge.
  ctx.fillStyle = "rgba(35,26,18,0.55)";
  ctx.fillRect(6, 14, W - 12, 3);

  // Planted area, clipped so nothing leaks into the headland.
  const m = 28, inner = W - 2 * m;
  ctx.save();
  ctx.beginPath();
  ctx.rect(m, m, inner, inner);
  ctx.clip();
  ctx.fillStyle = soil;
  ctx.fillRect(m, m, inner, inner);

  if (crop === "citrus") drawCitrus(ctx, rng, m, inner);
  else if (crop === "alfalfa") drawAlfalfa(ctx, rng, m, inner);
  else if (crop === "cotton") drawCotton(ctx, rng, m, inner);
  else drawLettuce(ctx, rng, m, inner, crop === "lettuce");

  // Darker damp strip where irrigation water enters (top edge).
  const damp = ctx.createLinearGradient(0, m, 0, m + 46);
  damp.addColorStop(0, "rgba(10,12,8,0.5)");
  damp.addColorStop(1, "rgba(10,12,8,0)");
  ctx.fillStyle = damp;
  ctx.fillRect(m, m, inner, 46);
  ctx.restore();

  ctx.restore();
}

// Lettuce (and the generic green row crop): rows of round heads on wet soil.
function drawLettuce(ctx, rng, m, inner, bright) {
  const pitch = 16;
  for (let y = m + 3; y < m + inner; y += pitch) {
    const hue = (bright ? 100 : 108) + rng() * 14;
    const light = (bright ? 42 : 34) + rng() * 8;
    ctx.fillStyle = "hsla(" + hue + ",65%," + (light - 12) + "%,0.7)";
    ctx.fillRect(m, y + 1, inner, 12); // leaf shade under the row
    for (let x = m + 4; x < m + inner; x += 10 + rng() * 2) {
      const r = 5 + rng() * 2;
      ctx.fillStyle = "hsl(" + (hue + rng() * 8) + ",70%," + (light + rng() * 10) + "%)";
      ctx.beginPath();
      ctx.arc(x, y + 7 + (rng() - 0.5) * 2, r, 0, Math.PI * 2);
      ctx.fill();
    }
  }
}

// Citrus: regular grid of round crowns with shadows on brown soil.
function drawCitrus(ctx, rng, m, inner) {
  const pitch = 36;
  // Grass alleys between the tree rows.
  for (let y = m + pitch / 2 + 12; y < m + inner; y += pitch) {
    ctx.fillStyle = "rgba(60,95,40,0.45)";
    ctx.fillRect(m, y, inner, 10);
  }
  for (let y = m + pitch / 2; y < m + inner + pitch; y += pitch) {
    for (let x = m + pitch / 2; x < m + inner + pitch; x += pitch) {
      const r = 15 + rng() * 4;
      const cx = x + (rng() - 0.5) * 2, cy = y + (rng() - 0.5) * 2;
      ctx.fillStyle = "rgba(0,0,0,0.4)";
      ctx.beginPath();
      ctx.ellipse(cx + 4, cy + 5, r, r * 0.9, 0, 0, Math.PI * 2);
      ctx.fill();
      const g = ctx.createRadialGradient(cx - 3, cy - 3, 1, cx, cy, r);
      g.addColorStop(0, "hsl(" + (110 + rng() * 10) + ",45%,30%)");
      g.addColorStop(1, "hsl(" + (115 + rng() * 10) + ",50%,15%)");
      ctx.fillStyle = g;
      ctx.beginPath();
      ctx.arc(cx, cy, r, 0, Math.PI * 2);
      ctx.fill();
    }
  }
}

// Alfalfa: dense lush green with faint mowing bands along the rows.
function drawAlfalfa(ctx, rng, m, inner) {
  ctx.fillStyle = "#3c8a28";
  ctx.fillRect(m, m, inner, inner);
  const band = 64;
  for (let y = m, k = 0; y < m + inner; y += band, k++) {
    ctx.fillStyle = k % 2 ? "rgba(190,230,110,0.12)" : "rgba(10,60,10,0.12)";
    ctx.fillRect(m, y, inner, band);
  }
  for (let i = 0; i < 9000; i++) {
    const x = m + rng() * inner, y = m + rng() * inner;
    ctx.strokeStyle = "hsla(" + (88 + rng() * 40) + ",60%," + (22 + rng() * 22) + "%,0.7)";
    ctx.lineWidth = 1 + rng();
    ctx.beginPath();
    ctx.moveTo(x, y);
    ctx.lineTo(x + 3 + rng() * 4, y + (rng() - 0.5) * 3);
    ctx.stroke();
  }
  for (let y = m + 6; y < m + inner; y += 12) {
    ctx.fillStyle = "rgba(10,50,10,0.15)";
    ctx.fillRect(m, y, inner, 1);
  }
}

// Cotton: dark green rows with small white boll flecks on brown soil.
function drawCotton(ctx, rng, m, inner) {
  const pitch = 18;
  for (let y = m + 2; y < m + inner; y += pitch) {
    const hue = 105 + rng() * 15;
    const light = 20 + rng() * 6;
    for (let x = m; x < m + inner; x += 5) {
      ctx.fillStyle = "hsl(" + (hue + rng() * 10) + ",50%," + (light + rng() * 8) + "%)";
      ctx.beginPath();
      ctx.arc(x, y + 6 + (rng() - 0.5) * 3, 4.5 + rng() * 1.5, 0, Math.PI * 2);
      ctx.fill();
      if (rng() < 0.3) {
        ctx.fillStyle = "#f3f0e4";
        ctx.beginPath();
        ctx.arc(x + (rng() - 0.5) * 6, y + 6 + (rng() - 0.5) * 6, 1.2 + rng() * 0.6, 0, Math.PI * 2);
        ctx.fill();
      }
    }
  }
}
