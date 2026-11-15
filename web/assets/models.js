import * as THREE from "three";

// Procedural farm machinery for the 3D view. Units are metres, +x is forward,
// y is up, the ground is y = 0. Geometry and materials only, no textures from disk.

// ---------- shared helpers ----------

const UP = new THREE.Vector3(0, 1, 0);
const dummy = new THREE.Object3D();
const _a = new THREE.Vector3();
const _b = new THREE.Vector3();
const unitBox = new THREE.BoxGeometry(1, 1, 1);
const unitRod = new THREE.CylinderGeometry(1, 1, 1, 12); // axis along y

function std(color, roughness, metalness, extra = {}) {
  return new THREE.MeshStandardMaterial({ color, roughness, metalness, ...extra });
}

// Optional PBR surface sets, see applySurfaces. Which maps each material kind takes:
// color = colour map (replaces any built-in one), normal = normalScale, rough = roughness map.
const KIND = {
  tyre: { src: "rubber", normal: 0.3, rough: true },
  paint: { src: "paintedMetal", normal: 0.15, rough: true },
  sheet: { src: "sheetMetal", color: true, normal: 1, rough: true },
  concrete: { src: "concrete", color: true, normal: 1, rough: true },
  steel: { src: "galvanised", rough: true },
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
  if (k.color) m.map = use(s.map) || e.base;
  if (k.normal) {
    m.normalMap = use(s.normalMap);
    m.normalScale.setScalar(k.normal);
  }
  if (k.rough) m.roughnessMap = use(s.roughnessMap);
  m.needsUpdate = true;
}

// Registers a material for surface maps; repeat is in UV units per tile (about 2 m).
function surf(mat, kind, repeat = [1, 1]) {
  const e = { mat, kind, repeat, base: mat.map || null, clones: [] };
  surfaced.push(e);
  if (surfaces) refresh(e);
  return mat;
}

// surfaces: {paintedMetal, rubber, sheetMetal, concrete, galvanised} of {map, normalMap, roughnessMap}.
// Safe before or after objects are built, and to call again.
export function applySurfaces(s) {
  surfaces = s || {};
  surfaced.forEach(refresh);
}

// Low metalness everywhere: there is no environment map, so metal would look black.
const MAT = {
  tyre: surf(std(0x1d1d1d, 0.92, 0), "tyre", [3, 1]),
  dark: surf(std(0x2b2d31, 0.55, 0.3), "steel"),
  steel: surf(std(0xaab0b6, 0.38, 0.5), "steel"),
  chrome: std(0xc6cacf, 0.25, 0.6),
  blade: surf(std(0xb9bec3, 0.3, 0.55, { side: THREE.DoubleSide }), "steel"),
  weight: std(0x45484d, 0.5, 0.35),
  glass: std(0x22313b, 0.08, 0.1, { transparent: true, opacity: 0.5 }),
};

// Implement colour schemes (generic, no real brand).
const IMAT = {
  sprayTank: surf(std(0xe9e5da, 0.35, 0.05), "paint", [1.5, 1.5]),
  sprayFrame: surf(std(0x2c3542, 0.5, 0.3), "steel"),
  sprayAccent: surf(std(0xe2722b, 0.45, 0.1), "paint", [1.5, 1.5]),
  mowRed: surf(std(0xb5312a, 0.42, 0.1, { side: THREE.DoubleSide }), "paint", [1.5, 1.5]),
  discBlue: surf(std(0x1f4f8f, 0.42, 0.1), "paint", [1.5, 1.5]),
  plantFrame: surf(std(0x2f4d6b, 0.45, 0.15), "paint", [1.5, 1.5]),
  plantHopper: surf(std(0xe39a1c, 0.4, 0.05), "paint", [1.5, 1.5]),
};

// One painted material per body colour, shared between tractors.
const paintCache = new Map();
function paint(color) {
  const key = String(color);
  if (!paintCache.has(key)) {
    paintCache.set(key, surf(std(new THREE.Color(color), 0.4, 0.1, { side: THREE.DoubleSide }), "paint", [1.5, 1.5]));
  }
  return paintCache.get(key);
}

function mesh(geo, mat, x = 0, y = 0, z = 0, shadow = true) {
  const m = new THREE.Mesh(geo, mat);
  m.position.set(x, y, z);
  m.castShadow = shadow;
  return m;
}

function matrixAt(im, i, x, y, z, rx = 0, ry = 0, rz = 0, sx = 1, sy = 1, sz = 1) {
  dummy.position.set(x, y, z);
  dummy.rotation.set(rx, ry, rz);
  dummy.scale.set(sx, sy, sz);
  dummy.updateMatrix();
  im.setMatrixAt(i, dummy.matrix);
}

