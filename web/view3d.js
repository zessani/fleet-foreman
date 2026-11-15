/*
 * Foreman 3D farm view.
 *
 * Draws the Foreman run from GET /api/state (CLAUDE.md section 8c). Everything
 * that moves or changes comes from that state; the farm layout comes from the
 * farm file inside it. Scenery (sky, ground, mountains) is the Arizona setting.
 *
 * Scale: 1 scene unit = 10 m. Fields, roads, pass spacing and coverage stripes
 * are at true size. A tractor and its implement are drawn together at true
 * size when the camera is close, and enlarged (up to MAX_ICON_SCALE) as the
 * camera pulls back so they still read from the overview. The current figure
 * is shown in the corner readout.
 *
 * three.js r160 is loaded from web/vendor. No CDN, so the page works offline.
 */
import * as THREE from "three";
import { OrbitControls } from "three/addons/controls/OrbitControls.js";
import { Sky } from "three/addons/objects/Sky.js";
import { GLTFLoader } from "three/addons/loaders/GLTFLoader.js";
import { CSS2DRenderer, CSS2DObject } from "three/addons/renderers/CSS2DRenderer.js";
import { RGBELoader } from "three/addons/loaders/RGBELoader.js";

const POLL_MS = 500;
const U = 100;                 // scene units per km (1 unit = 10 m)
const M = 0.1;                 // scene units per metre
const MAX_ICON_SCALE = 26;     // largest enlargement of a tractor, at the overview
const MAX_IMPLEMENT_SCALE = 9; // implements are enlarged less, so a wide boom does not cover a field
const ICON_DISTANCE = 16;      // camera distance per 1x of enlargement (true size at the follow distance)
const TRACTOR_GLB = "assets/tractor.glb";
const DUST = false;
// Used only until the server sends farm.implements: width in metres read from the id.
const FALLBACK_WIDTH = { spray: 18, mow: 4, till: 6, plant: 6 };
const TYPE_BY_PREFIX = { sprayer: "spray", mower: "mow", disc: "till", planter: "plant" };
const STATUS_COLOR = { idle: "#9fb0a5", traveling: "#7fb2ff", servicing: "#f0ae4d", working: "#67c587",
                       waiting: "#f0ae4d", broken: "#ef6d68" };

// Richer models and terrain live in web/assets. If either file is missing or
// breaks, the plain built-in versions below are used, so the page never fails.
const [richModels, richTerrain, richDressing] = await Promise.all([
  import("./assets/models.js").catch(() => null),
  import("./assets/terrain.js").catch(() => null),
  import("./assets/dressing.js").catch(() => null),
]);
function attempt(make, fallback) {
  try { const made = make(); if (made) return made; } catch (error) { console.warn("3D view: using the plain version.", error); }
  return fallback();
}

// Downloaded CC0 textures and sky (see web/assets/CREDITS.md). Each is optional:
// a file that fails to load leaves that surface looking as it did without it.
const TEX_DIR = "assets/textures/";
const textureLoader = new THREE.TextureLoader();
function loadTexture(file, colour) {
  return new Promise((resolve) => textureLoader.load(TEX_DIR + file, (t) => {
    t.wrapS = t.wrapT = THREE.RepeatWrapping;
    t.anisotropy = 8;
    if (colour) t.colorSpace = THREE.SRGBColorSpace;
    resolve(t);
  }, undefined, () => resolve(null)));
}
function loadImage(file) {
  return new Promise((resolve) => {
    const img = new Image();
    img.onload = () => resolve(img);
    img.onerror = () => resolve(null);
    img.src = TEX_DIR + file;
  });
}
const TEX = {};
const SOIL = {};
let ENV = null;
await Promise.all([
  ...Object.entries({
    dirt_aerial_03: ["diff", "nor"], aerial_mud_1: ["diff"], gravelly_sand: ["diff", "nor"],
    brushed_concrete: ["diff", "nor", "rough"], corrugated_iron_02: ["nor", "rough"],
    metal_plate_02: ["nor", "rough"], rubberized_track: ["nor", "rough"],
  }).flatMap(([id, kinds]) => kinds.map(
    async (kind) => { (TEX[id] = TEX[id] || {})[kind] = await loadTexture(`${id}_${kind}_1k.jpg`, kind === "diff"); })),
  ...[["till", "farm_soil"], ["plant", "raked_dirt"], ["mow", "withered_grass"]].map(
    async ([job, id]) => { SOIL[job] = await loadImage(`${id}_diff_1k.jpg`); }),
  new Promise((resolve) => new RGBELoader().load(TEX_DIR + "citrus_orchard_road_puresky_1k.hdr", (t) => {
    t.mapping = THREE.EquirectangularReflectionMapping;
    ENV = t;
    resolve();
  }, undefined, () => resolve())),
]);

// A copy of an image with its average colour moved to `target`, as a texture.
// The road texture is dark wet mud; Arizona roads are pale dry dirt of the same grain.
function recoloured(img, target, size = 1024) {
  const c = document.createElement("canvas");
  c.width = c.height = size;
  const g = c.getContext("2d", { willReadFrequently: true });
  g.drawImage(img, 0, 0, size, size);
  const data = g.getImageData(0, 0, size, size), px = data.data, sum = [0, 0, 0];
  for (let i = 0; i < px.length; i += 4) { sum[0] += px[i]; sum[1] += px[i + 1]; sum[2] += px[i + 2]; }
  const gain = target.map((v, k) => v / (sum[k] / (px.length / 4)));
  for (let i = 0; i < px.length; i += 4) { px[i] *= gain[0]; px[i + 1] *= gain[1]; px[i + 2] *= gain[2]; }
  g.putImageData(data, 0, 0);
  const t = new THREE.CanvasTexture(c);
  t.colorSpace = THREE.SRGBColorSpace;
  t.wrapS = t.wrapT = THREE.RepeatWrapping;
  t.anisotropy = 8;
  return t;
}
const ROAD_MAP = TEX.aerial_mud_1 && TEX.aerial_mud_1.diff ? recoloured(TEX.aerial_mud_1.diff.image, [214, 194, 160]) : null;

// Small tiles of real soil for the coverage paint: tilled soil, planted rows, mowed ground.
const PATTERN = {};
{
  const scratch = document.createElement("canvas").getContext("2d");
  for (const [job, img] of Object.entries(SOIL)) {
    if (!img) continue;
    const tile = document.createElement("canvas");
    tile.width = tile.height = 160;
    tile.getContext("2d").drawImage(img, 0, 0, 160, 160);
    PATTERN[job] = scratch.createPattern(tile, "repeat");
  }
}

const surface = (id, colour) => {
  const set = TEX[id] || {};
  return { map: colour ? set.diff || undefined : undefined, normalMap: set.nor || undefined, roughnessMap: set.rough || undefined };
};
const SURFACES = {
  paintedMetal: surface("metal_plate_02", false), rubber: surface("rubberized_track", false),
  // Sheet metal keeps its own paint colour: the downloaded colour map turned the shop dark and brick-like.
  sheetMetal: surface("corrugated_iron_02", false), concrete: surface("brushed_concrete", true),
  galvanised: surface("metal_plate_02", false), dirtTrack: { map: ROAD_MAP || undefined },
};
// Real metal, rubber and concrete on the generated machinery, buildings and infrastructure.
for (const module of [richModels, richDressing]) {
  if (module && typeof module.applySurfaces === "function") {
    try { module.applySurfaces(SURFACES); } catch (error) { console.warn("3D view: surfaces not applied.", error); }
  }
}

const $ = (id) => document.getElementById(id);
const clamp = (v, lo, hi) => Math.max(lo, Math.min(hi, v));
const ease = (dt, rate) => 1 - Math.exp(-dt * rate);

// ---------------------------------------------------------------- renderer

const canvas = $("scene");
const renderer = new THREE.WebGLRenderer({ canvas, antialias: true, powerPreference: "high-performance" });
renderer.setPixelRatio(Math.min(window.devicePixelRatio, 2));
renderer.toneMapping = THREE.ACESFilmicToneMapping;
renderer.toneMappingExposure = 0.5;
renderer.shadowMap.enabled = true;
renderer.shadowMap.type = THREE.PCFSoftShadowMap;

const labelRenderer = new CSS2DRenderer({ element: $("labels") });

const scene = new THREE.Scene();
scene.fog = new THREE.FogExp2(0xd9c7a8, 0.00024);
const camera = new THREE.PerspectiveCamera(40, 1, 1, 14000);
camera.position.set(120, 420, 620);

const controls = new OrbitControls(camera, canvas);
controls.enableDamping = true;
controls.dampingFactor = 0.08;
controls.maxPolarAngle = Math.PI / 2 - 0.04;
controls.minDistance = 4;
controls.maxDistance = 2600;
controls.autoRotateSpeed = 0.5;

const sky = new Sky();
sky.scale.setScalar(45000);
scene.add(sky);
const skyUniforms = sky.material.uniforms;
skyUniforms.mieCoefficient.value = 0.004;
skyUniforms.mieDirectionalG.value = 0.82;

const sun = new THREE.DirectionalLight(0xffffff, 3);
sun.castShadow = true;
sun.shadow.mapSize.set(2048, 2048);
sun.shadow.bias = -0.0004;
sun.shadow.normalBias = 0.05;
scene.add(sun, sun.target);
const hemi = new THREE.HemisphereLight(0xbfd4ee, 0x8a6f4d, 1);
scene.add(hemi);
const sunDir = new THREE.Vector3(1, 0.2, 0);
// The sky file lights and reflects on metal and other physical materials only.
// Its strength follows the clock, so the sun and sky above still set the mood.
if (ENV) scene.environment = ENV;
const envMaterials = new Set();
let envScanAt = -10;
function updateEnvironment(time, daylight) {
  if (!ENV) return;
  if (time - envScanAt > 1.5) {
    envScanAt = time;
    scene.traverse((o) => {
      if (!o.isMesh) return;
      for (const m of Array.isArray(o.material) ? o.material : [o.material]) if (m && m.isMeshStandardMaterial) envMaterials.add(m);
    });
  }
  const level = 0.1 + 0.55 * daylight;
  for (const m of envMaterials) m.envMapIntensity = level * (m.userData.envScale ?? 1);
}

function resize() {
  const w = window.innerWidth, h = window.innerHeight;
  renderer.setSize(w, h);   // also sets the CSS size, so a high-density screen is not drawn double size
  labelRenderer.setSize(w, h);
  camera.aspect = w / h;
  camera.updateProjectionMatrix();
}
window.addEventListener("resize", resize);
resize();

