/*
 * Accuracy chart (owner: Vishva).
 *
 * Shows how far the planner's job-time estimates were from the real times,
 * day by day. With the learning loop (CLAUDE.md section 16) the error should
 * fall as the planner learns real field speeds.
 *
 * Data it reads from GET /api/state (proposed, to agree with Zayyan):
 *   state.foreman.learning = {
 *     "days": [
 *       {"day": 1, "error_pct": 21.5, "jobs": 10},
 *       {"day": 2, "error_pct": 12.0, "jobs": 10}
 *     ]
 *   }
 *   error_pct = mean over that day's finished jobs of
 *               |predicted work minutes - actual work minutes| / actual * 100
 * state.learning with the same shape is also accepted.
 * Until that data exists the chart says "Learning data appears after day 1".
 *
 * To use it, add one line to a page:
 *     <script src="panels/accuracy.js"></script>
 * It draws into <div id="accuracy-panel"></div> if the page has one,
 * otherwise it adds that div at the end of <body>.
 *
 * A page that already polls can set window.ACCURACY_PANEL_NO_POLL = true and
 * call window.AccuracyPanel.render(state). window.ACCURACY_PANEL_DEMO = true
 * shows labelled demo data (for the test page only).
 *
 * Plain JavaScript and Canvas, no build step, no CDN. Server text is only
 * ever written with textContent.
 */