// One InstancedMesh of boxes. Item: [x, y, z, sx, sy, sz, ry = 0, rz = 0].
function boxes(mat, items, shadow = true) {
  const im = new THREE.InstancedMesh(unitBox, mat, items.length);
  items.forEach(([x, y, z, sx, sy, sz, ry = 0, rz = 0], i) => {
    matrixAt(im, i, x, y, z, 0, ry, rz, sx, sy, sz);
  });
  im.castShadow = shadow;
  return im;
}

// One InstancedMesh of round bars. Item: [ax, ay, az, bx, by, bz, radius].
function rods(mat, items, shadow = true) {
  const im = new THREE.InstancedMesh(unitRod, mat, items.length);
  items.forEach(([ax, ay, az, bx, by, bz, r], i) => {
    _a.set(ax, ay, az);
    _b.set(bx, by, bz);
    const len = _a.distanceTo(_b);
    dummy.position.copy(_a).add(_b).multiplyScalar(0.5);
    dummy.quaternion.setFromUnitVectors(UP, _b.sub(_a).normalize());
    dummy.scale.set(r, len, r);
    dummy.updateMatrix();
    im.setMatrixAt(i, dummy.matrix);
  });
  im.castShadow = shadow;
  return im;
}

// Instances of any geometry. Item: [x, y, z, rx = 0, ry = 0, rz = 0].
function instances(geo, mat, items, shadow = false) {
  const im = new THREE.InstancedMesh(geo, mat, items.length);
  items.forEach(([x, y, z, rx = 0, ry = 0, rz = 0], i) => matrixAt(im, i, x, y, z, rx, ry, rz));
  im.castShadow = shadow;
  return im;
}

// Lathe tyre with rounded shoulders; axle along z.
function tyreGeometry(r, w, rimR) {
  const h = w / 2;
  const s = Math.min(w * 0.22, (r - rimR) * 0.5);
  const pts = [new THREE.Vector2(rimR, -h * 0.85)];
  for (let i = 0; i <= 6; i++) {
    const a = -Math.PI / 2 + (i / 6) * (Math.PI / 2);
    pts.push(new THREE.Vector2(r - s + s * Math.cos(a), -(h - s) + s * Math.sin(a)));
  }
  for (let i = 0; i <= 6; i++) {
    const a = (i / 6) * (Math.PI / 2);
    pts.push(new THREE.Vector2(r - s + s * Math.cos(a), h - s + s * Math.sin(a)));
  }
  pts.push(new THREE.Vector2(rimR, h * 0.85));
  const g = new THREE.LatheGeometry(pts, 36);
  g.rotateX(Math.PI / 2);
  return g;
}

function zCylinder(rTop, rBottom, len, seg) {
  const g = new THREE.CylinderGeometry(rTop, rBottom, len, seg);
  g.rotateX(Math.PI / 2);
  return g;
}

// Cached wheel geometries. With lugs the carcass is smaller so lug tips reach r.
const wheelCache = new Map();
function wheelParts(r, w, rimR, lugs) {
  const key = [r, w, rimR, lugs].join("|");
  if (!wheelCache.has(key)) {
    wheelCache.set(key, {
      tyre: tyreGeometry(lugs ? r - 0.06 : r, w, rimR),
      lug: new THREE.BoxGeometry(0.075, Math.max(0.06, r * 0.1), w * 0.42),
      rim: zCylinder(rimR, rimR, w * 0.8, 28),
      hub: zCylinder(rimR * 0.35, rimR * 0.35, w * 0.95, 16),
    });
  }
  return wheelCache.get(key);
}

// A tractor wheel: a named group whose local z axis is the axle.
function buildWheel(name, r, w, rimR, rimMat, lugCount) {
  const g = new THREE.Group();
  g.name = name;
  const p = wheelParts(r, w, rimR, true);
  g.add(mesh(p.tyre, MAT.tyre));

  // Staggered chevron tread lugs
  const lugs = new THREE.InstancedMesh(p.lug, MAT.tyre, lugCount * 2);
  const rl = r - 0.035;
  for (let i = 0; i < lugCount; i++) {
    for (let side = 0; side < 2; side++) {
      const a = ((i + side * 0.5) / lugCount) * Math.PI * 2;
      const sgn = side ? 1 : -1;
      dummy.position.set(Math.cos(a) * rl, Math.sin(a) * rl, sgn * w * 0.24);
      dummy.rotation.set(0, 0, a);
      dummy.rotateX(sgn * 0.5);
      dummy.scale.set(1, 1, 1);
      dummy.updateMatrix();
      lugs.setMatrixAt(i * 2 + side, dummy.matrix);
    }
  }
  g.add(lugs);
  g.add(mesh(p.rim, rimMat, 0, 0, 0, false));
  g.add(mesh(p.hub, MAT.steel, 0, 0, 0, false));
  return g;
}