// ---------------------------------------------------------------- scenery

function noiseTexture(base, spots, size = 256) {
  const c = document.createElement("canvas");
  c.width = c.height = size;
  const g = c.getContext("2d");
  g.fillStyle = base;
  g.fillRect(0, 0, size, size);
  let seed = 7;
  const rnd = () => (seed = (seed * 16807) % 2147483647) / 2147483647;
  for (let i = 0; i < 2600; i++) {
    g.fillStyle = spots[i % spots.length];
    g.globalAlpha = 0.18 + rnd() * 0.25;
    const r = 0.6 + rnd() * 2.2;
    g.fillRect(rnd() * size, rnd() * size, r, r);
  }
  g.globalAlpha = 1;
  const t = new THREE.CanvasTexture(c);
  t.colorSpace = THREE.SRGBColorSpace;
  t.wrapS = t.wrapT = THREE.RepeatWrapping;
  t.anisotropy = 4;
  return t;
}

// The desert floor from a real aerial soil texture. Tiling is hidden four ways: the
// texture is laid twice at different scales and angles and mixed by a very large
// soft mask, it fades with distance, the fine grain only shows near the camera,
// and the terrain's own per-vertex colour varies over kilometres.
function realGround() {
  const dirt = TEX.dirt_aerial_03 || {};
  if (!dirt.diff) return null;
  const material = new THREE.MeshStandardMaterial({ map: dirt.diff, vertexColors: true, roughness: 1, metalness: 0 });
  if (dirt.nor) {
    const normal = dirt.nor.clone();
    normal.repeat.set(4200, 4200);
    normal.needsUpdate = true;
    material.normalMap = normal;
    material.normalScale.set(0.8, 0.8);
  }
  material.userData.envScale = 0.3;
  material.onBeforeCompile = (shader) => {
    shader.fragmentShader = shader.fragmentShader.replace("#include <map_fragment>", `
#ifdef USE_MAP
  // The texture is divided by its own average colour, so it only varies the vertex colour.
  vec3 gMean = vec3( 0.188, 0.122, 0.072 );
  float gDist = length( vViewPosition );
  vec3 gA = texture2D( map, mat2( 0.891, -0.454, 0.454, 0.891 ) * vMapUv * 610.0 ).rgb / gMean;
  vec3 gB = texture2D( map, mat2( 0.629, 0.777, -0.777, 0.629 ) * vMapUv * 173.0 ).rgb / gMean;
  float gMask = smoothstep( 0.6, 1.4, dot( texture2D( map, mat2( 0.5, 0.866, -0.866, 0.5 ) * vMapUv * 19.0 ).rgb / gMean, vec3( 0.333 ) ) );
  vec3 gCol = mix( vec3( 1.0 ), mix( gA, gB, gMask ), mix( 0.7, 0.3, smoothstep( 250.0, 1400.0, gDist ) ) );
  gCol *= mix( 0.92, 1.07, gMask );
  vec3 gGrain = texture2D( map, vMapUv * 4200.0 ).rgb / gMean;
  gCol *= mix( vec3( 1.0 ), gGrain, ( 1.0 - smoothstep( 10.0, 120.0, gDist ) ) * 0.9 );
  diffuseColor.rgb *= gCol;
#endif`);
  };
  return material;
}

function buildScenery() {
  if (richTerrain) {
    const ok = attempt(() => {
      const g = richTerrain.buildGround(), m = richTerrain.buildMountains();
      g.traverse((o) => {
        if (!o.isMesh) return;
        o.material = realGround() || o.material;
        o.material.color.setRGB(1.5, 1.42, 1.3);   // sunlit sand
      });
      scene.add(g, m);
      return true;
    }, () => false);
    if (ok) return;
  }
  const groundMap = noiseTexture("#c9a57a", ["#b8926a", "#d8b98e", "#a8845e", "#cfae86"]);
  groundMap.repeat.set(160, 160);
  const ground = new THREE.Mesh(new THREE.PlaneGeometry(16000, 16000),
                                new THREE.MeshLambertMaterial({ map: groundMap }));
  ground.rotation.x = -Math.PI / 2;
  ground.receiveShadow = true;
  scene.add(ground);

  // Low mountains on the horizon: two rings, the far one paler.
  for (const [radius, height, color, phase] of [[3300, 300, 0x8f7469, 0.0], [4300, 460, 0xa08d88, 1.7]]) {
    const steps = 220, positions = [], indices = [];
    for (let i = 0; i <= steps; i++) {
      const a = (i / steps) * Math.PI * 2;
      const h = height * (0.35 + 0.3 * Math.abs(Math.sin(a * 3 + phase)) + 0.22 * Math.abs(Math.sin(a * 7.3 + phase * 2))
                          + 0.13 * Math.abs(Math.sin(a * 17.1 + phase)));
      positions.push(Math.cos(a) * radius, 0, Math.sin(a) * radius);
      positions.push(Math.cos(a) * (radius + 420), h, Math.sin(a) * (radius + 420));
      if (i < steps) { const k = i * 2; indices.push(k, k + 1, k + 2, k + 1, k + 3, k + 2); }
    }
    const geo = new THREE.BufferGeometry();
    geo.setAttribute("position", new THREE.Float32BufferAttribute(positions, 3));
    geo.setIndex(indices);
    geo.computeVertexNormals();
    scene.add(new THREE.Mesh(geo, new THREE.MeshLambertMaterial({ color, side: THREE.DoubleSide, flatShading: true })));
  }
}
buildScenery();

// ---------------------------------------------------------------- the farm

const world = new THREE.Group();   // everything built from the farm file
scene.add(world);
let farmKey = "";
let nodes = {};                    // id -> node from the farm file
let fields = {};                   // field id -> {node, side, mesh, canvas, ctx, texture, base, key, flash}
let roadMeshes = [];
let barrierGroup = new THREE.Group();
let yard = null;
let lineWidth = 0;
let overview = { target: new THREE.Vector3(), position: new THREE.Vector3(0, 400, 600) };

const P = (x, y, h = 0) => new THREE.Vector3(x * U, h, -y * U);   // farm km -> scene
const nodePos = (id, h = 0) => P(nodes[id].x, nodes[id].y, h);

// The farm file gives each field a centre and an area, and no outline. So a
// block is drawn as a square of exactly that area, centred on its node and
// lined up north-south. A pivot field (shape "pivot", or "Pivot" in its name)
// is drawn as a circle of exactly that area.
function isPivot(node) {
  return node.shape === "pivot" || /pivot/i.test(node.name || "");
}
function fieldSide(node) {
  const squareMetres = Math.max(0.1, node.area_ha || 1) * 10000;
  return (isPivot(node) ? 2 * Math.sqrt(squareMetres / Math.PI) : Math.sqrt(squareMetres)) * M;   // width of its outline
}

function drawCrop(g, S, crop) {
  const row = (color, gap, width) => { g.fillStyle = color; for (let y = gap / 2; y < S; y += gap) g.fillRect(0, y, S, width); };
  if (crop === "lettuce") { g.fillStyle = "#4a3a28"; g.fillRect(0, 0, S, S); row("#6fbf45", 7, 5); }
  else if (crop === "citrus") {
    g.fillStyle = "#6e5a3e"; g.fillRect(0, 0, S, S);
    for (let y = 9; y < S; y += 18) for (let x = 9; x < S; x += 18) {
      g.fillStyle = "#1f4a20"; g.beginPath(); g.arc(x, y, 7.6, 0, 7); g.fill();
      g.fillStyle = "#3f7a37"; g.beginPath(); g.arc(x - 1.5, y - 1.5, 3.4, 0, 7); g.fill();
    }
  } else if (crop === "alfalfa") {
    g.fillStyle = "#5c8f3d"; g.fillRect(0, 0, S, S);
    row("#69a046", 12, 5);
  } else if (crop === "cotton") {
    g.fillStyle = "#5d4a34"; g.fillRect(0, 0, S, S); row("#4f7a3a", 8, 5);
    g.fillStyle = "#f2efe6";
    for (let y = 4; y < S; y += 8) for (let x = (y * 7) % 11; x < S; x += 11) g.fillRect(x, y, 2, 2);
  } else { g.fillStyle = "#8c9a55"; g.fillRect(0, 0, S, S); row("#7f8d4c", 10, 4); }
  g.strokeStyle = "#5b4630"; g.lineWidth = 6; g.strokeRect(3, 3, S - 6, S - 6);   // field edge
}

// Free everything a subtree holds on the GPU, and its labels. The downloaded
// textures themselves are kept, since the next farm uses them again. Geometry and
// materials that the model modules share are simply uploaded again when next drawn.
const KEEP_TEXTURES = new Set([...Object.values(TEX).flatMap((set) => Object.values(set)), ROAD_MAP, ENV].filter(Boolean));
function freeTree(root) {
  if (!root) return;
  root.traverse((o) => {
    if (o.isCSS2DObject) o.element.remove();
    if (!o.isMesh) return;
    o.geometry.dispose();
    if (o.isInstancedMesh) o.dispose();
    for (const m of Array.isArray(o.material) ? o.material : [o.material]) {
      for (const key of ["map", "normalMap", "roughnessMap"]) if (m[key] && !KEEP_TEXTURES.has(m[key])) m[key].dispose();
      m.dispose();
    }
  });
}

function clearFarm() {
  for (const field of Object.values(fields)) { freeTree(field.mesh); freeTree(field.flash); }
  for (const mesh of roadMeshes) mesh.geometry.dispose();
  for (const object of perFarm) freeTree(object);
  freeTree(yard);
  freeTree(barrierGroup);
  barrierGroup.clear();
  barrierKey = null;
  perFarm = [];
  envMaterials.clear();
  world.clear();
}
let perFarm = [];   // dressing and scrub, built for one farm
let barrierKey = null;

