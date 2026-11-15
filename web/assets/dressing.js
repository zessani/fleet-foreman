import * as THREE from "three";

// Farm infrastructure: tracks, ditches, pivots, windbreaks, poles, grain bins.
// 1 unit = 10 m. Everything repeated is instanced; ground flats are merged.

const GROUND_Y = 0.015;

function rng(seed) {
  let s = seed >>> 0;
  return () => {
    s = (s + 0x6d2b79f5) >>> 0;
    let t = s;
    t = Math.imul(t ^ (t >>> 15), t | 1);
    t ^= t + Math.imul(t ^ (t >>> 7), t | 61);
    return ((t ^ (t >>> 14)) >>> 0) / 4294967296;
  };
}

// Optional PBR surface sets, see applySurfaces. color = colour map, normal = normalScale,
// rough = roughness map. repeat is UV tiles per piece, chosen for about 2 m of real surface.
const KIND = {
  sheet: { src: "sheetMetal", color: true, normal: 1, rough: true },
  concrete: { src: "concrete", color: true },
  galvanised: { src: "galvanised", rough: true },
  dirt: { src: "dirtTrack", color: true },
};
let surfaces = null;
const surfaced = [];

function refresh(e) {
  const k = KIND[e.kind];
  const s = (surfaces && surfaces[k.src]) || {};
  e.clones.forEach((t) => t.dispose());
  e.clones = [];
  // Own clone per use so each part gets its own repeat
  const use = (t) => {
    if (!t) return null;
    const c = t.clone();
    c.repeat.set(e.repeat[0], e.repeat[1]);
    c.needsUpdate = true;
    e.clones.push(c);
    return c;
  };
  const m = e.mat;
  if (k.color) m.map = use(s.map);
  if (k.normal) {
    m.normalMap = use(s.normalMap);
    m.normalScale.setScalar(k.normal);
  }
  if (k.rough) m.roughnessMap = use(s.roughnessMap);
  m.needsUpdate = true;
}

function surf(mat, kind, repeat) {
  const e = { mat, kind, repeat, clones: [] };
  surfaced.push(e);
  if (surfaces) refresh(e);
  return mat;
}

// surfaces: {sheetMetal, concrete, galvanised, dirtTrack} of {map, normalMap, roughnessMap}.
// Safe before or after buildDressing, and to call again.
export function applySurfaces(s) {
  surfaces = s || {};
  surfaced.forEach(refresh);
}

function flatMat(color) {
  return new THREE.MeshLambertMaterial({
    color, side: THREE.DoubleSide,
    polygonOffset: true, polygonOffsetFactor: -1.5, polygonOffsetUnits: -60,
  });
}

// Merged flat geometry from rectangles and ring slices. UVs are world (x, z) / 4.
function flatMesh(rects, rings, mat) {
  const pos = [];
  const uv = [];
  const tri = (a, b, c) => {
    pos.push(...a, ...b, ...c);
    uv.push(a[0] / 4, a[2] / 4, b[0] / 4, b[2] / 4, c[0] / 4, c[2] / 4);
  };
  for (const [x0, z0, x1, z1] of rects) {
    const y = GROUND_Y;
    tri([x0, y, z0], [x0, y, z1], [x1, y, z0]);
    tri([x1, y, z0], [x0, y, z1], [x1, y, z1]);
  }
  for (const [cx, cz, r0, r1] of rings) {
    const n = 72;
    for (let i = 0; i < n; i++) {
      const a = (i / n) * Math.PI * 2, b = ((i + 1) / n) * Math.PI * 2;
      const p = (r, t) => [cx + Math.cos(t) * r, GROUND_Y, cz + Math.sin(t) * r];
      tri(p(r0, a), p(r0, b), p(r1, a));
      tri(p(r1, a), p(r0, b), p(r1, b));
    }
  }
  const g = new THREE.BufferGeometry();
  g.setAttribute("position", new THREE.Float32BufferAttribute(pos, 3));
  g.setAttribute("uv", new THREE.Float32BufferAttribute(uv, 2));
  g.computeVertexNormals();
  const m = new THREE.Mesh(g, mat);
  m.receiveShadow = true;
  return m;
}