// Plain implement wheels: tyres and rims as two instanced meshes.
function wheelPair(g, x, r, w, zs, rimMat) {
  const p = wheelParts(r, w, r * 0.6, false);
  const items = zs.map((z) => [x, r, z]);
  g.add(instances(p.tyre, MAT.tyre, items, true));
  g.add(instances(p.rim, rimMat, items));
}

// Open arc over a wheel; axis along z, arc centred on the top.
function fenderGeometry(radius, width, arc) {
  const g = new THREE.CylinderGeometry(radius, radius, width, 24, 1, true, Math.PI - arc / 2, arc);
  g.rotateX(Math.PI / 2);
  return g;
}

function roundedRect(w, d, r) {
  const s = new THREE.Shape();
  const x = -w / 2;
  const y = -d / 2;
  s.moveTo(x + r, y);
  s.lineTo(x + w - r, y);
  s.quadraticCurveTo(x + w, y, x + w, y + r);
  s.lineTo(x + w, y + d - r);
  s.quadraticCurveTo(x + w, y + d, x + w - r, y + d);
  s.lineTo(x + r, y + d);
  s.quadraticCurveTo(x, y + d, x, y + d - r);
  s.lineTo(x, y + r);
  s.quadraticCurveTo(x, y, x + r, y);
  return s;
}

// Side profile (x, y) extruded across z, centred on z = 0, total width exactly `width`.
function extrudeAcross(shape, width, bevel) {
  const depth = width - 2 * bevel;
  const g = new THREE.ExtrudeGeometry(shape, {
    depth,
    bevelEnabled: true,
    bevelThickness: bevel,
    bevelSize: bevel,
    bevelSegments: 3,
    curveSegments: 10,
  });
  g.translate(0, 0, -depth / 2);
  return g;
}

// ---------- tractor geometry (shared by all tractors) ----------

let tractorGeo = null;
function getTractorGeometry() {
  if (tractorGeo) return tractorGeo;

  // Sloped hood with rounded nose
  const hood = new THREE.Shape();
  hood.moveTo(-0.25, 1.15);
  hood.lineTo(2.2, 1.15);
  hood.lineTo(2.28, 1.25);
  hood.lineTo(2.28, 1.8);
  hood.quadraticCurveTo(2.28, 1.98, 2.05, 1.99);
  hood.lineTo(-0.25, 2.12);
  hood.closePath();

  const roof = new THREE.ExtrudeGeometry(roundedRect(1.96, 1.64, 0.2), {
    depth: 0.1,
    bevelEnabled: true,
    bevelThickness: 0.04,
    bevelSize: 0.05,
    bevelSegments: 2,
    curveSegments: 6,
  });
  roof.rotateX(-Math.PI / 2); // extrusion now points up: y from -0.04 to 0.14

  tractorGeo = {
    hood: extrudeAcross(hood, 1.0, 0.05),
    roof,
    glass: new THREE.BoxGeometry(1.7, 1.08, 1.3),
    frontFender: fenderGeometry(0.68, 0.46, 1.9),
    rearFender: fenderGeometry(1.05, 0.64, 2.1),
  };
  return tractorGeo;
}