function buildFarm(farm) {
  clearFarm();
  nodes = {}; fields = {}; roadMeshes = []; lineWidth = 0;
  for (const node of farm.nodes) nodes[node.id] = node;

  for (const node of farm.nodes) {
    if (node.kind !== "field") continue;
    const side = fieldSide(node), S = 1024;
    const base = document.createElement("canvas"); base.width = base.height = S;
    const baseCtx = base.getContext("2d");
    attempt(() => { if (!richTerrain) return null; richTerrain.drawCrop(baseCtx, S, node.crop); return true; },
            () => { drawCrop(baseCtx, S, node.crop); return true; });
    const cv = document.createElement("canvas"); cv.width = cv.height = S;
    const ctx = cv.getContext("2d");
    ctx.drawImage(base, 0, 0);
    const texture = new THREE.CanvasTexture(cv);
    texture.colorSpace = THREE.SRGBColorSpace;
    texture.anisotropy = 8;
    const round = isPivot(node);
    const mesh = new THREE.Mesh(round ? new THREE.CircleGeometry(side / 2, 96) : new THREE.PlaneGeometry(side, side), new THREE.MeshLambertMaterial({ map: texture, polygonOffset: true, polygonOffsetFactor: -2, polygonOffsetUnits: -80 }));
    mesh.rotation.x = -Math.PI / 2;
    mesh.position.copy(P(node.x, node.y, 0.02));
    mesh.receiveShadow = true;
    const flash = new THREE.Mesh(round ? new THREE.CircleGeometry(side * 0.53, 64) : new THREE.PlaneGeometry(side * 1.06, side * 1.06),
      new THREE.MeshBasicMaterial({ color: 0xffffff, transparent: true, opacity: 0, depthWrite: false, depthTest: false }));
    flash.renderOrder = 3;
    flash.rotation.x = -Math.PI / 2;
    flash.position.copy(P(node.x, node.y, 0.05));
    flash.visible = false;
    const tag = document.createElement("div");
    tag.className = "fieldtag";
    tag.textContent = node.name || node.id;
    const label = new CSS2DObject(tag);
    label.position.set(0, side / 2, 0.1);   // on the north edge, so the name does not cover the field
    label.center.set(0.5, 1.15);
    mesh.add(label);
    world.add(mesh, flash);
    fields[node.id] = { node, side, round, radius: side / 2, mesh, ctx, base, texture, key: "", flash, flashT: 0, S, tag };
    if (round) { baseCtx.strokeStyle = "#5b4630"; baseCtx.lineWidth = 6; baseCtx.beginPath(); baseCtx.arc(S / 2, S / 2, S / 2 - 3, 0, 7); baseCtx.stroke(); ctx.drawImage(base, 0, 0); }
  }

  world.add(barrierGroup);
  yard = buildYard(farm);
  world.add(yard);

  // Farm infrastructure around the fields: tracks, head ditches, a pivot arm, windbreaks, poles.
  if (richDressing) attempt(() => {
    const depot = farm.nodes.find((n) => n.kind === "depot");
    const dressing = richDressing.buildDressing({
      fields: Object.values(fields).map((f) => ({ x: f.mesh.position.x, z: f.mesh.position.z, side: f.side, round: f.round })),
      roads: (farm.roads || []).filter(([a, b]) => nodes[a] && nodes[b]).map(([a, b]) => {
        const pa = nodePos(a), pb = nodePos(b);
        return [{ x: pa.x, z: pa.z }, { x: pb.x, z: pb.z }];
      }),
      yard: depot ? { x: P(depot.x, depot.y).x, z: P(depot.x, depot.y).z } : { x: 0, z: 0 },
    });
    shapeWindbreak(dressing);
    world.add(dressing);
    perFarm.push(dressing);
    return dressing;
  }, () => null);

  if (richTerrain) attempt(() => {
    const keepOut = [];
    for (const node of farm.nodes) {
      const p = P(node.x, node.y);
      keepOut.push({ x: p.x, z: p.z, r: node.kind === "field" ? fieldSide(node) * 0.75 + 4 : 14 });   // covers a square's corners
    }
    for (const [a, b] of farm.roads || []) {
      if (!nodes[a] || !nodes[b]) continue;
      const pa = nodePos(a), pb = nodePos(b), steps = Math.ceil(pa.distanceTo(pb) / 6);
      for (let i = 0; i <= steps; i++) { const p = pa.clone().lerp(pb, i / steps); keepOut.push({ x: p.x, z: p.z, r: 5 }); }
    }
    const scrub = richTerrain.buildScrub(keepOut);
    world.add(scrub);
    perFarm.push(scrub);
    return scrub;
  }, () => null);

  // Frame the whole farm for the overview.
  const box = new THREE.Box3();
  for (const node of farm.nodes) box.expandByPoint(P(node.x, node.y));
  const centre = box.getCenter(new THREE.Vector3()), size = box.getSize(new THREE.Vector3());
  const span = Math.max(size.x, size.z * 1.3, 200);
  overview.target.copy(centre);
  overview.position.set(centre.x + span * 0.12, span * 0.24, centre.z + span * 0.66);
  flyTo(overview.position, overview.target, 0);
}

// Windbreak trees: no CC0 tree model was light enough to repeat along every field,
// so the generated crowns are made lumpy and tall, each with its own shade of green.
function shapeWindbreak(dressing) {
  const crowns = dressing.getObjectByName("treeCrowns");
  if (!crowns || !crowns.isInstancedMesh) return;
  const old = crowns.geometry;
  old.computeBoundingBox();
  const centre = old.boundingBox.getCenter(new THREE.Vector3()), radius = old.boundingBox.getSize(new THREE.Vector3()).x / 2;
  const geo = new THREE.IcosahedronGeometry(radius, 2);
  const pos = geo.attributes.position, v = new THREE.Vector3();
  for (let i = 0; i < pos.count; i++) {
    v.fromBufferAttribute(pos, i);
    const lump = 1 + 0.22 * Math.sin(v.x * 23 + v.y * 11) * Math.cos(v.z * 19 + v.y * 7);
    v.multiplyScalar(lump);
    v.y = v.y * 1.45 + radius * 0.25;            // taller than wide, like a tamarisk or eucalyptus
    pos.setXYZ(i, v.x + centre.x, v.y + centre.y, v.z + centre.z);
  }
  geo.computeVertexNormals();
  crowns.geometry = geo;
  const shade = new THREE.Color();
  for (let i = 0; i < crowns.count; i++) {
    const k = (Math.sin(i * 12.9898) * 43758.5453) % 1;
    shade.setHSL(0.24 + 0.04 * k, 0.32 + 0.1 * Math.abs(k), 0.3 + 0.07 * k);
    crowns.setColorAt(i, shade);
  }
  crowns.material.color.set(0xffffff);
  if (crowns.instanceColor) crowns.instanceColor.needsUpdate = true;
}

function ribbon(points, width, y) {
  // A flat strip along a polyline, built in order so drawRange can reveal it.
  const positions = [], indices = [], uvs = [];
  let quads = 0, run = 0;
  for (let i = 0; i + 1 < points.length; i++) {
    const a = points[i], b = points[i + 1];
    const dx = b.x - a.x, dz = b.z - a.z, len = Math.hypot(dx, dz);
    if (len < 1e-6) continue;
    const nx = (-dz / len) * width / 2, nz = (dx / len) * width / 2;
    positions.push(a.x + nx, y, a.z + nz, a.x - nx, y, a.z - nz, b.x + nx, y, b.z + nz, b.x - nx, y, b.z - nz);
    uvs.push(0, run / ROAD_TILE, 1, run / ROAD_TILE, 0, (run + len) / ROAD_TILE, 1, (run + len) / ROAD_TILE);
    run += len;
    const k = quads * 4;
    indices.push(k, k + 2, k + 1, k + 1, k + 2, k + 3);
    quads++;
  }
  const geo = new THREE.BufferGeometry();
  geo.setAttribute("position", new THREE.Float32BufferAttribute(positions, 3));
  geo.setAttribute("uv", new THREE.Float32BufferAttribute(uvs, 2));
  geo.setIndex(indices);
  geo.computeVertexNormals();
  return geo;
}
const ROAD_TILE = 5;   // the road texture repeats every 50 m along the road

function buildRoads(farm, width) {
  for (const mesh of roadMeshes) { world.remove(mesh); mesh.geometry.dispose(); }
  roadMeshes = [];
  const material = ROAD_MAP
    ? new THREE.MeshStandardMaterial({ map: ROAD_MAP, color: 0xffffff, roughness: 1, metalness: 0,
                                       polygonOffset: true, polygonOffsetFactor: -1, polygonOffsetUnits: -40 })
    : new THREE.MeshLambertMaterial({ color: 0xe6d0aa, polygonOffset: true, polygonOffsetFactor: -1, polygonOffsetUnits: -40 });
  material.userData.envScale = 0.3;
  for (const [a, b] of farm.roads || []) {
    if (!nodes[a] || !nodes[b]) continue;
    const mesh = new THREE.Mesh(ribbon([nodePos(a), nodePos(b)], width, 0.01), material);
    mesh.receiveShadow = true;
    world.add(mesh);
    roadMeshes.push(mesh);
  }
}

function buildBarriers(blocked) {
  const key = JSON.stringify(blocked || []);
  if (key === barrierKey) return;
  barrierKey = key;
  freeTree(barrierGroup);
  barrierGroup.clear();
  for (const [a, b] of blocked || []) {
    if (!nodes[a] || !nodes[b]) continue;
    const pa = nodePos(a), pb = nodePos(b), mid = pa.clone().lerp(pb, 0.5);
    const group = new THREE.Group();
    const post = new THREE.MeshLambertMaterial({ color: 0xf2f2f2 });
    const bar = new THREE.Mesh(new THREE.BoxGeometry(12, 1.6, 0.6), new THREE.MeshLambertMaterial({ color: 0xe5352b, emissive: 0x7a1510 }));
    bar.position.y = 4;
    group.add(bar);
    for (const x of [-5, 5]) {
      const leg = new THREE.Mesh(new THREE.BoxGeometry(0.6, 5, 0.6), post);
      leg.position.set(x, 2.5, 0);
      group.add(leg);
    }
    for (const x of [-3, 0, 3]) {
      const stripe = new THREE.Mesh(new THREE.BoxGeometry(1.4, 1.7, 0.7), post);
      stripe.position.set(x, 4, 0);
      group.add(stripe);
    }
    group.position.copy(mid);
    group.rotation.y = Math.atan2(pb.x - pa.x, pb.z - pa.z);   // across the road
    group.userData.scaleWithCamera = true;
    barrierGroup.add(group);
  }
}

// ---------------------------------------------------------------- models (built in metres)

