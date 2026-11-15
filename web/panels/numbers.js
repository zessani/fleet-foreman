/*
 * Numbers panel (owner: Vishva).
 *
 * Shows Foreman next to "first come, first served" using the totals from
 * GET /api/state (CLAUDE.md section 8c). The better side of each number is
 * green, with the improvement next to it.
 *
 * To use it, add one line to a page:
 *     <script src="panels/numbers.js"></script>
 * It draws into <div id="numbers-panel"></div> if the page has one,
 * otherwise it adds that div at the end of <body>.
 *
 * It polls /api/state by itself. A page that already polls can set
 * window.NUMBERS_PANEL_NO_POLL = true before loading this file and call
 * window.NumbersPanel.render(state) instead.
 *
 * Plain JavaScript, no build step, no CDN.
 */
(function () {
  "use strict";

  var POLL_MS = 500;

  // better: "high" means a bigger number wins, "low" means a smaller one wins.
  var METRICS = [
    { key: "jobs_done",     label: "Jobs done",           better: "high", unit: "" },
    { key: "on_time",       label: "Jobs on time",        better: "high", unit: "" },
    { key: "working_share", label: "Working share of day", better: "high", unit: "%", points: true },
    { key: "travel_min",    label: "Travel time",         better: "low",  unit: " min" },
    { key: "idle_min",      label: "Idle time",           better: "low",  unit: " min" },
    { key: "km",            label: "Distance driven",     better: "low",  unit: " km" },
    { key: "violations",    label: "Rule violations",     better: "low",  unit: "" }
  ];

  // Colors match the dashboard's dark theme (web/index.html :root tokens).
  var CSS = [
    ".np { font-family: system-ui, -apple-system, 'Segoe UI', Roboto, sans-serif;",
    "  color: #edf5ef; background: #19221e; border: 1px solid #34453c; border-radius: 8px;",
    "  padding: 14px 16px; margin: 12px 0; max-width: 1100px; box-sizing: border-box; }",
    ".np-title { font-size: 13px; font-weight: 700; text-transform: uppercase; letter-spacing: .05em;",
    "  color: #9fb0a5; margin: 0 0 10px; }",
    ".np-grid { display: grid; grid-template-columns: minmax(0, 1.4fr) repeat(3, minmax(0, 1fr));",
    "  gap: 6px; align-items: stretch; }",
    ".np-head { font-size: clamp(12px, 1.5vw, 16px); font-weight: 700; color: #9fb0a5;",
    "  text-transform: uppercase; letter-spacing: 0.04em; padding: 4px 8px; }",
    ".np-head.np-num { text-align: right; }",
    ".np-label { font-size: clamp(14px, 2vw, 24px); font-weight: 600; padding: 10px 8px;",
    "  display: flex; align-items: center; border-top: 1px solid #2b3a33; }",
    ".np-cell { font-size: clamp(22px, 3.6vw, 44px); font-weight: 700; padding: 6px 12px;",
    "  text-align: right; border-radius: 6px; font-variant-numeric: tabular-nums;",
    "  display: flex; align-items: center; justify-content: flex-end; }",
    ".np-win { background: #223d2c; color: #7fd99a; box-shadow: inset 0 0 0 1px #67c58755; }",
    ".np-gain { font-size: clamp(16px, 2.4vw, 30px); font-weight: 700; padding: 6px 12px;",
    "  text-align: right; display: flex; align-items: center; justify-content: flex-end;",
    "  font-variant-numeric: tabular-nums; color: #9fb0a5; }",
    ".np-gain.np-good { color: #67c587; }",
    ".np-gain.np-bad { color: #ef6d68; }",
    ".np-status { font-size: 13px; color: #9fb0a5; margin-top: 10px; min-height: 1em; }",
    ".np-status.np-error { color: #ef6d68; font-weight: 700; }"
  ].join("\n");

  var cells = {};   // cells[key] = {foreman, baseline, gain}
  var statusEl = null;

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

    var root = document.getElementById("numbers-panel");
    if (!root) {
      root = el("div");
      root.id = "numbers-panel";
      document.body.appendChild(root);
    }
    root.textContent = "";

    var panel = el("section", "np");
    panel.appendChild(el("h2", "np-title", "Foreman vs first come, first served"));

    var grid = el("div", "np-grid");
    grid.appendChild(el("div", "np-head", ""));
    grid.appendChild(el("div", "np-head np-num", "Foreman"));
    grid.appendChild(el("div", "np-head np-num", "First come"));
    grid.appendChild(el("div", "np-head np-num", "Gain"));

    METRICS.forEach(function (metric) {
      var hint = metric.better === "high" ? " (higher is better)" : " (lower is better)";
      var label = el("div", "np-label", metric.label);
      label.title = metric.label + hint;
      var row = {
        foreman: el("div", "np-cell", "-"),
        baseline: el("div", "np-cell", "-"),
        gain: el("div", "np-gain", "")
      };
      grid.appendChild(label);
      grid.appendChild(row.foreman);
      grid.appendChild(row.baseline);
      grid.appendChild(row.gain);
      cells[metric.key] = row;
    });

    panel.appendChild(grid);
    statusEl = el("div", "np-status", "Waiting for data...");
    panel.appendChild(statusEl);
    root.appendChild(panel);
  }

  function format(value, metric) {
    if (value == null || isNaN(value)) return "-";
    var whole = Math.abs(value - Math.round(value)) < 1e-9;
    return (whole ? String(Math.round(value)) : value.toFixed(1)) + metric.unit;
  }

  // Works out which side wins and the text for the Gain column.
  // Returns {winner: "foreman" | "baseline" | "tie" | "both" | null, text, good}
  function compare(metric, f, b) {
    if (f == null || b == null || isNaN(f) || isNaN(b)) {
      return { winner: null, text: "", good: null };
    }
    if (f === b) {
      if (metric.key === "violations" && f === 0) {
        return { winner: "both", text: "Both zero", good: true };
      }
      return { winner: "tie", text: "Same", good: null };
    }
    var foremanWins = metric.better === "high" ? f > b : f < b;
    var text;
    if (metric.points) {
      // A share is already a percent, so show the gap in percentage points.
      var pts = Math.abs(f - b).toFixed(1);
      text = (foremanWins ? "+" : "-") + pts + " pts";
    } else if (b === 0) {
      text = foremanWins ? "Better" : "Worse";
    } else {
      var pct = Math.round(Math.abs(f - b) / Math.abs(b) * 100);
      if (metric.better === "high") {
        text = (foremanWins ? "+" : "-") + pct + "%";
      } else {
        text = (foremanWins ? pct + "% less" : pct + "% more");
      }
    }
    return { winner: foremanWins ? "foreman" : "baseline", text: text, good: foremanWins };
  }

  function render(state) {
    if (!statusEl) build();
    var f = ((state && state.foreman) || {}).totals || {};
    var b = ((state && state.baseline) || {}).totals || {};

    METRICS.forEach(function (metric) {
      var row = cells[metric.key];
      var fv = f[metric.key];
      var bv = b[metric.key];
      var result = compare(metric, fv, bv);
      row.foreman.textContent = format(fv, metric);
      row.baseline.textContent = format(bv, metric);
      var foremanGreen = result.winner === "foreman" || result.winner === "both";
      var baselineGreen = result.winner === "baseline" || result.winner === "both";
      row.foreman.className = "np-cell" + (foremanGreen ? " np-win" : "");
      row.baseline.className = "np-cell" + (baselineGreen ? " np-win" : "");
      row.gain.textContent = result.text;
      row.gain.className = "np-gain" +
        (result.good === true ? " np-good" : result.good === false ? " np-bad" : "");
    });

    statusEl.textContent = state && state.clock ? "As of " + state.clock : "";
    statusEl.className = "np-status";
  }

  function showError() {
    if (!statusEl) build();
    statusEl.textContent = "Server not reachable";
    statusEl.className = "np-status np-error";
  }

  function poll() {
    fetch("/api/state")
      .then(function (response) {
        if (!response.ok) throw new Error("HTTP " + response.status);
        return response.json();
      })
      .then(render)
      .catch(showError);
  }

  function start() {
    build();
    if (!window.NUMBERS_PANEL_NO_POLL) {
      poll();
      setInterval(poll, POLL_MS);
    }
  }

  window.NumbersPanel = { render: render, compare: compare, METRICS: METRICS };

  if (document.readyState === "loading") {
    document.addEventListener("DOMContentLoaded", start);
  } else {
    start();
  }
})();