export function buildTractor(color) {
  const body = paint(color);
  const geo = getTractorGeometry();
  const g = new THREE.Group();
  g.name = "tractor";

  const rearX = -1.35;
  const frontX = 1.5;

  // Wheels: rear radius 0.95, front 0.6. Left is -z when facing +x.
  for (const [name, z] of [["wheelRL", -0.98], ["wheelRR", 0.98]]) {
    const w = buildWheel(name, 0.95, 0.56, 0.52, body, 26);
    w.position.set(rearX, 0.95, z);
    g.add(w);
  }
  for (const [name, z] of [["wheelFL", -0.86], ["wheelFR", 0.86]]) {
    const w = buildWheel(name, 0.6, 0.42, 0.34, body, 18);
    w.position.set(frontX, 0.6, z);
    g.add(w);
  }

  // Painted bodywork
  g.add(mesh(geo.hood, body));
  g.add(mesh(geo.roof, body, -0.85, 3.06, 0)); // roof top at y = 3.20
  for (const z of [-0.86, 0.86]) g.add(mesh(geo.frontFender, body, frontX, 0.6, z));
  for (const z of [-0.98, 0.98]) g.add(mesh(geo.rearFender, body, rearX, 0.95, z));

  // Tinted cab glass
  g.add(mesh(geo.glass, MAT.glass, -0.85, 2.52, 0, false));

  // Frame, cab base, pillars, seat, steps, vents, grille backing, lights
  g.add(boxes(MAT.dark, [
    [0.95, 0.85, 0, 2.9, 0.6, 0.78], // engine block and frame rails
    [-1.15, 1.0, 0, 1.3, 0.8, 0.82], // transmission housing
    [rearX, 0.95, 0, 0.4, 0.4, 1.45], // rear axle housing
    [-0.85, 1.72, 0, 1.8, 0.5, 1.32], // cab base
    [frontX, 0.6, 0, 0.22, 0.2, 1.4], // front axle beam
    [2.47, 0.85, 0, 0.2, 0.45, 0.9], // front weight bracket
    [-2.1, 0.5, 0, 0.6, 0.08, 0.2], // drawbar
    [0.02, 2.52, -0.66, 0.08, 1.1, 0.08], // cab pillars
    [0.02, 2.52, 0.66, 0.08, 1.1, 0.08],
    [-1.72, 2.52, -0.66, 0.08, 1.1, 0.08],
    [-1.72, 2.52, 0.66, 0.08, 1.1, 0.08],
    [-0.62, 2.52, -0.66, 0.06, 1.1, 0.06],
    [-0.62, 2.52, 0.66, 0.06, 1.1, 0.06],
    [-1.15, 2.2, 0, 0.45, 0.45, 0.5], // seat
    [0.15, 0.55, -0.9, 0.42, 0.05, 0.3], // steps
    [0.15, 0.95, -0.9, 0.42, 0.05, 0.3],
    [0.15, 1.35, -0.9, 0.42, 0.05, 0.3],
    [-0.08, 0.95, -0.9, 0.04, 0.9, 0.3],
    [0.38, 0.95, -0.9, 0.04, 0.9, 0.3],
    [1.9, 1.55, -0.51, 0.7, 0.4, 0.02], // hood side vents
    [1.9, 1.55, 0.51, 0.7, 0.4, 0.02],
    [2.355, 1.53, 0, 0.05, 0.55, 0.8], // grille backing
    [0.12, 2.8, -0.98, 0.04, 0.32, 0.2], // mirrors
    [0.12, 2.8, 0.98, 0.04, 0.32, 0.2],
    [0.2, 3.1, -0.5, 0.08, 0.08, 0.22], // roof work lights
    [0.2, 3.1, 0.5, 0.08, 0.08, 0.22],
  ]));

  // Grille bars
  const bars = [];
  for (let i = 0; i < 5; i++) bars.push([2.39, 1.32 + i * 0.105, 0, 0.02, 0.035, 0.76]);
  g.add(boxes(MAT.steel, bars, false));

  // Front weight stack
  const slabs = [];
  for (let i = 0; i < 8; i++) slabs.push([2.73, 0.9, -0.42 + i * 0.12, 0.34, 0.55, 0.11]);
  g.add(boxes(MAT.weight, slabs));

  // Exhaust stack and muffler
  g.add(rods(MAT.chrome, [
    [0.35, 2.1, 0.38, 0.35, 3.2, 0.38, 0.06],
    [0.35, 2.15, 0.38, 0.35, 2.65, 0.38, 0.1],
  ]));

  // Three-point hitch: lower links end at x = -2.4
  g.add(rods(MAT.dark, [
    [-1.6, 0.68, -0.34, -2.4, 0.5, -0.44, 0.045],
    [-1.6, 0.68, 0.34, -2.4, 0.5, 0.44, 0.045],
    [-1.75, 1.3, 0, -2.35, 1.05, 0, 0.04], // top link
    [-1.65, 1.42, -0.3, -2.0, 1.35, -0.36, 0.04], // lift arms
    [-1.65, 1.42, 0.3, -2.0, 1.35, 0.36, 0.04],
    [-2.0, 1.35, -0.36, -2.05, 0.6, -0.4, 0.03], // lift rods
    [-2.0, 1.35, 0.36, -2.05, 0.6, 0.4, 0.03],
  ]));

  return g;
}

// ---------- implements ----------

export function buildImplement(type, widthM) {
  const W = Math.max(Number(widthM) || 3, 1);
  const g = new THREE.Group();
  g.name = "implement-" + type;
  if (type === "spray") buildSprayer(g, W);
  else if (type === "mow") buildMower(g, W);
  else if (type === "till") buildDisc(g, W);
  else buildPlanter(g, W);
  return g;
}

let capsuleGeo = null;