const mat = (color, extra = {}) => new THREE.MeshStandardMaterial({ color, roughness: 0.75, metalness: 0.1, ...extra });
const DARK = mat(0x1c1d1f, { roughness: 0.95 });
const STEEL = mat(0x6d7378, { metalness: 0.5, roughness: 0.5 });
const GLASS = mat(0x1d2b33, { roughness: 0.2, metalness: 0.6 });

function box(w, h, d, material, x, y, z) {
  const m = new THREE.Mesh(new THREE.BoxGeometry(w, h, d), material);
  m.position.set(x, y, z);
  m.castShadow = true;
  return m;
}
function wheel(radius, width, x, z) {
  const m = new THREE.Mesh(new THREE.CylinderGeometry(radius, radius, width, 18), DARK);
  m.rotation.x = Math.PI / 2;
  m.position.set(x, radius, z);
  m.castShadow = true;
  return m;
}

function tractorModel(color) {
  return attempt(() => (richModels ? richModels.buildTractor(color) : null), () => proceduralTractor(color));
}

function proceduralTractor(color) {
  // Faces +x. About 5.2 m long, 2.5 m wide, 3.2 m tall.
  const body = mat(color);
  const g = new THREE.Group();
  g.add(box(4.2, 0.7, 1.1, STEEL, 0.1, 1.05, 0));
  g.add(box(2.3, 1.05, 1.15, body, 1.05, 1.85, 0));              // hood
  g.add(box(0.12, 0.8, 1.0, DARK, 2.22, 1.8, 0));                 // grille
  g.add(box(1.7, 1.55, 1.6, GLASS, -0.85, 2.35, 0));              // cab
  g.add(box(1.9, 0.14, 1.8, body, -0.85, 3.18, 0));               // roof
  for (const z of [-1.15, 1.15]) {
    g.add(wheel(0.95, 0.62, -1.15, z));
    g.add(wheel(0.6, 0.42, 1.55, z * 0.9));
    g.add(box(1.5, 0.12, 0.7, body, -1.15, 2.0, z));              // fender
  }
  const stack = new THREE.Mesh(new THREE.CylinderGeometry(0.07, 0.07, 1.3, 8), DARK);
  stack.position.set(0.5, 3.0, 0.48);
  g.add(stack);
  return g;
}

function implementModel(type, width) {
  return attempt(() => (richModels ? richModels.buildImplement(type, width) : null), () => plainImplement(type, width));
}

function plainImplement(type, width) {
  // Sits behind the tractor (negative x). The working width runs along z, at true size.
  const g = new THREE.Group();
  const W = Math.max(1, width);
  if (type === "spray") {
    const frame = mat(0xd9d2c0), tank = mat(0xf3efe2, { roughness: 0.4 });
    g.add(box(2.6, 0.3, 1.6, STEEL, -4.0, 1.0, 0));
    const t = new THREE.Mesh(new THREE.CylinderGeometry(0.95, 0.95, 2.4, 16), tank);
    t.rotation.z = Math.PI / 2; t.position.set(-4.0, 2.05, 0); t.castShadow = true;
    g.add(t);
    for (const z of [-1.0, 1.0]) g.add(wheel(0.75, 0.4, -4.2, z));
    g.add(box(0.18, 0.18, W, frame, -5.6, 1.25, 0));              // the boom, full working width
    g.add(box(0.1, 0.1, W, STEEL, -5.6, 1.7, 0));
    for (let z = -W / 2; z <= W / 2 + 1e-6; z += W / 6) g.add(box(0.08, 0.5, 0.08, STEEL, -5.6, 1.45, z));
    const nozzles = new THREE.InstancedMesh(new THREE.BoxGeometry(0.1, 0.22, 0.1), DARK, Math.floor(W / 0.5) + 1);
    const d = new THREE.Object3D();
    for (let i = 0; i < nozzles.count; i++) { d.position.set(-5.6, 1.05, -W / 2 + i * 0.5); d.updateMatrix(); nozzles.setMatrixAt(i, d.matrix); }
    g.add(nozzles);
  } else if (type === "mow") {
    const deck = mat(0xc23b2e);
    g.add(box(1.6, 0.14, 0.2, STEEL, -2.6, 0.9, 0));
    g.add(box(1.5, 0.45, W, deck, -3.6, 0.55, 0));                // cutter bar and cover
    g.add(box(0.5, 0.25, W, DARK, -4.35, 0.35, 0));               // swath curtain
  } else if (type === "till") {
    const frame = mat(0x2f5fa8);
    g.add(box(2.8, 0.2, 0.25, frame, -3.2, 1.0, 0));
    for (const x of [-3.0, -4.6]) g.add(box(0.2, 0.2, W, frame, x, 1.0, 0));
    const count = Math.floor(W / 0.24) + 1;
    const discs = new THREE.InstancedMesh(new THREE.CylinderGeometry(0.33, 0.33, 0.03, 14), STEEL, count * 2);
    discs.castShadow = true;
    const d = new THREE.Object3D();
    let n = 0;
    for (const [x, tilt] of [[-3.0, 0.3], [-4.6, -0.3]]) for (let i = 0; i < count; i++) {
      d.position.set(x, 0.33, -W / 2 + i * 0.24);
      d.rotation.set(Math.PI / 2, 0, 0); d.rotateZ(tilt);
      d.updateMatrix(); discs.setMatrixAt(n++, d.matrix);
    }
    g.add(discs);
  } else {   // plant
    const frame = mat(0x2e7d46), hopper = mat(0xf0c93a);
    g.add(box(2.4, 0.2, 0.25, frame, -3.0, 1.0, 0));
    g.add(box(0.3, 0.3, W, frame, -4.0, 1.0, 0));                 // toolbar
    const rows = Math.max(2, Math.round(W / 0.76));
    const boxes = new THREE.InstancedMesh(new THREE.BoxGeometry(0.5, 0.55, 0.42), hopper, rows);
    const wheels = new THREE.InstancedMesh(new THREE.CylinderGeometry(0.22, 0.22, 0.1, 10), DARK, rows);
    boxes.castShadow = true;
    const d = new THREE.Object3D();
    for (let i = 0; i < rows; i++) {
      const z = -W / 2 + (i + 0.5) * (W / rows);
      d.rotation.set(0, 0, 0); d.position.set(-4.3, 1.45, z); d.updateMatrix(); boxes.setMatrixAt(i, d.matrix);
      d.rotation.set(Math.PI / 2, 0, 0); d.position.set(-4.9, 0.22, z); d.updateMatrix(); wheels.setMatrixAt(i, d.matrix);
    }
    g.add(boxes, wheels);
  }
  return g;
}

function buildYard(farm) {
  const depot = farm.nodes.find((n) => n.kind === "depot");
  const g = new THREE.Group();
  if (!depot) return g;
  g.position.copy(P(depot.x, depot.y));
  const gravel = TEX.gravelly_sand || {};
  const padMaterial = gravel.diff
    ? new THREE.MeshStandardMaterial({ map: gravel.diff.clone(), normalMap: gravel.nor ? gravel.nor.clone() : null,
                                       color: new THREE.Color(1.45, 2.05, 3.0), roughness: 1, metalness: 0 })
    : new THREE.MeshLambertMaterial({ color: 0x8f8068 });
  for (const map of [padMaterial.map, padMaterial.normalMap]) if (map) { map.repeat.set(12, 9.5); map.needsUpdate = true; }
  Object.assign(padMaterial, { polygonOffset: true, polygonOffsetFactor: -3, polygonOffsetUnits: -120 });
  padMaterial.userData.envScale = 0.3;
  const pad = new THREE.Mesh(new THREE.PlaneGeometry(9.6, 7.6), padMaterial);
  pad.rotation.x = -Math.PI / 2; pad.position.y = 0.015; pad.receiveShadow = true;
  g.add(pad);

  const m = new THREE.Group();       // yard furniture, modelled in metres
  m.scale.setScalar(M);
  const rich = attempt(() => (richModels ? richModels.buildYard() : null), () => null);
  if (rich) {
    m.add(rich);
    g.add(m);
    g.userData.parked = new THREE.Group();
    g.userData.parked.scale.setScalar(M);
    g.add(g.userData.parked);
    g.userData.parkedKey = "";
    return g;
  }
  const wall = mat(0xd8d2c4), roof = mat(0x8c3a2e), tank = mat(0xe9e6df, { roughness: 0.35 });
  m.add(box(30, 6, 16, wall, -30, 3, -25));                        // shop
  const ridge = new THREE.Mesh(new THREE.CylinderGeometry(9.4, 9.4, 30.6, 3, 1), roof);
  ridge.rotation.z = Math.PI / 2; ridge.rotation.x = Math.PI / 2 * 0; ridge.scale.set(1, 1, 0.42);
  ridge.rotation.set(0, 0, Math.PI / 2); ridge.position.set(-30, 7.9, -25); ridge.castShadow = true;
  m.add(ridge);
  m.add(box(8, 4.6, 0.3, DARK, -30, 2.3, -16.9));                  // shop door
  const fuel = new THREE.Mesh(new THREE.CylinderGeometry(1.5, 1.5, 6, 18), tank);   // fuel tank on a stand
  fuel.rotation.z = Math.PI / 2; fuel.position.set(28, 3.2, -28); fuel.castShadow = true;
  m.add(fuel);
  for (const x of [26, 30]) m.add(box(0.4, 1.8, 2.4, STEEL, x, 0.9, -28));
  m.add(box(0.8, 1.8, 0.6, mat(0xc23b2e), 24, 0.9, -24));          // pump
  g.add(m);
  g.userData.parked = new THREE.Group();
  g.userData.parked.scale.setScalar(M);
  g.add(g.userData.parked);
  g.userData.parkedKey = "";
  return g;
}

// Parked at the yard: every implement in the farm's catalog that no tractor has hitched.
function updateParked(state) {
  const catalog = implementCatalog(state);
  const mounted = new Set(state.foreman.tractors.map((t) => t.mounted));
  const parked = Object.keys(catalog).filter((id) => !mounted.has(id)).sort();
  const key = parked.join(",");
  if (!yard || yard.userData.parkedKey === key) return;
  yard.userData.parkedKey = key;
  const lot = yard.userData.parked;
  freeTree(lot);
  lot.clear();
  let z = 14;
  for (const id of parked) {
    const model = implementModel(catalog[id].type, catalog[id].width);
    model.rotation.y = Math.PI / 2;                 // working width along the lot
    model.position.set(-34 + catalog[id].width / 2, 0, z + 4);
    lot.add(model);
    z += 9;
  }
}