function instanced(geo, mat, matrices, shadow) {
  const m = new THREE.InstancedMesh(geo, mat, matrices.length);
  matrices.forEach((mx, i) => m.setMatrixAt(i, mx));
  m.instanceMatrix.needsUpdate = true;
  m.castShadow = shadow;
  m.receiveShadow = !shadow;
  return m;
}

const _q = new THREE.Quaternion(), _s = new THREE.Vector3(1, 1, 1), _p = new THREE.Vector3();
const _up = new THREE.Vector3(0, 1, 0);
function mat4(x, y, z, yaw = 0, sx = 1, sy = 1, sz = 1) {
  _q.setFromAxisAngle(_up, yaw);
  return new THREE.Matrix4().compose(_p.set(x, y, z), _q, _s.set(sx, sy, sz));
}

export function buildDressing({ fields, roads, yard }) {
  const group = new THREE.Group();
  group.name = "dressing";
  const rand = rng(20261115);
  const TRACK = 0.5, DITCH = 0.3;

  const dirt = [], dirtRings = [], concrete = [], water = [], pads = [];
  const trees = [], treeInfo = [], pivots = [], towers = [], legs = [], wheels = [];
  const BEARING = (35 * Math.PI) / 180;

  for (const f of fields) {
    const h = f.side / 2;
    if (f.round) {
      dirtRings.push([f.x, f.z, h, h + TRACK]);
      pads.push(mat4(f.x, GROUND_Y, f.z));
      pivots.push({ f, len: h });
      continue;
    }
    const o = h + TRACK;
    dirt.push(
      [f.x - o, f.z - o, f.x + o, f.z - h],
      [f.x - o, f.z + h, f.x + o, f.z + o],
      [f.x - o, f.z - h, f.x - h, f.z + h],
      [f.x + h, f.z - h, f.x + o, f.z + h],
    );
    concrete.push([f.x - o, f.z - o - DITCH, f.x + o, f.z - o]);
    const zc = f.z - o - DITCH / 2;
    water.push([f.x - o, zc - 0.05, f.x + o, zc + 0.05]);
    // West windbreak, just outside the track
    for (let z = f.z - h; z <= f.z + h; z += 1.6) {
      const s = 0.8 + rand() * 0.5;
      const tx = f.x - o - 0.35 + (rand() - 0.5) * 0.3;
      const tz = z + (rand() - 0.5) * 0.5;
      trees.push(mat4(tx, 0, tz, rand() * 6.28, s, s, s));
      treeInfo.push({ x: tx, z: tz, scale: s });
    }
  }

  // Centre-pivot machines
  const dirX = Math.cos(BEARING), dirZ = Math.sin(BEARING);
  const yaw = -BEARING;
  const tilt = 0.35;
  for (const { f, len } of pivots) {
    pivots[pivots.indexOf(pivots.find((p) => p.f === f))].done = true;
    towers.push(mat4(f.x + dirX * len * 0.5, 0.4, f.z + dirZ * len * 0.5, yaw, len, 1, 1));
    const ds = [];
    for (let d = 5; d < len - 1; d += 5) ds.push(d);
    ds.push(len - 0.2);
    for (const d of ds) {
      const base = mat4(f.x + dirX * d, 0, f.z + dirZ * d, yaw);
      for (const sgn of [-1, 1]) {
        const legR = new THREE.Matrix4().makeRotationX(sgn * tilt);
        legs.push(base.clone().multiply(legR));
        wheels.push(base.clone().multiply(new THREE.Matrix4().makeTranslation(0, 0, sgn * 0.17)));
      }
    }
  }

  // Utility poles along roads
  const poles = [], arms = [];
  const nearField = (x, z) => fields.some((f) => {
    const r = f.side / 2 + 1;
    return f.round ? Math.hypot(x - f.x, z - f.z) <= r
      : Math.abs(x - f.x) <= r && Math.abs(z - f.z) <= r;
  });
  for (const [a, b] of roads) {
    const dx = b.x - a.x, dz = b.z - a.z, L = Math.hypot(dx, dz);
    if (L < 1e-6) continue;
    const px = -dz / L, pz = dx / L, th = -Math.atan2(pz, px);
    for (let d = 7; d < L; d += 14) {
      const x = a.x + (dx / L) * d + px * 1.6, z = a.z + (dz / L) * d + pz * 1.6;
      if (Math.hypot(x - yard.x, z - yard.z) < 9 || nearField(x, z)) continue;
      poles.push(mat4(x, 0, z, th));
      arms.push(mat4(x, 0.85, z, th));
    }
  }

  // Grain bins
  const bx = yard.x + 7.5, bz = yard.z - 5.5;
  const binAt = (dx, y) => mat4(bx + dx, y, bz);

  // Flat UVs span 40 m per unit, so 20 repeats give a 2 m concrete tile
  const concreteMat = surf(flatMat(0xbdbab0), "concrete", [20, 20]);
  const add = (m) => { if (m) group.add(m); return m; };
  add(flatMesh(dirt, dirtRings, surf(flatMat(0xb9a584), "dirt", [10, 10])));
  add(flatMesh(concrete, [], concreteMat));
  add(flatMesh(water, [], flatMat(0x2f5f60)));

  const lam = (c) => new THREE.MeshLambertMaterial({ color: c });
  const gal = () => surf(new THREE.MeshStandardMaterial({ color: 0xcfd3d6, roughness: 0.5, metalness: 0.6 }), "galvanised", [2, 1]);
  const bin = (c, repeat) => surf(new THREE.MeshStandardMaterial({ color: c, roughness: 0.7, metalness: 0.1 }), "sheet", repeat);
  const geo = (g, y = 0) => g.translate(0, y, 0);
  const alongX = (g) => g.rotateZ(Math.PI / 2);

  if (pads.length) {
    const g = geo(new THREE.CylinderGeometry(0.5, 0.5, 0.02, 20));
    const m = instanced(g, flatMat(0xa8a39a), pads, false);
    group.add(m);
  }
  if (towers.length) {
    group.add(instanced(alongX(new THREE.CylinderGeometry(0.04, 0.04, 1, 8)), gal(), towers, true));
    group.add(instanced(geo(new THREE.BoxGeometry(0.04, 0.42, 0.04), 0.21), gal(), legs, true));
    group.add(instanced(geo(alongX(new THREE.CylinderGeometry(0.07, 0.07, 0.05, 10)), 0.07), lam(0x2a2a2a), wheels, true));
  }
  if (trees.length) {
    const trunks = instanced(geo(new THREE.CylinderGeometry(0.03, 0.04, 0.4, 6), 0.2), lam(0x5a4632), trees, true);
    const crowns = instanced(geo(new THREE.IcosahedronGeometry(0.38, 1), 0.68), lam(0x4a5a2a), trees, true);
    trunks.name = "treeTrunks";
    crowns.name = "treeCrowns";
    group.add(trunks, crowns);
  }
  group.userData.trees = treeInfo;
  if (poles.length) {
    group.add(instanced(geo(new THREE.CylinderGeometry(0.022, 0.03, 0.9, 6), 0.45), lam(0x6b5640), poles, true));
    group.add(instanced(new THREE.BoxGeometry(0.5, 0.03, 0.04), lam(0x6b5640), arms, true));
  }
  group.add(instanced(geo(new THREE.CylinderGeometry(0.45, 0.45, 1.1, 28), 0.55), bin(0xc4c8cb, [14, 5]), [binAt(-0.55, 0), binAt(0.55, 0)], true));
  group.add(instanced(geo(new THREE.ConeGeometry(0.5, 0.35, 28), 0.175), bin(0xa9aeb2, [14, 2]), [binAt(-0.55, 1.1), binAt(0.55, 1.1)], true));

  return group;
}