function buildSprayer(g, W) {
  // Chassis, saddles, lift frame and mudguards
  g.add(boxes(IMAT.sprayFrame, [
    [-4.95, 0.85, -0.42, 3.4, 0.16, 0.14], // rails
    [-4.95, 0.85, 0.42, 3.4, 0.16, 0.14],
    [-2.85, 0.65, 0, 0.98, 0.12, 0.14, 0, -0.32], // tongue
    [-3.3, 0.85, 0, 0.14, 0.14, 0.98],
    [-5.1, 0.7, 0, 0.16, 0.16, 2.2], // axle
    [-4.0, 0.95, 0, 0.18, 0.25, 1.1], // tank saddles
    [-5.8, 0.95, 0, 0.18, 0.25, 1.1],
    [-6.8, 1.45, -0.45, 0.14, 1.3, 0.14], // lift frame posts
    [-6.8, 1.45, 0.45, 0.14, 1.3, 0.14],
    [-7.0, 1.35, 0, 0.12, 0.8, 1.6], // boom centre frame
    [-5.1, 1.5, -1.08, 1.3, 0.05, 0.42], // mudguards
    [-5.1, 1.5, 1.08, 1.3, 0.05, 0.42],
  ]));

  // Tank
  if (!capsuleGeo) {
    capsuleGeo = new THREE.CapsuleGeometry(0.78, 2.0, 6, 24);
    capsuleGeo.rotateZ(Math.PI / 2);
  }
  g.add(mesh(capsuleGeo, IMAT.sprayTank, -4.9, 1.75, 0));

  // Boom truss across the full width, tapering toward the tips
  const x0 = -7.15;
  const xr = -7.45;
  const yb = 1.0;
  const half = W / 2;
  const topY = (z) => 1.6 - 0.36 * (Math.abs(z) / half);
  const n = Math.max(4, Math.round(W));
  const inner = half - 0.04; // keeps braces inside the exact width set by the two tubes
  const seg = (2 * inner) / n;
  const truss = [
    [x0, yb, -half, x0, yb, half, 0.04], // spray bar
    [xr, yb, -half, xr, yb, half, 0.03], // rear tube
    [x0, 1.6, 0, x0, topY(inner), -inner, 0.035], // top chords
    [x0, 1.6, 0, x0, topY(inner), inner, 0.035],
  ];
  for (let i = 0; i <= n; i++) {
    const z = -inner + i * seg;
    truss.push([x0, yb, z, x0, topY(z), z, 0.018]); // verticals
    truss.push([xr, yb, z, x0, topY(z), z, 0.015]); // rear struts
    truss.push([xr, yb, z, x0, yb, z, 0.015]); // rungs
    if (i < n) {
      const z2 = z + seg;
      truss.push(i % 2 ? [x0, topY(z), z, x0, yb, z2, 0.015] : [x0, yb, z, x0, topY(z2), z2, 0.015]);
    }
  }
  g.add(rods(MAT.steel, truss));

  // Nozzles every 0.5 m, tank lid and stripes
  const accent = [
    [-4.4, 2.5, 0, -4.4, 2.62, 0, 0.25],
    [-4.06, 1.75, 0, -3.94, 1.75, 0, 0.79],
    [-5.86, 1.75, 0, -5.74, 1.75, 0, 0.79],
  ];
  const nozzles = Math.floor(W / 0.5);
  for (let i = 0; i < nozzles; i++) {
    const z = -half + 0.25 + i * 0.5;
    accent.push([x0, 0.98, z, x0, 0.86, z, 0.022]);
  }
  g.add(rods(IMAT.sprayAccent, accent, false));

  wheelPair(g, -5.1, 0.72, 0.34, [-1.08, 1.08], MAT.steel);
}

let mowerGeo = null;

function buildMower(g, W) {
  // Conditioner hood: side profile extruded across the full width
  const hood = new THREE.Shape();
  hood.moveTo(-3.95, 0.2);
  hood.lineTo(-3.95, 0.55);
  hood.quadraticCurveTo(-4.0, 1.15, -4.6, 1.2);
  hood.lineTo(-5.15, 1.05);
  hood.lineTo(-5.3, 0.5);
  hood.lineTo(-5.2, 0.25);
  hood.closePath();
  g.add(mesh(extrudeAcross(hood, W, 0.04), IMAT.mowRed));

  const zw = Math.max(0.8, Math.min(W / 2 - 0.45, 1.8));
  const zl = zw - 0.2;
  g.add(boxes(MAT.dark, [
    [-3.375, 0.9, 0, 1.98, 0.16, 0.18, 0, -0.362], // tongue up to the header
    [-4.3, 0.07, 0, 0.6, 0.1, W - 0.02], // cutterbar
    [-5.2, 1.25, 0, 0.16, 0.16, 2 * zl + 0.12], // rear beam
    [-5.5, 1.25, -zl, 0.7, 0.12, 0.12], // wheel arms
    [-5.5, 1.25, zl, 0.7, 0.12, 0.12],
    [-5.8, 0.83, -zl, 0.12, 0.85, 0.12], // wheel legs
    [-5.8, 0.83, zl, 0.12, 0.85, 0.12],
    [-5.75, 0.45, -0.8, 0.9, 0.45, 0.03, 0.42], // swath boards
    [-5.75, 0.45, 0.8, 0.9, 0.45, 0.03, -0.42],
  ]));

  // Cutting discs along the bar
  if (!mowerGeo) mowerGeo = { disc: new THREE.CylinderGeometry(0.22, 0.22, 0.04, 18) };
  const nd = Math.max(2, Math.round(W / 0.62));
  const discs = [];
  for (let i = 0; i < nd; i++) discs.push([-4.2, 0.15, -W / 2 + (i + 0.5) * (W / nd)]);
  g.add(instances(mowerGeo.disc, MAT.steel, discs));

  // Rubber swath curtain at the front
  g.add(boxes(MAT.tyre, [[-4.02, 0.36, 0, 0.02, 0.36, W - 0.1]], false));

  // PTO driveline and wheel stubs
  g.add(rods(MAT.steel, [
    [-2.35, 0.75, 0.25, -4.2, 1.15, 0.25, 0.045],
    [-5.8, 0.42, -zl, -5.8, 0.42, -zw, 0.04],
    [-5.8, 0.42, zl, -5.8, 0.42, zw, 0.04],
  ], false));

  wheelPair(g, -5.8, 0.42, 0.22, [-zw, zw], IMAT.mowRed);
}