function implementCatalog(state) {
  const catalog = {};
  const add = (id, type, width) => { if (id && !catalog[id]) catalog[id] = { type, width }; };
  for (const item of state.farm.implements || []) add(item.id, item.type, item.width_m);
  // Before the server sends farm.implements, read what the state does show.
  const guess = (id, type) => {
    const t = type || TYPE_BY_PREFIX[String(id).split("_")[0]] || "till";
    const n = parseFloat(String(id).split("_").pop());
    add(id, t, Number.isFinite(n) ? n : FALLBACK_WIDTH[t]);
  };
  const jobType = Object.fromEntries(state.foreman.jobs.map((j) => [j.id, j.type]));
  for (const row of state.foreman.plan) guess(row.implement, jobType[row.job]);
  for (const t of state.foreman.tractors) guess(t.mounted);
  return catalog;
}

// ---------------------------------------------------------------- tractors

const tractorLayer = new THREE.Group();
scene.add(tractorLayer);
const tractors = {};   // id -> view
let glbTemplate = null;

fetch(TRACTOR_GLB, { method: "HEAD" }).then((r) => {
  if (!r.ok) return;
  new GLTFLoader().load(TRACTOR_GLB, (gltf) => {
    const model = gltf.scene, size = new THREE.Box3().setFromObject(model).getSize(new THREE.Vector3());
    if (size.z > size.x) model.rotation.y = Math.PI / 2;          // long axis along x
    model.scale.multiplyScalar(5.2 / Math.max(size.x, size.z, 0.001));
    model.updateMatrixWorld(true);
    const b = new THREE.Box3().setFromObject(model);
    model.position.y -= b.min.y;
    glbTemplate = model;
    for (const view of Object.values(tractors)) setBody(view);    // swap in for tractors already made
  }, undefined, () => {});
}).catch(() => {});

function setBody(view) {
  if (view.body) view.inner.remove(view.body);
  if (glbTemplate) {
    view.body = glbTemplate.clone(true);
    view.body.traverse((o) => {
      if (!o.isMesh) return;
      o.castShadow = true;
      o.material = o.material.clone();
      if (o.material.color) o.material.color.lerp(new THREE.Color(view.color), 0.55);   // tint per tractor
    });
  } else view.body = tractorModel(view.color);
  view.wheels = ["wheelRL", "wheelRR", "wheelFL", "wheelFR"].map((name) => view.body.getObjectByName(name)).filter(Boolean);
  view.inner.add(view.body);
}

function makeTractor(t) {
  const group = new THREE.Group();          // position and heading
  const inner = new THREE.Group();          // metres, times the icon scale
  group.add(inner);
  const view = { id: t.id, color: t.color || "#e8a33d", group, inner, body: null, implement: null, mounted: null,
                 pos: null, heading: 0, target: new THREE.Vector3(), targetHeading: 0, data: t };
  setBody(view);

  const amber = new THREE.MeshBasicMaterial({ color: 0xffb020 });
  view.hazards = [];
  for (const z of [-0.8, 0.8]) {
    const lamp = new THREE.Mesh(new THREE.SphereGeometry(0.22, 10, 8), amber);
    lamp.position.set(-0.85, 3.42, z);
    lamp.visible = false;
    inner.add(lamp);
    view.hazards.push(lamp);
  }
  view.headlights = [];
  const lampMat = new THREE.MeshBasicMaterial({ color: 0xfff2c4 });
  for (const z of [-0.42, 0.42]) {
    const lamp = new THREE.Mesh(new THREE.BoxGeometry(0.1, 0.24, 0.3), lampMat);
    lamp.position.set(2.3, 2.05, z);
    lamp.visible = false;
    inner.add(lamp);
    view.headlights.push(lamp);
  }
  const beam = new THREE.Mesh(new THREE.ConeGeometry(2.6, 9, 14, 1, true),
    new THREE.MeshBasicMaterial({ color: 0xfff0c0, transparent: true, opacity: 0.16, depthWrite: false, side: THREE.DoubleSide }));
  beam.rotation.z = Math.PI / 2; beam.position.set(6.8, 1.6, 0); beam.visible = false;
  inner.add(beam);
  view.beam = beam;

  // A ring on the ground in the tractor's colour, and a red one when it is broken.
  view.ring = new THREE.Mesh(new THREE.RingGeometry(4.6, 5.4, 40),
    new THREE.MeshBasicMaterial({ color: view.color, transparent: true, opacity: 0.75, depthWrite: false, side: THREE.DoubleSide }));
  view.ring.rotation.x = -Math.PI / 2; view.ring.position.y = 0.05;
  Object.assign(view.ring.material, { polygonOffset: true, polygonOffsetFactor: -5, polygonOffsetUnits: -200 });
  inner.add(view.ring);
  view.warn = new THREE.Mesh(new THREE.ConeGeometry(0.9, 1.8, 3),
    new THREE.MeshBasicMaterial({ color: 0xef3b30 }));
  view.warn.position.set(-0.85, 5.6, 0); view.warn.visible = false;
  inner.add(view.warn);

  const hit = new THREE.Mesh(new THREE.SphereGeometry(4.5, 10, 8), new THREE.MeshBasicMaterial({ visible: false }));
  hit.position.y = 1.8;
  hit.userData.tractor = t.id;
  inner.add(hit);
  view.hit = hit;

  const tag = document.createElement("div");
  tag.className = "tag";
  view.tag = tag;
  const label = new CSS2DObject(tag);
  label.position.set(0, 4.4, 0);
  label.center.set(0.5, 1.2);
  inner.add(label);

  // Dust (tilling) and mist (spraying): a few soft puffs recycled behind the implement.
  const puffs = new THREE.InstancedMesh(new THREE.SphereGeometry(1, 8, 6),
    new THREE.MeshBasicMaterial({ color: 0xd8c3a0, transparent: true, opacity: 0.2, depthWrite: false }), 14);
  puffs.frustumCulled = false; puffs.visible = false;
  tractorLayer.add(puffs);
  view.puffs = puffs;
  view.puffData = Array.from({ length: 14 }, () => ({ life: 0, p: new THREE.Vector3(), s: 0 }));
  view.puffNext = 0;

  tractorLayer.add(group);
  tractors[t.id] = view;
  return view;
}

function setImplement(view, id, catalog) {
  if (view.mounted === id) return;
  view.mounted = id;
  if (view.implement) { view.inner.remove(view.implement); freeTree(view.implement); }
  const spec = catalog[id];
  view.implement = spec ? implementModel(spec.type, spec.width) : null;
  if (view.implement) view.inner.add(view.implement);
  view.spec = spec || null;
}

// ---------------------------------------------------------------- field work

const shown = {};      // job id -> percent drawn, eased toward the reported percent

// Passes run east-west, one implement width apart, from the north edge down,
// turning at each end. On a pivot circle each pass is the chord strip at that
// position. The share of the outline's area covered equals the job's percent.

// Area of one pass strip of a circle that lies west of `front` (centre origin).
function stripArea(radius, yA, yB, front) {
  const n = 24;
  let sum = 0;
  for (let k = 0; k < n; k++) {
    const y = yA + (k + 0.5) * (yB - yA) / n, c = Math.sqrt(Math.max(0, radius * radius - y * y));
    sum += clamp(front + c, 0, 2 * c);
  }
  return sum * (yB - yA) / n;
}

// How far a full pass reaches, as [west, east] measured from the outline's west edge.
function passSpan(field, i, w) {
  if (!field.round) return [0, field.side];
  const r = field.radius, yA = -r + i * w, yB = Math.min(r, yA + w);
  const near = yA > 0 ? yA : yB < 0 ? yB : 0, half = Math.sqrt(Math.max(0, r * r - near * near));
  return [r - half, r + half];
}

// Where the work stands after `pct` percent: the pass it is on, the painted part
// of that pass (from, to), and the tractor's x. All measured from the west edge.
function passState(field, widthM, pct) {
  const side = field.side, w = Math.max(0.05, widthM * M), count = Math.ceil(side / w - 1e-9);
  const share = clamp(pct, 0, 100) / 100, r = field.radius;
  const areas = [];
  let total = 0;
  for (let i = 0; i < count; i++) {
    const height = Math.min(w, side - i * w);
    const a = field.round ? stripArea(r, -r + i * w, -r + i * w + height, r) : height * side;
    areas.push(a);
    total += a;
  }
  let left = share * total;
  for (let i = 0; i < count; i++) {
    if (left < areas[i] - 1e-9 || i === count - 1) {
      const passWidth = Math.min(w, side - i * w), east = i % 2 === 0, [west, eastEnd] = passSpan(field, i, w);
      let reach;                                   // distance driven along this pass, from its starting end
      if (!field.round) reach = clamp(left / areas[i], 0, 1) * side;
      else {
        const half = (eastEnd - west) / 2, yA = -r + i * w;
        let lo = -half, hi = half;
        for (let k = 0; k < 22; k++) {
          const mid = (lo + hi) / 2;
          if (stripArea(r, yA, yA + passWidth, mid) < left) lo = mid; else hi = mid;
        }
        reach = left >= areas[i] ? 2 * half : (lo + hi) / 2 + half;
      }
      return { w, pass: i, top: i * w, passWidth, east,
               from: east ? west : eastEnd - reach, to: east ? west + reach : eastEnd,
               x: east ? west + reach : eastEnd - reach };
    }
    left -= areas[i];
  }
  return { w, pass: 0, top: 0, passWidth: w, east: true, from: 0, to: 0, x: 0 };
}

// How one pass looks once worked. Real soil tiles where they loaded, flat colour if not.
function soil(g, job, flat, x, y, w, h) {
  g.fillStyle = PATTERN[job] || flat;
  g.fillRect(x, y, w, h);
}
const STRIPE = {
  till: (g, x, y, w, h, i) => { soil(g, "till", "#47331f", x, y, w, h);
                                g.fillStyle = i % 2 ? "rgba(30,18,8,0.30)" : "rgba(30,18,8,0.42)"; g.fillRect(x, y, w, h);
                                g.fillStyle = "rgba(20,12,6,0.55)"; g.fillRect(x, y + h - 1.5, w, 1.5); },
  plant: (g, x, y, w, h, i) => { soil(g, "plant", "#66503a", x, y, w, h);
                                 g.fillStyle = i % 2 ? "rgba(70,48,28,0.10)" : "rgba(70,48,28,0.18)"; g.fillRect(x, y, w, h);
                                 g.fillStyle = "rgba(48,34,20,0.5)"; const n = Math.max(2, Math.round(h / 4));
                                 for (let k = 0; k < n; k++) g.fillRect(x, y + (k + 0.5) * h / n, w, 1.4); },
  mow: (g, x, y, w, h, i) => { soil(g, "mow", "#bac878", x, y, w, h);
                               g.fillStyle = i % 2 ? "rgba(196,210,120,0.42)" : "rgba(170,190,100,0.42)"; g.fillRect(x, y, w, h);
                               g.fillStyle = "rgba(226,226,170,0.6)"; g.fillRect(x, y + h * 0.4, w, Math.max(1.5, h * 0.2)); },
  spray: (g, x, y, w, h, i) => { g.fillStyle = i % 2 ? "rgba(18,42,58,0.34)" : "rgba(18,42,58,0.26)"; g.fillRect(x, y, w, h); },
};