(function () {
  "use strict";

  var POLL_MS = 2000;
  var HEIGHT = 240;
  var COLORS = {
    line: "#f0ae4d", surface: "#19221e", grid: "#2b3a33", axis: "#9fb0a5", text: "#edf5ef"
  };
  var DEMO = {
    days: [
      { day: 1, error_pct: 21.5, jobs: 10 }, { day: 2, error_pct: 12.8, jobs: 10 },
      { day: 3, error_pct: 7.9, jobs: 10 }, { day: 4, error_pct: 5.2, jobs: 10 },
      { day: 5, error_pct: 4.1, jobs: 10 }
    ]
  };

  var CSS = [
    ".ap { font-family: system-ui, -apple-system, 'Segoe UI', sans-serif; color: #edf5ef;",
    "  background: #19221e; border: 1px solid #34453c; border-radius: 8px; padding: 14px 16px;",
    "  margin: 12px 0; box-sizing: border-box; position: relative; }",
    ".ap-title { font-size: 13px; font-weight: 700; text-transform: uppercase; letter-spacing: .05em;",
    "  color: #9fb0a5; margin: 0 0 6px; }",
    ".ap-hero { font-size: clamp(18px, 2.4vw, 26px); font-weight: 700; margin: 0 0 2px; }",
    ".ap-sub { font-size: 13px; color: #9fb0a5; margin: 0 0 10px; }",
    ".ap-demo { display: inline-block; font-size: 11px; font-weight: 700; color: #111714;",
    "  background: #f0ae4d; border-radius: 4px; padding: 1px 6px; margin-left: 8px; vertical-align: middle; }",
    ".ap-canvas-wrap { position: relative; }",
    ".ap canvas { display: block; width: 100%; }",
    ".ap-empty { height: 160px; display: flex; align-items: center; justify-content: center;",
    "  text-align: center; color: #9fb0a5; font-size: 16px; border: 1px dashed #34453c; border-radius: 6px; }",
    ".ap-tip { position: absolute; pointer-events: none; background: #202c26; border: 1px solid #34453c;",
    "  border-radius: 6px; padding: 6px 9px; font-size: 13px; white-space: nowrap; display: none;",
    "  box-shadow: 0 4px 12px #0006; }",
    ".ap-tip b { display: block; font-size: 15px; }",
    ".ap details { margin-top: 8px; font-size: 13px; color: #9fb0a5; }",
    ".ap table { border-collapse: collapse; margin-top: 6px; }",
    ".ap th, .ap td { padding: 3px 12px 3px 0; text-align: right; font-variant-numeric: tabular-nums; }",
    ".ap th { color: #9fb0a5; font-weight: 600; }",
    ".ap td { color: #edf5ef; }"
  ].join("\n");

  var els = null;
  var days = [];        // the points on screen
  var points = [];      // their canvas positions, for hover
  var lastKey = null;

  function el(tag, className, text) {
    var node = document.createElement(tag);
    if (className) node.className = className;
    if (text != null) node.textContent = text;
    return node;
  }

  function build() {
    var style = el("style");
    style.textContent = CSS;
    document.head.appendChild(style);

    var root = document.getElementById("accuracy-panel");
    if (!root) {
      root = el("div");
      root.id = "accuracy-panel";
      document.body.appendChild(root);
    }
    root.textContent = "";

    var panel = el("section", "ap");
    var title = el("h2", "ap-title", "Estimate error by day");
    var hero = el("p", "ap-hero", "");
    var sub = el("p", "ap-sub",
      "How far predicted job times were from real ones. Lower is better. " +
      "Shown in simulation, where hidden real field speeds differ from the catalog.");
    var wrap = el("div", "ap-canvas-wrap");
    var canvas = el("canvas");
    canvas.setAttribute("role", "img");
    var empty = el("div", "ap-empty", "Learning data appears after day 1");
    var tip = el("div", "ap-tip");
    wrap.appendChild(canvas);
    wrap.appendChild(empty);
    wrap.appendChild(tip);

    var details = el("details");
    details.appendChild(el("summary", null, "Show as table"));
    var table = el("table");
    details.appendChild(table);

    panel.appendChild(title);
    panel.appendChild(hero);
    panel.appendChild(sub);
    panel.appendChild(wrap);
    panel.appendChild(details);
    root.appendChild(panel);

    els = { title: title, hero: hero, wrap: wrap, canvas: canvas, empty: empty, tip: tip,
            table: table, details: details };

    canvas.addEventListener("mousemove", onHover);
    canvas.addEventListener("mouseleave", function () { tip.style.display = "none"; draw(null); });
    if (window.ResizeObserver) {
      new ResizeObserver(function () { draw(null); }).observe(wrap);
    } else {
      window.addEventListener("resize", function () { draw(null); });
    }
  }

  function learningFrom(state) {
    if (window.ACCURACY_PANEL_DEMO) return DEMO;
    if (!state) return null;
    return (state.foreman && state.foreman.learning) || state.learning || null;
  }

  function cleanDays(learning) {
    var list = (learning && Array.isArray(learning.days)) ? learning.days : [];
    return list
      .filter(function (d) { return d && isFinite(d.day) && isFinite(d.error_pct); })
      .map(function (d) { return { day: +d.day, error_pct: +d.error_pct, jobs: d.jobs }; })
      .sort(function (a, b) { return a.day - b.day; });
  }

  function pct(value) {
    return (Math.round(value * 10) / 10).toFixed(1) + "%";
  }

  function render(state) {
    if (!els) build();
    var list = cleanDays(learningFrom(state));
    var key = JSON.stringify(list) + (window.ACCURACY_PANEL_DEMO ? "demo" : "");
    if (key === lastKey) return;
    lastKey = key;
    days = list;

    // Title, with a clear label when the data is not real.
    els.title.textContent = "Estimate error by day";
    if (window.ACCURACY_PANEL_DEMO) els.title.appendChild(el("span", "ap-demo", "DEMO DATA"));

    if (!days.length) {
      els.hero.textContent = "";
      els.canvas.style.display = "none";
      els.empty.style.display = "flex";
      els.details.style.display = "none";
      return;
    }
    els.canvas.style.display = "block";
    els.empty.style.display = "none";
    els.details.style.display = "block";

    var first = days[0];
    var last = days[days.length - 1];
    if (days.length === 1) {
      els.hero.textContent = "Day " + first.day + ": estimates were off by " + pct(first.error_pct) +
        ". The trend appears after day 2.";
    } else {
      var change = last.error_pct < first.error_pct ? "down from " : "up from ";
      els.hero.textContent = "Estimate error " + pct(last.error_pct) + " on day " + last.day +
        ", " + change + pct(first.error_pct) + " on day " + first.day;
    }
    els.canvas.setAttribute("aria-label", "Line chart. " + days.map(function (d) {
      return "Day " + d.day + ": " + pct(d.error_pct);
    }).join(", "));
    fillTable();
    draw(null);
  }

  function fillTable() {
    var table = els.table;
    table.textContent = "";
    var head = el("tr");
    ["Day", "Estimate error", "Jobs measured"].forEach(function (h) { head.appendChild(el("th", null, h)); });
    table.appendChild(head);
    days.forEach(function (d) {
      var row = el("tr");
      row.appendChild(el("td", null, String(d.day)));
      row.appendChild(el("td", null, pct(d.error_pct)));
      row.appendChild(el("td", null, d.jobs == null ? "-" : String(d.jobs)));
      table.appendChild(row);
    });
  }

  // A round tick step (1, 2, 5 or 10 times a power of ten) giving at most 5 gridlines.
  function niceStep(maxValue) {
    var raw = Math.max(maxValue, 1) / 4;
    var power = Math.pow(10, Math.floor(Math.log10(raw)));
    var options = [1, 2, 5, 10];
    for (var i = 0; i < options.length; i++) {
      if (options[i] * power >= raw) return options[i] * power;
    }
    return 10 * power;
  }

  function draw(hoverIndex) {
    if (!els || !days.length) return;
    var canvas = els.canvas;
    var width = els.wrap.clientWidth || 600;
    var ratio = window.devicePixelRatio || 1;
    canvas.width = Math.round(width * ratio);
    canvas.height = Math.round(HEIGHT * ratio);
    canvas.style.height = HEIGHT + "px";
    var ctx = canvas.getContext("2d");
    ctx.setTransform(ratio, 0, 0, ratio, 0, 0);
    ctx.clearRect(0, 0, width, HEIGHT);

    var pad = { left: 44, right: 24, top: 24, bottom: 30 };
    var plotW = Math.max(10, width - pad.left - pad.right);
    var plotH = HEIGHT - pad.top - pad.bottom;
    var highest = Math.max.apply(null, days.map(function (d) { return d.error_pct; }));
    var step = niceStep(highest * 1.1);
    var lines = Math.max(1, Math.ceil(highest * 1.1 / step));
    var top = step * lines;
    var x = function (i) { return pad.left + (days.length === 1 ? plotW / 2 : plotW * i / (days.length - 1)); };
    var y = function (v) { return pad.top + plotH * (1 - v / top); };

    // Grid and y labels: recessive.
    ctx.font = "12px system-ui, -apple-system, 'Segoe UI', sans-serif";
    ctx.textBaseline = "middle";
    ctx.textAlign = "right";
    for (var g = 0; g <= lines; g++) {
      var value = step * g;
      var gy = y(value);
      ctx.strokeStyle = COLORS.grid;
      ctx.lineWidth = 1;
      ctx.beginPath();
      ctx.moveTo(pad.left, Math.round(gy) + 0.5);
      ctx.lineTo(pad.left + plotW, Math.round(gy) + 0.5);
      ctx.stroke();
      ctx.fillStyle = COLORS.axis;
      ctx.fillText((step < 1 ? value.toFixed(1) : Math.round(value)) + "%", pad.left - 8, gy);
    }

    // X labels: every day while they fit, otherwise every few days.
    ctx.textAlign = "center";
    ctx.textBaseline = "top";
    var every = Math.max(1, Math.ceil(days.length / Math.max(1, Math.floor(plotW / 56))));
    days.forEach(function (d, i) {
      if (i % every === 0 || i === days.length - 1) {
        ctx.fillStyle = COLORS.axis;
        ctx.fillText("Day " + d.day, x(i), HEIGHT - pad.bottom + 8);
      }
    });

    // Crosshair under the line.
    if (hoverIndex != null) {
      ctx.strokeStyle = COLORS.axis;
      ctx.lineWidth = 1;
      ctx.beginPath();
      ctx.moveTo(Math.round(x(hoverIndex)) + 0.5, pad.top);
      ctx.lineTo(Math.round(x(hoverIndex)) + 0.5, pad.top + plotH);
      ctx.stroke();
    }

    // The line: 2px, round joins.
    points = days.map(function (d, i) { return { x: x(i), y: y(d.error_pct) }; });
    ctx.strokeStyle = COLORS.line;
    ctx.lineWidth = 2;
    ctx.lineJoin = "round";
    ctx.lineCap = "round";
    ctx.beginPath();
    points.forEach(function (p, i) { if (i === 0) ctx.moveTo(p.x, p.y); else ctx.lineTo(p.x, p.y); });
    ctx.stroke();

    // Markers with a 2px surface ring.
    points.forEach(function (p, i) {
      var r = i === hoverIndex ? 6 : 4.5;
      ctx.fillStyle = COLORS.surface;
      ctx.beginPath();
      ctx.arc(p.x, p.y, r + 2, 0, Math.PI * 2);
      ctx.fill();
      ctx.fillStyle = COLORS.line;
      ctx.beginPath();
      ctx.arc(p.x, p.y, r, 0, Math.PI * 2);
      ctx.fill();
    });

    // Direct labels on the first and last point only.
    ctx.fillStyle = COLORS.text;
    ctx.font = "600 13px system-ui, -apple-system, 'Segoe UI', sans-serif";
    ctx.textBaseline = "bottom";
    var label = function (i, align) {
      ctx.textAlign = align;
      ctx.fillText(pct(days[i].error_pct), points[i].x + (align === "left" ? 6 : align === "right" ? -6 : 0),
                   points[i].y - 9);
    };
    if (days.length === 1) {
      label(0, "center");
    } else {
      label(0, "left");
      label(days.length - 1, "right");
    }
  }

  function onHover(event) {
    if (!points.length) return;
    var box = els.canvas.getBoundingClientRect();
    var mx = event.clientX - box.left;
    var best = 0;
    points.forEach(function (p, i) {
      if (Math.abs(p.x - mx) < Math.abs(points[best].x - mx)) best = i;
    });
    draw(best);
    var d = days[best];
    var tip = els.tip;
    tip.textContent = "";
    tip.appendChild(el("b", null, pct(d.error_pct) + " error"));
    tip.appendChild(document.createTextNode("Day " + d.day + (d.jobs == null ? "" : ", " + d.jobs + " jobs measured")));
    tip.style.display = "block";
    var left = points[best].x + 12;
    if (left + tip.offsetWidth > box.width) left = points[best].x - tip.offsetWidth - 12;
    tip.style.left = left + "px";
    tip.style.top = Math.max(0, points[best].y - tip.offsetHeight - 10) + "px";
  }

  function poll() {
    fetch("/api/state")
      .then(function (response) {
        if (!response.ok) throw new Error("HTTP " + response.status);
        return response.json();
      })
      .then(render)
      .catch(function () { if (!els) build(); });
  }

  function start() {
    build();
    render(null);
    if (!window.ACCURACY_PANEL_NO_POLL && !window.ACCURACY_PANEL_DEMO) {
      poll();
      setInterval(poll, POLL_MS);
    }
  }

  window.AccuracyPanel = { render: render };

  if (document.readyState === "loading") {
    document.addEventListener("DOMContentLoaded", start);
  } else {
    start();
  }
})();