let bladeGeo = null;

function buildDisc(g, W) {
  const half = W / 2;
  const bz = Math.min(1.4, half - 0.1);
  const bl = Math.hypot(0.75, bz);
  const ba = Math.atan2(bz, 0.75);
  const frame = [
    [-3.5, 0.78, 0, 0.14, 0.14, W], // cross tubes, exact working width
    [-4.4, 0.78, 0, 0.14, 0.14, W],
    [-5.3, 0.78, 0, 0.14, 0.14, W],
    [-4.4, 0.78, -(half - 0.07), 1.94, 0.14, 0.14], // side rails
    [-4.4, 0.78, half - 0.07, 1.94, 0.14, 0.14],
    [-4.4, 0.78, 0, 1.94, 0.14, 0.14], // centre rail
    [-2.975, 0.64, 0, 1.09, 0.14, 0.16, 0, -0.26], // tongue
    [-3.125, 0.7, bz / 2, bl, 0.1, 0.1, ba], // tongue braces
    [-3.125, 0.7, -bz / 2, bl, 0.1, 0.1, -ba],
  ];

  // Concave blade: a shallow spherical cap, axis along z
  if (!bladeGeo) {
    bladeGeo = new THREE.SphereGeometry(0.55, 18, 4, 0, Math.PI * 2, 0, 0.55);
    bladeGeo.rotateX(Math.PI / 2);
    bladeGeo.translate(0, 0, -0.5);
  }

  // Two gangs (front and rear), each split in two halves at opposite angles
  const ang = 0.22;
  const t = Math.tan(ang);
  const blades = [];
  const shafts = [];
  for (const [base, k] of [[-3.75, 1], [-5.05, -1]]) {
    for (const sgn of [-1, 1]) {
      const zIn = 0.15;
      const zOut = half - 0.25;
      const nb = Math.max(2, Math.floor((zOut - zIn) / 0.23) + 1);
      const xAt = (az) => base + k * (az - W / 4) * t;
      for (let i = 0; i < nb; i++) {
        const az = zIn + (i * (zOut - zIn)) / (nb - 1);
        const flip = sgn * k > 0 ? Math.PI : 0;
        blades.push([xAt(az), 0.28, sgn * az, 0, sgn * k * ang + flip, 0]);
      }
      shafts.push([xAt(zIn), 0.28, sgn * zIn, xAt(zOut), 0.28, sgn * zOut, 0.03]);
      for (const f of [0.3, 0.76]) {
        const az = half * f;
        frame.push([xAt(az), 0.55, sgn * az, 0.1, 0.45, 0.1]); // gang hangers
      }
    }
  }
  g.add(boxes(IMAT.discBlue, frame));
  g.add(instances(bladeGeo, MAT.blade, blades, true));
  g.add(rods(MAT.dark, shafts, false));

  wheelPair(g, -4.07, 0.32, 0.18, [-0.45, 0.45], IMAT.discBlue);
}

let planterGeo = null;