function paintField(field, jobs, catalog, planImplement) {
  const parts = jobs.map((j) => `${j.id}:${(shown[j.id] ?? j.pct).toFixed(1)}:${planImplement[j.id] || ""}`);
  const key = parts.join("|");
  if (key === field.key) return;
  field.key = key;
  const g = field.ctx, S = field.S, px = S / field.side;
  g.drawImage(field.base, 0, 0);
  for (const job of jobs) {
    const pct = shown[job.id] ?? job.pct;
    if (pct <= 0) continue;
    const width = (catalog[planImplement[job.id]] || {}).width || FALLBACK_WIDTH[job.type] || 6;
    const st = passState(field, width, pct), draw = STRIPE[job.type] || STRIPE.till;
    for (let i = 0; i <= st.pass; i++) {
      const y = i * st.w * px, h = Math.min(st.w, field.side - i * st.w) * px;
      const [from, to] = i < st.pass ? passSpan(field, i, st.w) : [st.from, st.to];
      if (to - from <= 0) continue;
      draw(g, from * px, y, (to - from) * px, h, i);
    }
  }
  g.strokeStyle = "#5b4630"; g.lineWidth = 6;
  if (field.round) { g.beginPath(); g.arc(S / 2, S / 2, S / 2 - 3, 0, 7); g.stroke(); }
  else g.strokeRect(3, 3, S - 6, S - 6);
  field.texture.needsUpdate = true;
}

// ---------------------------------------------------------------- routes

const routeLayer = new THREE.Group();
scene.add(routeLayer);
let routes = [];          // live route meshes: {mesh, total, drawn, fading}
let routeKey = "", routePlanNo = -1, lastPlanTractor = {};

function routePoints(state, tractorIndex, count) {
  // Offset each tractor's line sideways so shared roads show every colour.
  const offset = (tractorIndex - (count - 1) / 2) * lineWidth * 1.15;
  return (path) => {
    const pts = [];
    for (let i = 0; i < path.length; i++) {
      if (!nodes[path[i]]) continue;
      const p = nodePos(path[i]);
      const a = nodes[path[i - 1]] ? nodePos(path[i - 1]) : null, b = nodes[path[i + 1]] ? nodePos(path[i + 1]) : null;
      const dir = new THREE.Vector3().subVectors(b || p, a || p);
      if (dir.lengthSq() > 1e-9) { dir.normalize(); p.x += -dir.z * offset; p.z += dir.x * offset; }
      pts.push(p);
    }
    return pts;
  };
}

function rebuildRoutes(state, animate) {
  const jobStatus = Object.fromEntries(state.foreman.jobs.map((j) => [j.id, j.status]));
  const rows = state.foreman.plan.filter((r) => jobStatus[r.job] === "pending" && (r.path || []).length > 1)
    .sort((a, b) => a.start - b.start);
  const ids = state.foreman.tractors.map((t) => t.id);

  if (animate) for (const r of routes) { r.fading = 1.4; r.mesh.material.color.set(0xff3b30); }
  else { for (const r of routes) if (!r.fading) { routeLayer.remove(r.mesh); r.mesh.geometry.dispose(); } routes = routes.filter((r) => r.fading); }

  for (const t of state.foreman.tractors) {
    const mine = rows.filter((r) => r.tractor === t.id);
    if (!mine.length) continue;
    const toPoints = routePoints(state, ids.indexOf(t.id), ids.length);
    let pts = [];
    for (const row of mine) { const seg = toPoints(row.path); pts = pts.concat(pts.length ? seg.slice(1) : seg); }
    if (pts.length < 2) continue;
    const geo = ribbon(pts, lineWidth, 0.03);
    const total = geo.index.count;
    const mesh = new THREE.Mesh(geo, new THREE.MeshBasicMaterial({ color: t.color, transparent: true, opacity: 0.92, depthWrite: false, polygonOffset: true, polygonOffsetFactor: -4, polygonOffsetUnits: -160 }));
    mesh.renderOrder = 2;
    if (animate) geo.setDrawRange(0, 0);
    routeLayer.add(mesh);
    routes.push({ mesh, total, drawn: animate ? 0 : 1, fading: 0 });
  }
}

function updateRoutes(state) {
  const jobStatus = Object.fromEntries(state.foreman.jobs.map((j) => [j.id, j.status]));
  const key = state.foreman.plan.filter((r) => jobStatus[r.job] === "pending").map((r) => `${r.job}>${r.tractor}`).join(",")
    + "|" + lineWidth.toFixed(2);
  const planNo = state.foreman.plan_no;
  const planChanged = routePlanNo !== -1 && planNo !== routePlanNo;
  if (planChanged) {
    // Fields whose job moved to another tractor flash once.
    const jobField = Object.fromEntries(state.foreman.jobs.map((j) => [j.id, j.field]));
    for (const row of state.foreman.plan) {
      const before = lastPlanTractor[row.job];
      if (before && before !== row.tractor && fields[jobField[row.job]]) fields[jobField[row.job]].flashT = 1.6;
    }
  }
  if (planChanged || key !== routeKey) rebuildRoutes(state, planChanged);
  routeKey = key;
  routePlanNo = planNo;
  lastPlanTractor = Object.fromEntries(state.foreman.plan.map((r) => [r.job, r.tractor]));
}

// ---------------------------------------------------------------- camera moves

let fly = null, followId = null, selectedId = null;

function flyTo(position, target, seconds = 1.3) {
  if (seconds <= 0) { camera.position.copy(position); controls.target.copy(target); fly = null; return; }
  fly = { t: 0, seconds, fromP: camera.position.clone(), fromT: controls.target.clone(), toP: position.clone(), toT: target.clone() };
}

function follow(id) {
  const view = tractors[id];
  if (!view || !view.pos) return;
  followId = selectedId = id;
  const dir = new THREE.Vector3().subVectors(camera.position, controls.target).setY(0);
  if (dir.lengthSq() < 1e-6) dir.set(0.4, 0, 1);
  dir.normalize();
  const position = view.pos.clone().addScaledVector(dir, 8.5).add(new THREE.Vector3(0, 5.5, 0));
  flyTo(position, view.pos.clone().add(new THREE.Vector3(0, 0.25, 0)), 1.4);
  setButtons();
}

function setButtons() {
  fleetKey = "";
  if (state) updateHud(state);
  $("btn-follow").classList.toggle("on", followId !== null);
  $("btn-orbit").classList.toggle("on", controls.autoRotate);
}

$("btn-overview").onclick = () => { followId = null; flyTo(overview.position, overview.target); setButtons(); };
$("btn-yard").onclick = () => {
  followId = null;
  if (yard) flyTo(yard.position.clone().add(new THREE.Vector3(26, 22, 44)), yard.position.clone());
  setButtons();
};
$("btn-follow").onclick = () => {
  const ids = Object.keys(tractors).sort();
  if (!ids.length) return;
  // Follow the selected tractor, or step to the next one if already following.
  const next = followId ? ids[(ids.indexOf(followId) + 1) % ids.length] : (selectedId || ids[0]);
  follow(next);
};
$("btn-orbit").onclick = () => { controls.autoRotate = !controls.autoRotate; setButtons(); };
$("card-close").onclick = () => { selectedId = null; followId = null; setButtons(); };

const raycaster = new THREE.Raycaster(), pointer = new THREE.Vector2();
let downAt = null;
canvas.addEventListener("pointerdown", (e) => { downAt = [e.clientX, e.clientY]; });
canvas.addEventListener("pointerup", (e) => {
  if (!downAt || Math.hypot(e.clientX - downAt[0], e.clientY - downAt[1]) > 5) return;   // a drag, not a click
  pointer.set((e.clientX / window.innerWidth) * 2 - 1, -(e.clientY / window.innerHeight) * 2 + 1);
  raycaster.setFromCamera(pointer, camera);
  const hits = raycaster.intersectObjects(Object.values(tractors).map((v) => v.hit), false);
  if (hits.length) follow(hits[0].object.userData.tractor);
});

// ---------------------------------------------------------------- state

let state = null, dayStart = 360, shownNow = null, lastStateAt = 0;

function clockText(minute) {
  const total = Math.floor(dayStart + minute);
  return `${String(Math.floor(total / 60) % 24).padStart(2, "0")}:${String(total % 60).padStart(2, "0")}`;
}

