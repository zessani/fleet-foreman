// Close-up crop tiles: one seamless top-down canvas per crop, rows along x.
// Light comes from the upper left, so shadows fall to the lower right.

const TAU = Math.PI * 2;

// Small seeded generator (mulberry32).
function makeRng(seed) {
  let a = seed >>> 0;
  return () => {
    a = (a + 0x6d2b79f5) >>> 0;
    let t = a;
    t = Math.imul(t ^ (t >>> 15), t | 1);
    t ^= t + Math.imul(t ^ (t >>> 7), t | 61);
    return ((t ^ (t >>> 14)) >>> 0) / 4294967296;
  };
}

function seedOf(name) {
  let h = 2166136261;
  for (let i = 0; i < name.length; i++) h = Math.imul(h ^ name.charCodeAt(i), 16777619);
  return h >>> 0;
}

const clamp = (v) => Math.max(0, Math.min(255, Math.round(v)));
const rgba = (c, a = 1, d = 0) =>
  `rgba(${clamp(c[0] + d)},${clamp(c[1] + d)},${clamp(c[2] + d)},${a})`;

const META = { lettuce: 6, citrus: 12, alfalfa: 4, cotton: 6, generic: 6 };

export function cropTile(crop, size) {
  const kind = META[crop] ? crop : "generic";
  const metres = META[kind];
  const canvas = document.createElement("canvas");
  canvas.width = size;
  canvas.height = size;
  const ctx = canvas.getContext("2d");
  const rnd = makeRng(seedOf(kind));
  const pm = size / metres; // pixels per metre
  const rr = (a, b) => a + (b - a) * rnd();

  // Run fn at (x, y) and at every wrapped copy that touches the tile.
  const wrap = (x, y, r, fn) => {
    for (const ox of [-size, 0, size]) {
      for (const oy of [-size, 0, size]) {
        const px = x + ox;
        const py = y + oy;
        if (px + r > 0 && px - r < size && py + r > 0 && py - r < size) fn(px, py);
      }
    }
  };
  const blob = (x, y, rx, ry, rot, fill) => {
    wrap(x, y, Math.max(rx, ry), (px, py) => {
      ctx.fillStyle = fill;
      ctx.beginPath();
      ctx.ellipse(px, py, rx, ry, rot, 0, TAU);
      ctx.fill();
    });
  };
  const soil = (base, flecks, strength) => {
    ctx.fillStyle = rgba(base);
    ctx.fillRect(0, 0, size, size);
    for (let i = 0; i < flecks; i++) {
      const d = rr(-strength, strength);
      ctx.fillStyle = rgba(base, rr(0.25, 0.7), d);
      const s = rr(0.6, 2.2) * (size / 512);
      ctx.fillRect(rnd() * size, rnd() * size, s * rr(1, 1.8), s);
    }
  };

  if (kind === "lettuce") {
    // Raised beds 1 m apart, two plant lines each, rosette heads.
    const bed = pm;
    const furrow = [78, 58, 42];
    soil(furrow, 3500, 12);
    for (let i = 0; i < 6; i++) {
      const cy = (i + 0.5) * bed;
      const g = ctx.createLinearGradient(0, cy - 0.38 * pm, 0, cy + 0.38 * pm);
      g.addColorStop(0, "rgba(130,100,72,0)");
      g.addColorStop(0.2, "rgba(132,102,74,0.85)");
      g.addColorStop(0.8, "rgba(126,97,70,0.85)");
      g.addColorStop(1, "rgba(100,76,54,0)");
      ctx.fillStyle = g;
      ctx.fillRect(0, cy - 0.38 * pm, size, 0.76 * pm);
    }
    for (let i = 0; i < 700; i++) {
      ctx.fillStyle = rgba([150, 118, 86], rr(0.2, 0.5), rr(-18, 18));
      ctx.fillRect(rnd() * size, ((Math.floor(rnd() * 6) + 0.5) + rr(-0.3, 0.3)) * bed, 1.5, 1.5);
    }
    const step = 0.3 * pm;
    for (let i = 0; i < 6; i++) {
      for (const off of [-0.17, 0.17]) {
        const cy = (i + 0.5) * bed + off * pm;
        for (let k = 0; k < 20; k++) {
          const x = (k + 0.5) * step + rr(-0.03, 0.03) * pm;
          const y = cy + rr(-0.03, 0.03) * pm;
          const r = rr(0.12, 0.16) * pm;
          const tone = rr(-10, 10);
          blob(x + r * 0.35, y + r * 0.4, r * 1.05, r, 0, "rgba(30,20,12,0.38)");
          const n = 7;
          const a0 = rnd() * TAU;
          for (let j = 0; j < n; j++) {
            const a = a0 + (j / n) * TAU + rr(-0.2, 0.2);
            const d = r * 0.5;
            blob(x + Math.cos(a) * d, y + Math.sin(a) * d, r * 0.55, r * 0.4, a, rgba([52, 100, 42], 1, tone));
          }
          for (let j = 0; j < 5; j++) {
            const a = a0 + (j / 5) * TAU + rr(-0.3, 0.3);
            const d = r * 0.3;
            blob(x + Math.cos(a) * d, y + Math.sin(a) * d, r * 0.42, r * 0.3, a, rgba([86, 138, 56], 1, tone));
          }
          blob(x - r * 0.08, y - r * 0.08, r * 0.3, r * 0.28, 0, rgba([156, 190, 96], 1, tone));
          blob(x - r * 0.14, y - r * 0.14, r * 0.12, r * 0.1, 0, rgba([196, 220, 140], 0.8, tone));
        }
      }
    }
  } else if (kind === "citrus") {
    // Four trees on a 6 m grid over pale orchard floor with wheel tracks.
    soil([176, 148, 108], 7000, 14);
    for (let i = 0; i < 2; i++) {
      for (const off of [-0.8, 0.8]) {
        ctx.fillStyle = "rgba(120,94,64,0.28)";
        ctx.fillRect(0, (i * 6 + off) * pm - 0.2 * pm + 0, size, 0.4 * pm);
        ctx.fillStyle = "rgba(210,184,142,0.12)";
        ctx.fillRect(0, (i * 6 + off) * pm - 0.04 * pm, size, 0.08 * pm);
      }
    }
    const trees = [];
    for (let i = 0; i < 2; i++) {
      for (let j = 0; j < 2; j++) {
        trees.push({
          x: (j * 6 + 3 + rr(-0.4, 0.4)) * pm,
          y: (i * 6 + 3 + rr(-0.4, 0.4)) * pm,
          r: rr(1.8, 2.2) * pm,
        });
      }
    }
    for (const t of trees) {
      // Lobed outline from noisy polar radius.
      const lobes = [];
      const ph = [rnd() * TAU, rnd() * TAU, rnd() * TAU];
      for (let k = 0; k < 40; k++) {
        const a = (k / 40) * TAU;
        const f = 1 + 0.1 * Math.sin(3 * a + ph[0]) + 0.07 * Math.sin(5 * a + ph[1]) + 0.05 * Math.sin(8 * a + ph[2]);
        lobes.push([a, f * t.r]);
      }
      const outline = (cx, cy, scale) => {
        ctx.beginPath();
        lobes.forEach(([a, d], k) => {
          const px = cx + Math.cos(a) * d * scale;
          const py = cy + Math.sin(a) * d * scale;
          if (k === 0) ctx.moveTo(px, py);
          else ctx.lineTo(px, py);
        });
        ctx.closePath();
      };
      const reach = t.r * 2.2;
      wrap(t.x, t.y, reach, (px, py) => {
        ctx.fillStyle = "rgba(40,30,18,0.32)";
        outline(px + t.r * 0.5, py + t.r * 0.45, 1.02);
        ctx.fill();
        ctx.fillStyle = rgba([34, 66, 34]);
        outline(px, py, 1);
        ctx.fill();
      });
      // Foliage clumps: lighter upper left, darker lower right.
      for (let k = 0; k < 150; k++) {
        const a = rnd() * TAU;
        const d = Math.sqrt(rnd()) * t.r * 0.9;
        const cx = t.x + Math.cos(a) * d;
        const cy = t.y + Math.sin(a) * d;
        const light = (-(Math.cos(a) + Math.sin(a)) * d) / t.r; // -1..1, up-left positive
        const base = [46 + light * 18, 88 + light * 28, 42 + light * 10];
        blob(cx, cy, rr(0.14, 0.34) * pm, rr(0.12, 0.28) * pm, rnd() * TAU, rgba(base, 0.75, rr(-8, 8)));
      }
      for (let k = 0; k < 60; k++) {
        const a = rr(Math.PI * 0.75, Math.PI * 1.75);
        const d = Math.sqrt(rnd()) * t.r * 0.8;
        blob(t.x + Math.cos(a) * d, t.y + Math.sin(a) * d, rr(0.05, 0.1) * pm, rr(0.04, 0.08) * pm, rnd() * TAU, "rgba(120,160,70,0.6)");
      }
      for (let k = 0; k < 9; k++) {
        const a = rnd() * TAU;
        const d = Math.sqrt(rnd()) * t.r * 0.75;
        blob(t.x + Math.cos(a) * d, t.y + Math.sin(a) * d, 0.06 * pm, 0.06 * pm, 0, rgba([220, 128, 36]));
      }
    }
  } else if (kind === "alfalfa") {
    // Dense even stand with faint drill rows and yellower patches.
    ctx.fillStyle = rgba([70, 118, 48]);
    ctx.fillRect(0, 0, size, size);
    const rows = 20;
    ctx.fillStyle = "rgba(36,70,28,0.16)";
    for (let i = 0; i < rows; i++) ctx.fillRect(0, (i + 0.5) * (size / rows) - 0.8, size, 1.6);
    for (let i = 0; i < 90; i++) {
      const x = rnd() * size;
      const y = rnd() * size;
      const r = rr(0.12, 0.3) * pm;
      wrap(x, y, r, (px, py) => {
        const g = ctx.createRadialGradient(px, py, 0, px, py, r);
        const dark = rnd() < 0.5;
        g.addColorStop(0, dark ? "rgba(44,84,34,0.4)" : "rgba(104,150,66,0.4)");
        g.addColorStop(1, "rgba(70,118,48,0)");
        ctx.fillStyle = g;
        ctx.beginPath();
        ctx.arc(px, py, r, 0, TAU);
        ctx.fill();
      });
    }
    for (let i = 0; i < 5; i++) {
      const x = rnd() * size;
      const y = rnd() * size;
      const r = rr(0.35, 0.6) * pm;
      wrap(x, y, r, (px, py) => {
        const g = ctx.createRadialGradient(px, py, 0, px, py, r);
        g.addColorStop(0, "rgba(138,152,56,0.4)");
        g.addColorStop(1, "rgba(138,152,56,0)");
        ctx.fillStyle = g;
        ctx.beginPath();
        ctx.arc(px, py, r, 0, TAU);
        ctx.fill();
      });
    }
    const leaf = Math.max(1, size / 256);
    for (let i = 0; i < 5500; i++) {
      const x = rnd() * size;
      const y = rnd() * size;
      const t = rnd();
      const c = t < 0.5 ? [52, 100, 38] : t < 0.85 ? [84, 134, 54] : [118, 160, 72];
      blob(x, y, leaf * rr(1, 2.2), leaf * rr(0.6, 1.1), rnd() * TAU, rgba(c, rr(0.6, 0.95), rr(-8, 8)));
    }
    for (let i = 0; i < 40; i++) {
      ctx.fillStyle = rgba([120, 92, 62], 0.6);
      ctx.fillRect(rnd() * size, rnd() * size, 1.2, 1.2);
    }
  } else if (kind === "cotton") {
    // Bushy dark rows with white bolls, dry soil between.
    soil([158, 124, 90], 6000, 14);
    const bandH = 0.6 * pm;
    for (let i = 0; i < 6; i++) {
      const cy = (i + 0.5) * pm;
      ctx.fillStyle = "rgba(40,28,16,0.32)";
      ctx.fillRect(0, cy + 0.16 * pm, size, 0.34 * pm);
      ctx.fillStyle = "rgba(40,28,16,0.18)";
      ctx.fillRect(0, cy + 0.3 * pm, size, 0.16 * pm);
      // Lumpy continuous band from overlapping blobs.
      const n = 52;
      for (let k = 0; k < n; k++) {
        const x = (k / n) * size + rr(-0.5, 0.5) * (size / n);
        const y = cy + rr(-0.12, 0.12) * pm;
        const rx = rr(0.12, 0.22) * pm;
        const ry = rr(0.2, 0.3) * pm;
        blob(x + 2, y + 3, rx, ry, 0, "rgba(24,38,18,0.45)");
        blob(x, y, rx, ry, rr(-0.4, 0.4), rgba([58, 80, 40], 1, rr(-8, 8)));
      }
      for (let k = 0; k < 170; k++) {
        const x = rnd() * size;
        const y = cy + rr(-0.27, 0.27) * pm;
        const dark = rnd() < 0.45;
        blob(x, y, rr(0.04, 0.1) * pm, rr(0.03, 0.08) * pm, rnd() * TAU, dark ? "rgba(40,58,30,0.8)" : rgba([86, 112, 56], 0.7, rr(-8, 8)));
      }
      for (let k = 0; k < 85; k++) {
        const x = rnd() * size;
        const y = cy + rr(-0.24, 0.24) * pm;
        const m = 2 + Math.floor(rnd() * 3);
        for (let j = 0; j < m; j++) {
          const bx = x + rr(-0.03, 0.03) * pm;
          const by = y + rr(-0.03, 0.03) * pm;
          const s = rr(0.014, 0.03) * pm + 0.5;
          blob(bx + 0.8, by + 0.8, s, s * 0.8, 0, "rgba(30,24,16,0.4)");
          blob(bx, by, s, s * rr(0.7, 1), rnd() * TAU, rgba([236, 232, 220], 1, rr(-14, 6)));
        }
      }
    }
  } else {
    // Generic young row crop (sorghum-like): strappy blades on tan soil.
    soil([164, 134, 98], 5000, 13);
    const rows = 8;
    const rowGap = size / rows;
    for (let i = 0; i < rows; i++) {
      const cy = (i + 0.5) * rowGap;
      ctx.fillStyle = "rgba(60,44,28,0.16)";
      ctx.fillRect(0, cy + 0.04 * pm, size, 0.2 * pm);
      for (let k = 0; k < 24; k++) {
        const x = (k + 0.5) * (size / 24) + rr(-0.03, 0.03) * pm;
        const y = cy + rr(-0.03, 0.03) * pm;
        const tone = rr(-10, 10);
        const blades = 6 + Math.floor(rnd() * 3);
        const a0 = rnd() * TAU;
        const len = rr(0.1, 0.16) * pm;
        wrap(x, y, len * 1.5, (px, py) => {
          ctx.lineCap = "round";
          ctx.strokeStyle = "rgba(34,26,16,0.35)";
          ctx.lineWidth = Math.max(1, 0.03 * pm);
          for (let j = 0; j < blades; j++) {
            const a = a0 + (j / blades) * TAU;
            ctx.beginPath();
            ctx.moveTo(px + 1.5, py + 1.5);
            ctx.lineTo(px + 1.5 + Math.cos(a) * len, py + 1.5 + Math.sin(a) * len);
            ctx.stroke();
          }
          for (let j = 0; j < blades; j++) {
            const a = a0 + (j / blades) * TAU + 0.2;
            ctx.strokeStyle = rgba(j % 2 ? [92, 132, 56] : [66, 108, 44], 1, tone);
            ctx.lineWidth = Math.max(1, 0.026 * pm);
            ctx.beginPath();
            ctx.moveTo(px, py);
            ctx.quadraticCurveTo(px + Math.cos(a - 0.4) * len * 0.6, py + Math.sin(a - 0.4) * len * 0.6, px + Math.cos(a) * len, py + Math.sin(a) * len);
            ctx.stroke();
          }
        });
        blob(x, y, 0.025 * pm, 0.025 * pm, 0, rgba([130, 164, 84], 0.9, tone));
      }
    }
  }

  // Average colour from the finished pixels.
  const data = ctx.getImageData(0, 0, size, size).data;
  let r = 0;
  let g = 0;
  let b = 0;
  const n = size * size;
  for (let i = 0; i < n; i++) {
    r += data[i * 4];
    g += data[i * 4 + 1];
    b += data[i * 4 + 2];
  }
  return { canvas, metres, average: [r / n, g / n, b / n] };
}