function buildPlanter(g, W) {
  const half = W / 2;
  const n = Math.max(1, Math.floor(W / 0.76));
  const zs = [];
  for (let i = 0; i < n; i++) zs.push((i - (n - 1) / 2) * 0.76);
  const zw = Math.min(1.25, half - 0.2);
  const bz = Math.min(1.2, half - 0.1);
  const bl = Math.hypot(0.55, bz);
  const ba = Math.atan2(bz, 0.55);

  // Toolbar (exact working width), tongue, braces and wheel arms
  g.add(boxes(IMAT.plantFrame, [
    [-3.3, 0.85, 0, 0.2, 0.2, W],
    [-2.85, 0.675, 0, 0.97, 0.14, 0.16, 0, -0.37],
    [-2.975, 0.75, bz / 2, bl, 0.1, 0.1, ba],
    [-2.975, 0.75, -bz / 2, bl, 0.1, 0.1, -ba],
    [-3.125, 0.6, -(zw - 0.14), 0.1, 0.61, 0.1, 0, 0.61],
    [-3.125, 0.6, zw - 0.14, 0.1, 0.61, 0.1, 0, 0.61],
  ]));

  if (!planterGeo) {
    const hopper = new THREE.CylinderGeometry(0.27, 0.15, 0.5, 4, 1);
    hopper.rotateY(Math.PI / 4);
    planterGeo = {
      hopper,
      opener: zCylinder(0.19, 0.19, 0.02, 20),
      press: zCylinder(0.15, 0.15, 0.06, 16),
    };
  }

  // Row units every 0.76 m
  const dark = [];
  const hoppers = [];
  const openers = [];
  const press = [];
  for (const z of zs) {
    dark.push(
      [-3.62, 0.75, z, 0.45, 0.08, 0.1], // parallel arm
      [-3.95, 0.6, z, 0.3, 0.55, 0.14], // unit frame
      [-3.95, 1.42, z, 0.4, 0.05, 0.4], // hopper lid
      [-4.2, 0.35, z, 0.5, 0.07, 0.08], // press wheel arm
    );
    hoppers.push([-3.95, 1.15, z]);
    openers.push([-3.92, 0.19, z]);
    press.push([-4.42, 0.15, z - 0.07, 0.3], [-4.42, 0.15, z + 0.07, -0.3]);
  }
  g.add(boxes(MAT.dark, dark));
  g.add(instances(planterGeo.hopper, IMAT.plantHopper, hoppers, true));
  g.add(instances(planterGeo.opener, MAT.blade, openers));
  g.add(instances(planterGeo.press, MAT.tyre, press));

  wheelPair(g, -2.95, 0.35, 0.22, [-zw, zw], IMAT.plantFrame);
}

// ---------- yard ----------

// Small canvas of one metal rib, tiled. alongV: ribs vary along the texture's v.
function ribTexture(light, dark, alongV, repeatU, repeatV) {
  const c = document.createElement("canvas");
  c.width = alongV ? 4 : 32;
  c.height = alongV ? 32 : 4;
  const ctx = c.getContext("2d");
  const grad = alongV ? ctx.createLinearGradient(0, 0, 0, 32) : ctx.createLinearGradient(0, 0, 32, 0);
  grad.addColorStop(0, light);
  grad.addColorStop(0.45, light);
  grad.addColorStop(0.6, dark);
  grad.addColorStop(0.85, light);
  grad.addColorStop(1, light);
  ctx.fillStyle = grad;
  ctx.fillRect(0, 0, c.width, c.height);
  const t = new THREE.CanvasTexture(c);
  t.wrapS = THREE.RepeatWrapping;
  t.wrapT = THREE.RepeatWrapping;
  t.repeat.set(repeatU, repeatV);
  t.colorSpace = THREE.SRGBColorSpace;
  t.anisotropy = 4;
  return t;
}