function applyState(next) {
  const key = `${next.scenario}|${next.seed ?? ""}|${next.farm.name}|${next.farm.nodes.map((n) => n.id + n.x + n.y + (n.area_ha || "")).join(",")}`;
  if (key !== farmKey) {
    farmKey = key;
    buildFarm(next.farm);
    for (const id of Object.keys(tractors)) {
      freeTree(tractors[id].group);
      freeTree(tractors[id].puffs);
      tractorLayer.remove(tractors[id].group, tractors[id].puffs);
      delete tractors[id];
    }
    for (const r of routes) { routeLayer.remove(r.mesh); r.mesh.geometry.dispose(); r.mesh.material.dispose(); }
    fleetKey = "";
    routes = []; routeKey = ""; routePlanNo = -1; lastPlanTractor = {};
    for (const id of Object.keys(shown)) delete shown[id];
    followId = selectedId = null;
  }
  if (state && next.now + 1e-6 < state.now) {          // the day was reset
    for (const id of Object.keys(shown)) delete shown[id];
    for (const f of Object.values(fields)) f.key = "";
    shownNow = null;
  }
  state = next;
  dayStart = next.farm.day_start_min ?? 360;
  lastStateAt = performance.now();

  const catalog = implementCatalog(next);
  state.catalog = catalog;
  state.planImplement = Object.fromEntries(next.foreman.plan.map((r) => [r.job, r.implement]));
  state.jobById = Object.fromEntries(next.foreman.jobs.map((j) => [j.id, j]));
  for (const t of next.foreman.tractors) {
    const view = tractors[t.id] || makeTractor(t);
    view.data = t;
    setImplement(view, t.mounted, catalog);
  }
  const occupied = new Set(next.foreman.tractors.filter((t) => t.status !== "traveling").map((t) => t.node));
  for (const [id, field] of Object.entries(fields)) field.tag.style.opacity = occupied.has(id) ? "0" : "1";   // the tractor's label names the field
  buildBarriers(next.farm.blocked);
  updateParked(next);
  if (lineWidth > 0) updateRoutes(next);

  updateHud(next);
  $("clock").textContent = next.clock || "--:--";
  $("plan-no").textContent = next.foreman.plan_no ? `Plan ${next.foreman.plan_no}` : "";
  $("run-state").textContent = next.finished ? "Day complete" : next.running ? `Running ${next.clock_speed ?? next.speed}×` : "Paused";
  $("headline").textContent = (next.foreman.explanation || {}).headline || "No plan yet.";
  $("status").textContent = "";
  $("status").className = "";
}

// Day summary and fleet list, straight from the state's totals and tractor rows.
let fleetKey = "";
function updateHud(next) {
  $("farm-name").textContent = next.farm.name ? `· ${next.farm.name}` : "";
  const totals = next.foreman.totals || {}, jobs = next.foreman.jobs.length;
  const kpis = [
    ["Jobs done", `${totals.jobs_done ?? 0} of ${jobs}`, false],
    ["On time", `${totals.on_time ?? 0}`, false],
    ["Working share", `${Math.round(totals.working_share ?? 0)}%`, false],
    ["Rule violations", `${totals.violations ?? 0}`, (totals.violations ?? 0) > 0],
  ];
  const kpiKey = JSON.stringify(kpis);
  if ($("kpis").dataset.key !== kpiKey) {
    $("kpis").dataset.key = kpiKey;
    $("kpis").replaceChildren(...kpis.map(([label, value, bad]) => {
      const item = document.createElement("span");
      if (bad) item.className = "bad";
      const strong = document.createElement("b");
      strong.textContent = value;
      item.append(label, strong);
      return item;
    }));
  }

  const jobById = Object.fromEntries(next.foreman.jobs.map((j) => [j.id, j]));
  const rows = next.foreman.tractors.map((t) => {
    const job = t.job ? jobById[t.job] : null, field = job && nodes[job.field] ? nodes[job.field].name || job.field : "";
    const what = t.status === "broken" ? "broken down" : job ? `${t.status}: ${job.type} ${field}` : t.status;
    return [t.id, t.name, t.color, what, t.fuel_pct, t.status, selectedId === t.id];
  });
  const key = JSON.stringify(rows);
  if (key === fleetKey) return;
  fleetKey = key;
  $("fleet-rows").replaceChildren(...rows.map(([id, name, color, what, fuel, status, on]) => {
    const row = document.createElement("button");
    row.className = "fleet-row" + (on ? " on" : "");
    row.onclick = () => follow(id);
    const dot = document.createElement("span"); dot.className = "dot"; dot.style.background = color;
    const label = document.createElement("span"); label.textContent = name;
    const state = document.createElement("span"); state.className = "what"; state.textContent = what;
    state.style.color = status === "broken" ? "var(--danger)" : "";
    const bar = document.createElement("span"); bar.className = "bar";
    const fill = document.createElement("span"); fill.style.width = `${clamp(fuel, 0, 100)}%`;
    fill.style.background = fuel < 25 ? "var(--danger)" : fuel < 60 ? "var(--accent)" : "var(--good)";
    bar.append(fill);
    row.append(dot, label, state, bar);
    return row;
  }));
}

// A true scale bar for the ground at the point the camera looks at.
const SCALE_STEPS = [10, 20, 50, 100, 200, 500, 1000, 2000, 5000];
function updateScaleBar(distance) {
  const metresPerPixel = (2 * distance * Math.tan(THREE.MathUtils.degToRad(camera.fov / 2)) / window.innerHeight) / M;
  const metres = SCALE_STEPS.find((step) => step / metresPerPixel >= 90) || 5000;
  const text = metres >= 1000 ? `${metres / 1000} km` : `${metres} m`;
  if ($("scalebar-text").textContent !== text) $("scalebar-text").textContent = text;
  $("scalebar-line").style.width = `${Math.round(metres / metresPerPixel)}px`;
}

function poll() {
  fetch("/api/state", { cache: "no-store" })
    .then((r) => { if (!r.ok) throw new Error(String(r.status)); return r.json(); })
    .then(applyState)
    .catch(() => { $("status").textContent = "Server not reachable"; $("status").className = "error"; });
}
poll();
setInterval(poll, POLL_MS);

// ---------------------------------------------------------------- per frame

function updateSun(dt) {
  if (!state) return;
  shownNow = shownNow === null ? state.now : shownNow + (state.now - shownNow) * ease(dt, 3);
  const hour = (dayStart + shownNow) / 60;
  // Already up in the east at 06:00, due south at noon, low in the west by evening.
  const elevation = THREE.MathUtils.degToRad(Math.max(6, 58 * Math.sin(Math.PI * (hour - 5.2) / 13.6)));
  const azimuth = Math.PI * (hour - 6) / 12;
  sunDir.set(Math.cos(elevation) * Math.cos(azimuth), Math.sin(elevation), Math.cos(elevation) * Math.sin(azimuth));
  skyUniforms.sunPosition.value.copy(sunDir);
  const high = clamp(Math.sin(elevation) / 0.55, 0, 1);       // 0 at the horizon, 1 by mid-morning
  skyUniforms.turbidity.value = 9 - 6.5 * high;
  skyUniforms.rayleigh.value = 2.6 - 1.3 * high;
  sun.color.setRGB(1, 0.72 + 0.26 * high, 0.5 + 0.46 * high);
  sun.intensity = 4.2 - 0.9 * high;          // a low sun grazes the ground, so it is driven harder
  hemi.intensity = 1.5 - 0.35 * high;
  scene.fog.color.setRGB(0.93 - 0.1 * high, 0.76 + 0.06 * high, 0.6 + 0.26 * high);   // haze matched to the horizon
  renderer.toneMappingExposure = 0.6 - 0.06 * high;
  state.dawn = high < 0.5;
  state.daylight = high;
}

function updateShadowFrustum(distance) {
  const half = clamp(distance * 0.75, 30, 520);
  const cam = sun.shadow.camera;
  if (Math.abs(cam.right - half) > half * 0.1) {
    cam.left = -half; cam.right = half; cam.top = half; cam.bottom = -half;
    cam.near = 10; cam.far = 3000;
    cam.updateProjectionMatrix();
  }
  sun.target.position.copy(controls.target);
  sun.position.copy(controls.target).addScaledVector(sunDir, 1400);
}

const tmp = new THREE.Vector3(), dummy = new THREE.Object3D();

function updateTractors(dt, iconScale, time) {
  if (!state) return;
  // Tractors standing at the same node report the same point. They are drawn side by side.
  const standing = {};
  for (const t of state.foreman.tractors) {
    if (t.status !== "traveling" && t.status !== "working") (standing[t.node] = standing[t.node] || []).push(t.id);
  }
  for (const view of Object.values(tractors)) {
    const t = view.data, job = t.job ? state.jobById[t.job] : null;
    let working = false;
    view.target.copy(P(t.x, t.y));
    view.targetHeading = THREE.MathUtils.degToRad(t.heading || 0);
    const group = standing[t.node] || [];
    if (t.status !== "traveling" && t.status !== "working" && view.workSpot && view.workSpot.node === t.node) {
      // It stopped in this field (broke down, or finished): it stays where it stopped.
      view.target.copy(view.workSpot.pos);
      view.targetHeading = view.workSpot.heading;
    } else if (group.length > 1 && group.includes(t.id)) {
      view.target.z += (group.indexOf(t.id) - (group.length - 1) / 2) * 0.75 * iconScale;
    }
    if (t.status === "traveling") view.workSpot = null;

    if (t.status === "working" && job && fields[job.field]) {
      // Inside the field: on the pass that the job's percent done puts it on.
      const field = fields[job.field], width = (view.spec || {}).width || FALLBACK_WIDTH[job.type] || 6;
      const st = passState(field, width, shown[job.id] ?? job.pct);
      const x = -field.side / 2 + st.x, z = -field.side / 2 + st.top + st.passWidth / 2;
      view.target.set(field.mesh.position.x + x, 0, field.mesh.position.z + z);
      view.targetHeading = st.east ? 0 : Math.PI;
      view.workSpot = { node: job.field, pos: view.target.clone(), heading: view.targetHeading };
      working = true;
    }
    if (!view.pos) { view.pos = view.target.clone(); view.heading = view.targetHeading; }
    const before = tmp.copy(view.pos);
    view.pos.lerp(view.target, ease(dt, working ? 5 : 7));
    let turn = view.targetHeading - view.heading;
    turn = Math.atan2(Math.sin(turn), Math.cos(turn));
    view.heading += turn * ease(dt, 6);
    view.group.position.copy(view.pos);
    view.group.rotation.y = view.heading;
    const rolled = before.distanceTo(view.pos) / M / iconScale;      // metres, as the drawn tractor sees them
    for (const w of view.wheels || []) w.rotation.z -= rolled / (w.name.startsWith("wheelR") ? 0.95 : 0.6);
    view.inner.scale.setScalar(M * iconScale);
    if (view.implement) view.implement.scale.setScalar(Math.min(iconScale, MAX_IMPLEMENT_SCALE) / iconScale);
    view.ring.visible = iconScale > 2.5;

    const broken = t.status === "broken";
    const blink = Math.floor(time * 2.6) % 2 === 0;
    for (const lamp of view.hazards) lamp.visible = broken && blink;
    view.warn.visible = broken;
    view.warn.rotation.y = time * 2;
    view.ring.material.color.set(broken ? "#ef3b30" : view.color);
    view.ring.material.opacity = broken ? (blink ? 0.95 : 0.35) : 0.75;
    const lights = !!state.dawn && !broken;
    for (const lamp of view.headlights) lamp.visible = lights;
    view.beam.visible = lights && (t.status === "traveling" || working);

    const jobLabel = job && nodes[job.field] ? `${job.type} ${nodes[job.field].name || job.field}` : t.status;
    const text = `${broken ? "⚠ " : ""}${t.name}|${broken ? "broken down" : jobLabel}`;
    if (view.tagText !== text) {
      view.tagText = text;
      view.tag.className = broken ? "tag broken" : "tag";
      view.tag.replaceChildren();
      const dot = document.createElement("span"); dot.className = "dot"; dot.style.background = view.color;
      const small = document.createElement("small"); small.textContent = broken ? "broken down" : jobLabel;
      view.tag.append(dot, document.createTextNode(`${broken ? "⚠ " : ""}${t.name}`), small);
    }

    // Dust behind a disc, mist behind a sprayer, only while it is really working and moving.
    const type = (view.spec || {}).type, moved = before.distanceTo(view.pos);
    // Off: the clock runs at 60 times real speed or more, so a dust trail turns into a row of blobs.
    const emit = DUST && working && (type === "till" || type === "spray") && moved > 1e-4;
    view.puffs.visible = emit || view.puffData.some((p) => p.life > 0);
    if (view.puffs.visible) {
      view.puffs.material.color.set(type === "spray" ? 0xdfeaf2 : 0xd3b990);
      view.puffTimer = (view.puffTimer || 0) + dt;
      if (emit && view.puffTimer > 0.09) {
        view.puffTimer = 0;
        const p = view.puffData[view.puffNext++ % view.puffData.length];
        const drawn = Math.min(iconScale, MAX_IMPLEMENT_SCALE);
        const back = (type === "spray" ? 5.6 : 4.6) * M * drawn, half = ((view.spec || {}).width || 4) * M * drawn / 2;
        p.p.set(view.pos.x - Math.cos(view.heading) * back + Math.sin(view.heading) * (Math.random() - 0.5) * 2 * half, 0.06 * drawn,
                view.pos.z + Math.sin(view.heading) * back + Math.cos(view.heading) * (Math.random() - 0.5) * 2 * half);
        p.life = 1; p.s = (type === "spray" ? 0.07 : 0.12) * drawn;
      }
      view.puffData.forEach((p, i) => {
        p.life = Math.max(0, p.life - dt * 0.8);
        dummy.position.copy(p.p); dummy.position.y += (1 - p.life) * 0.25 * Math.min(iconScale, MAX_IMPLEMENT_SCALE);
        dummy.scale.setScalar(p.life > 0 ? p.s * (1.2 + (1 - p.life) * 2.5) * p.life ** 0.4 : 0.0001);
        dummy.updateMatrix(); view.puffs.setMatrixAt(i, dummy.matrix);
      });
      view.puffs.instanceMatrix.needsUpdate = true;
    }
  }
}

function updateFields(dt, realDt) {
  if (!state) return;
  const byField = {};
  for (const job of state.foreman.jobs) {
    const target = job.pct_exact ?? job.pct;
    const now = shown[job.id];
    // Ease toward the reported percent so the tractor and the paint move together.
    shown[job.id] = now === undefined || target < now ? target : now + (target - now) * ease(dt, 2.2);
    if (Math.abs(target - shown[job.id]) < 0.02) shown[job.id] = target;
    (byField[job.field] = byField[job.field] || []).push(job);
  }
  for (const [id, field] of Object.entries(fields)) {
    paintField(field, byField[id] || [], state.catalog, state.planImplement);
    if (field.flashT > 0) {
      field.flashT = Math.max(0, field.flashT - realDt);
      field.flash.visible = field.flashT > 0;
      field.flash.material.opacity = 0.85 * Math.abs(Math.sin(field.flashT * Math.PI * 1.25)) * Math.min(1, field.flashT * 2);
    }
  }
}

function updateRouteAnimation(dt) {
  for (const r of routes) {
    if (r.fading > 0) {
      r.fading -= dt;
      r.mesh.material.opacity = clamp(r.fading / 1.4, 0, 1) * 0.9;
      if (r.fading <= 0) { routeLayer.remove(r.mesh); r.mesh.geometry.dispose(); r.gone = true; }
    } else if (r.drawn < 1) {
      r.drawn = Math.min(1, r.drawn + dt / 1.2);
      r.mesh.geometry.setDrawRange(0, Math.ceil(r.total / 6 * r.drawn) * 6);
    }
  }
  routes = routes.filter((r) => !r.gone);
}

function updateCard() {
  const view = selectedId ? tractors[selectedId] : null;
  $("card").style.display = view ? "block" : "none";
  if (!view || !state) return;
  const t = view.data, job = t.job ? state.jobById[t.job] : null;
  const rows = state.foreman.plan.filter((r) => r.tractor === t.id);
  const current = rows.find((r) => r.job === t.job);
  const pending = rows.filter((r) => (state.jobById[r.job] || {}).status !== "done").sort((a, b) => a.finish - b.finish);
  const last = pending[pending.length - 1];
  const implement = (state.farm.implements || []).find((i) => i.id === t.mounted);
  $("card-swatch").style.background = view.color;
  $("card-name").textContent = t.name;
  $("card-status").textContent = t.status;
  $("card-status").style.color = STATUS_COLOR[t.status] || "";
  $("card-job").textContent = job && nodes[job.field]
    ? `${job.type[0].toUpperCase()}${job.type.slice(1)} ${nodes[job.field].name || job.field} (${job.pct}%)` : "None";
  $("card-implement").textContent = implement ? `${implement.name}` : (t.mounted || "None");
  $("card-finish").textContent = current ? `This job ${clockText(current.finish)}` + (last && last !== current ? `, all work ${clockText(last.finish)}` : "")
    : last ? `All work ${clockText(last.finish)}` : "Nothing planned";
  $("card-fuel").textContent = `${t.fuel_pct}%`;
  $("card-fuel-bar").style.width = `${clamp(t.fuel_pct, 0, 100)}%`;
  $("card-fuel-bar").style.background = t.fuel_pct < 25 ? "var(--danger)" : t.fuel_pct < 60 ? "var(--accent)" : "var(--good)";
}

window.foremanView = { camera, controls, scene, overview, renderer, fps: 0 };
const fpsBox = new URLSearchParams(location.search).has("fps") ? document.createElement("div") : null;
if (fpsBox) {
  fpsBox.style.cssText = "position:fixed;top:14px;left:50%;transform:translateX(-50%);padding:4px 10px;border-radius:6px;"
    + "background:#111714eb;color:#fff;font:700 15px system-ui;";
  document.body.append(fpsBox);
}
let fpsFrames = 0, fpsSince = performance.now();
function countFrame() {
  fpsFrames++;
  const now = performance.now();
  if (now - fpsSince < 1000) return;
  window.foremanView.fps = Math.round(fpsFrames * 1000 / (now - fpsSince));
  if (document.visibilityState === "visible" && state && state.running) {
    // Kept so a reading taken while the page was in front can be read back later.
    window.foremanView.fpsInFront = { fps: window.foremanView.fps, at: new Date().toISOString(), clock: state.clock };
  }
  if (fpsBox) fpsBox.textContent = `${window.foremanView.fps} fps`;
  fpsFrames = 0;
  fpsSince = now;
}   // for inspection from the browser console

const timer = new THREE.Clock();
function frame() {
  const elapsed = timer.getDelta(), dt = Math.min(elapsed, 0.1), time = timer.elapsedTime;

  if (fly) {
    // Real elapsed time, so a camera move still finishes if the tab was in the background.
    fly.t = Math.min(1, fly.t + Math.min(elapsed, 3) / fly.seconds);
    const k = fly.t * fly.t * (3 - 2 * fly.t);
    if (followId && tractors[followId] && tractors[followId].pos) {    // the target keeps moving
      const delta = tmp.copy(tractors[followId].pos).setY(0.25).sub(fly.toT);
      fly.toT.add(delta); fly.toP.add(delta);
    }
    camera.position.lerpVectors(fly.fromP, fly.toP, k);
    controls.target.lerpVectors(fly.fromT, fly.toT, k);
    if (fly.t >= 1) fly = null;
  } else if (followId && tractors[followId] && tractors[followId].pos) {
    const delta = tmp.copy(tractors[followId].pos).setY(0.25).sub(controls.target);   // locked on: at one simulated minute per second a tractor covers about 150 m a second
    controls.target.add(delta);
    camera.position.add(delta);
  }
  controls.update();

  const distance = camera.position.distanceTo(controls.target);
  const near = clamp(distance * 0.02, 0.05, 4);
  if (Math.abs(camera.near - near) > near * 0.2) { camera.near = near; camera.updateProjectionMatrix(); }
  const iconScale = clamp(distance / ICON_DISTANCE, 1, MAX_ICON_SCALE);
  // Roads and route lines are widened with distance so they stay visible from the overview.
  const wanted = clamp(distance * 0.0042, 0.06, 4);
  if (state && (lineWidth === 0 || Math.abs(wanted - lineWidth) / lineWidth > 0.18)) {
    lineWidth = wanted;
    buildRoads(state.farm, Math.max(0.6, wanted * 1.9));   // a 6 m dirt road at true size
    updateRoutes(state);
  }
  for (const barrier of barrierGroup.children) barrier.scale.setScalar(clamp(distance / 260, 0.25, 3));
  if (yard) {
    const s = clamp(iconScale, 1, 5);
    yard.children[1].scale.setScalar(M * s);
    yard.userData.parked.scale.setScalar(M * s);
    yard.children[0].scale.setScalar(s);
  }

  updateSun(dt);
  updateEnvironment(time, state ? state.daylight ?? 0.5 : 0.5);
  updateShadowFrustum(distance);
  updateFields(dt, Math.min(elapsed, 3));
  updateTractors(dt, iconScale, time);
  updateRouteAnimation(Math.min(elapsed, 3));   // real time, so a fade still ends if the tab was in the background
  updateCard();
  updateScaleBar(distance);
  $("scale-note").textContent = iconScale < 1.05
    ? "Everything is at true size."
    : `Machines enlarged for visibility (tractors ${iconScale.toFixed(0)}×, implements ${Math.min(iconScale, MAX_IMPLEMENT_SCALE).toFixed(0)}×). Fields and passes are true size.`;

  renderer.render(scene, camera);
  labelRenderer.render(scene, camera);
  countFrame();
  requestAnimationFrame(frame);
}
requestAnimationFrame(frame);