export function buildYard() {
  const g = new THREE.Group();
  g.name = "yard";

  // Shell UVs are in metres (0.5 = one 2 m tile); box UVs span the whole face
  const wallMat = surf(std(0xffffff, 0.6, 0.15, { map: ribTexture("#d3d7db", "#a4aab0", true, 1, 1.5) }), "sheet", [0.5, 0.5]);
  const gableMat = surf(std(0xffffff, 0.6, 0.15, { map: ribTexture("#d3d7db", "#a4aab0", false, 1.5, 1) }), "sheet", [0.5, 0.5]);
  const roofMat = surf(std(0xffffff, 0.55, 0.2, { map: ribTexture("#5a6068", "#3b4046", false, 40, 1) }), "sheet", [15, 4]);
  const doorMat = surf(std(0xffffff, 0.5, 0.2, { map: ribTexture("#9aa1a9", "#6c737b", true, 1, 18) }), "sheet", [3.5, 2.6]);
  const trimMat = std(0x565c63, 0.5, 0.3);
  const concrete = surf(std(0x9c9a94, 0.9, 0), "concrete", [15, 1]);
  const standMat = surf(std(0x9c9a94, 0.9, 0), "concrete", [3, 1]);
  const windowMat = std(0x2a3640, 0.12, 0.1);

  // Farm shop, 30 x 16 m, ridge along x, door facing +z
  const shop = new THREE.Group();
  shop.position.set(-15, 0, -20);
  const prof = new THREE.Shape();
  prof.moveTo(-8, 0);
  prof.lineTo(8, 0);
  prof.lineTo(8, 6);
  prof.lineTo(0, 8.5);
  prof.lineTo(-8, 6);
  prof.closePath();
  const shell = new THREE.ExtrudeGeometry(prof, { depth: 30, bevelEnabled: false });
  shell.translate(0, 0, -15);
  shell.rotateY(Math.PI / 2); // extrusion along x, profile across z
  shop.add(mesh(shell, [gableMat, wallMat])); // groups: 0 = gable caps, 1 = walls

  // Roof slabs with a small overhang
  const slope = Math.atan2(2.5, 8);
  const roofGeo = new THREE.BoxGeometry(31, 0.15, 8.75);
  for (const s of [-1, 1]) {
    const r = mesh(roofGeo, roofMat, 0, 7.27, s * 4.2);
    r.rotation.x = s * slope;
    shop.add(r);
  }

  // Roller door, housing, jambs, walk door, ridge cap, corner trim
  shop.add(mesh(new THREE.BoxGeometry(7, 5.2, 0.2), doorMat, 2, 2.6, 8.08, false));
  shop.add(boxes(trimMat, [
    [2, 5.45, 8.25, 7.6, 0.6, 0.5],
    [-1.65, 2.6, 8.1, 0.25, 5.2, 0.25],
    [5.65, 2.6, 8.1, 0.25, 5.2, 0.25],
    [-9, 1.05, 8.05, 1.0, 2.1, 0.1],
    [0, 8.56, 0, 31, 0.12, 0.6],
    [-15, 3, -8, 0.2, 6, 0.2],
    [15, 3, -8, 0.2, 6, 0.2],
    [-15, 3, 8, 0.2, 6, 0.2],
    [15, 3, 8, 0.2, 6, 0.2],
  ], false));
  shop.add(boxes(windowMat, [
    [-5, 4.2, 8.03, 1.8, 0.8, 0.06],
    [10, 4.2, 8.03, 1.8, 0.8, 0.06],
    [-15.03, 4.2, 0, 0.06, 0.8, 1.8],
  ], false));
  shop.add(boxes(concrete, [[0, 0.2, 0, 30.3, 0.4, 16.3]], false)); // footing skirt
  g.add(shop);

  // Diesel tank on a stand inside a bund, with a pump
  const tankGeo = new THREE.CylinderGeometry(1.05, 1.05, 4.2, 28);
  tankGeo.rotateZ(Math.PI / 2);
  g.add(mesh(tankGeo, std(0x3e6b45, 0.45, 0.15), 26, 2.4, -6));
  g.add(boxes(standMat, [[26, 0.15, -6, 6, 0.3, 3.4]], false));
  g.add(boxes(MAT.dark, [
    [24.6, 0.85, -6.7, 0.12, 1.2, 0.12], // legs
    [24.6, 0.85, -5.3, 0.12, 1.2, 0.12],
    [27.4, 0.85, -6.7, 0.12, 1.2, 0.12],
    [27.4, 0.85, -5.3, 0.12, 1.2, 0.12],
    [24.6, 1.45, -6, 0.25, 0.25, 1.9], // saddles
    [27.4, 1.45, -6, 0.25, 0.25, 1.9],
    [26, 1.27, -3.36, 0.4, 0.3, 0.02], // pump display
  ]));
  g.add(boxes(std(0xb9bec4, 0.45, 0.2), [[26, 0.8, -3.6, 0.7, 1.6, 0.45]])); // dispenser
  g.add(rods(MAT.dark, [
    [26, 1.4, -5.0, 26, 1.4, -3.8, 0.04], // feed pipe
    [26.36, 1.2, -3.6, 26.6, 0.4, -3.3, 0.025], // hose
  ], false));

  // Pallet stacks with bagged seed
  const pallets = [];
  const bags = [];
  for (const [px, pz] of [[5, -9], [6.4, -9], [5, -10.4]]) {
    for (let l = 0; l < 2; l++) {
      pallets.push([px, 0.07 + l * 0.9, pz, 1.2, 0.14, 1.0]);
      bags.push([px, 0.44 + l * 0.9, pz, 1.1, 0.6, 0.9]);
    }
  }
  g.add(boxes(std(0x9c7a52, 0.85, 0), pallets));
  g.add(boxes(std(0xe6e1d3, 0.8, 0), bags));

  // Oil drums by the shop
  const drumGeo = new THREE.CylinderGeometry(0.29, 0.29, 0.88, 18);
  const drums = [];
  for (let i = 0; i < 6; i++) drums.push([2 + (i % 3) * 0.62, 0.44, -13.5 - Math.floor(i / 3) * 0.62]);
  g.add(instances(drumGeo, std(0x2f5f9e, 0.45, 0.2), drums, true));

  // Light pole
  g.add(rods(MAT.steel, [
    [14, 0, 2, 14, 9, 2, 0.12],
    [14, 8.8, 2, 15.2, 8.9, 2, 0.05],
  ]));
  g.add(boxes(std(0x3a3f45, 0.5, 0.2, { emissive: 0xfff1cc, emissiveIntensity: 0.25 }),
    [[15.3, 8.85, 2, 0.6, 0.15, 0.3]], false));

  return g;
}
