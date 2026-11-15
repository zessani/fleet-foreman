/*
 * "Why?" panel (owner: Vishva).
 *
 * Pick a job and it shows the server's own explanation of why that job went
 * to that tractor, from GET /api/why?job=J1:
 *   {"job", "label", "status", "chosen": {tractor, name, implement,
 *    implement_name, start, finish, deadline, margin_min} or null,
 *    "held_reason", "others": [{tractor, name, reason}], "text": [sentences]}
 * An unknown job answers {"ok": false, "error": ...}. A reply with ok false,
 * or without a text array, counts as no answer, and the panel then says so
 * and writes no reasons of its own.
 *
 * Facts that only repeat server data may sit beside it: the job's plan row,
 * the latest plan change, the safety gate checks, and log lines as written.
 *
 * It draws into <div id="why-panel"></div> if the page has one, otherwise it
 * adds that div at the end of <body>.
 *     window.WhyPanel.show("J5")
 * or:  window.dispatchEvent(new CustomEvent("foreman:select-job", {detail: {job: "J5"}}))
 *
 * A page that already polls can set window.WHY_PANEL_NO_POLL = true and call
 * window.WhyPanel.render(state). Plain JavaScript, no build step, no CDN.
 * Server text is only ever written with textContent.
 */
(function () {
  "use strict";

  var POLL_MS = 1000;
  var WHY_RETRY_MS = 30000;
  var HISTORY_LINES = 8;
  var IMPLEMENT_CHECK = 2;
  var FUEL_CHECK = 3;

  var CSS = [
    ".wp { font-family: system-ui, -apple-system, 'Segoe UI', sans-serif; color: #edf5ef;",
    "  background: #19221e; border: 1px solid #34453c; border-radius: 8px; padding: 14px 16px;",
    "  margin: 12px 0; box-sizing: border-box; }",
    ".wp-top { display: flex; flex-wrap: wrap; gap: 10px; align-items: center; justify-content: space-between; }",
    ".wp-title { font-size: 13px; font-weight: 700; text-transform: uppercase; letter-spacing: .05em;",
    "  color: #9fb0a5; margin: 0; }",
    ".wp select { background: #202c26; color: #edf5ef; border: 1px solid #34453c; border-radius: 6px;",
    "  padding: 6px 8px; font-size: 14px; max-width: 100%; }",
    ".wp-job { font-size: clamp(20px, 2.6vw, 28px); font-weight: 700; margin: 12px 0 2px; }",
    ".wp-meta { font-size: 13px; color: #9fb0a5; margin: 0 0 10px; }",
    ".wp-verdict { font-size: 17px; font-weight: 700; padding: 8px 12px; border-radius: 6px;",
    "  background: #202c26; border-left: 4px solid #f0ae4d; margin: 0 0 12px; }",
    ".wp-verdict.wp-held { border-left-color: #ef6d68; }",
    ".wp-verdict.wp-done { border-left-color: #67c587; }",
    ".wp h3 { font-size: 12px; text-transform: uppercase; letter-spacing: .05em; color: #9fb0a5;",
    "  margin: 14px 0 6px; }",
    ".wp ul { list-style: none; margin: 0; padding: 0; }",
    ".wp li { display: grid; grid-template-columns: 92px minmax(0, 1fr); gap: 10px; padding: 6px 0;",
    "  border-top: 1px solid #2b3a33; font-size: 14px; line-height: 1.4; }",
    ".wp li .wp-k { color: #9fb0a5; font-weight: 600; }",
    ".wp .wp-ok { color: #67c587; font-weight: 700; }",
    ".wp .wp-bad { color: #ef6d68; font-weight: 700; }",
    ".wp-hist li { grid-template-columns: 52px minmax(0, 1fr); }",
    ".wp-note { font-size: 12px; color: #9fb0a5; margin-top: 12px; }",
    ".wp-empty { color: #9fb0a5; padding: 20px 0; text-align: center; }"
  ].join("\n");

  var els = null;
  var state = null;
  var selected = null;
  var optionsKey = null;
  var lastRenderKey = null;
  var polling = false;
  var server = { job: null, data: null, failed: false, retryAt: 0, pending: false };

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
    var root = document.getElementById("why-panel");
    if (!root) {
      root = el("div");
      root.id = "why-panel";
      document.body.appendChild(root);
    }
    root.textContent = "";

    var panel = el("section", "wp");
    var top = el("div", "wp-top");
    top.appendChild(el("h2", "wp-title", "Why this tractor?"));
    var select = el("select");
    select.setAttribute("aria-label", "Pick a job");
    select.addEventListener("change", function () { show(select.value); });
    top.appendChild(select);
    var body = el("div");
    body.appendChild(el("div", "wp-empty", "Waiting for the plan..."));
    panel.appendChild(top);
    panel.appendChild(body);
    root.appendChild(panel);
    els = { select: select, body: body };
  }

  function cap(word) { return word ? word.charAt(0).toUpperCase() + word.slice(1) : ""; }

  function nodesById() {
    var map = {};
    ((state.farm && state.farm.nodes) || []).forEach(function (n) { map[n.id] = n; });
    return map;
  }

  function jobLabel(job, nodes) {
    var n = nodes[job.field];
    return cap(job.type) + " " + ((n && n.name) || job.field);
  }

  function dayStart() {
    var parts = String(state.clock || "0:0").split(":");
    return (+parts[0]) * 60 + (+parts[1]) - Math.floor(state.now || 0);
  }

  function clock(minute) {
    var total = Math.floor(dayStart() + minute);
    var h = Math.floor(total / 60) % 24;
    var m = total % 60;
    return (h < 10 ? "0" : "") + h + ":" + (m < 10 ? "0" : "") + m;
  }

  function tractorsById() {
    var map = {};
    ((state.foreman && state.foreman.tractors) || []).forEach(function (t) { map[t.id] = t; });
    return map;
  }

  function jobById(id) {
    var jobs = (state.foreman && state.foreman.jobs) || [];
    for (var i = 0; i < jobs.length; i++) if (jobs[i].id === id) return jobs[i];
    return null;
  }

  function safetyCheck(id) {
    var checks = (state.foreman && state.foreman.safety && state.foreman.safety.checks) || [];
    for (var i = 0; i < checks.length; i++) if (checks[i].id === id) return checks[i];
    return null;
  }

  function updateOptions() {
    var jobs = (state.foreman && state.foreman.jobs) || [];
    var nodes = nodesById();
    var names = tractorsById();
    var key = JSON.stringify(jobs.map(function (j) { return [j.id, j.status, j.tractor]; }));
    if (key === optionsKey) return;
    optionsKey = key;
    var select = els.select;
    select.textContent = "";
    jobs.forEach(function (job) {
      var who = job.tractor && names[job.tractor] ? names[job.tractor].name : "no tractor";
      var option = el("option", null, job.id + "  " + jobLabel(job, nodes) + "  (" + who + ", " + job.status + ")");
      option.value = job.id;
      select.appendChild(option);
    });
    if (!selected || !jobs.some(function (j) { return j.id === selected; })) {
      var active = jobs.filter(function (j) { return j.status === "active"; })[0];
      selected = (active || jobs[0] || {}).id || null;
    }
    if (selected) select.value = selected;
  }

  function show(jobId) {
    selected = jobId;
    if (els && els.select.value !== jobId) els.select.value = jobId;
    lastRenderKey = null;
    askServer(true);
    draw();
  }

  function askServer(force) {
    if (!selected || server.pending) return;
    if (!force && server.job === selected && Date.now() < server.retryAt) return;
    var job = selected;
    server.pending = true;
    fetch("/api/why?job=" + encodeURIComponent(job))
      .then(function (response) {
        if (!response.ok) throw new Error("HTTP " + response.status);
        return response.json();
      })
      .then(function (data) {
        server.job = job;
        server.failed = false;
        server.data = data && data.ok !== false && Array.isArray(data.text) ? data : null;
        server.retryAt = Date.now() + POLL_MS * 3;
      })
      .catch(function () {
        server.job = job;
        server.failed = true;
        server.data = null;
        server.retryAt = Date.now() + WHY_RETRY_MS;
      })
      .then(function () {
        server.pending = false;
        lastRenderKey = null;
        draw();
      });
  }

  function addList(body, heading, lines) {
    if (!lines.length) return;
    body.appendChild(el("h3", null, heading));
    var list = el("ul");
    lines.forEach(function (text) {
      var item = el("li");
      item.style.gridTemplateColumns = "1fr";
      item.appendChild(el("span", null, text));
      list.appendChild(item);
    });
    body.appendChild(list);
  }

  function checkRow(list, key, check) {
    if (!check) return;
    var item = el("li");
    item.appendChild(el("span", "wp-k", key));
    var text = el("span");
    text.appendChild(document.createTextNode("Safety gate check \"" + check.name + "\": "));
    text.appendChild(el("span", check.passed ? "wp-ok" : "wp-bad", check.passed ? "passed" : "failed"));
    item.appendChild(text);
    list.appendChild(item);
  }

  function draw() {
    if (!els || !state) return;
    var fromServer = server.job === selected ? server.data : null;
    var renderKey = JSON.stringify([selected, state.now, fromServer, server.failed, server.pending]);
    if (renderKey === lastRenderKey) return;
    lastRenderKey = renderKey;

    var body = els.body;
    body.textContent = "";
    var job = jobById(selected);
    if (!job) {
      body.appendChild(el("div", "wp-empty", "No jobs yet."));
      return;
    }
    var nodes = nodesById();
    var tractors = tractorsById();
    var foreman = state.foreman || {};
    var plan = foreman.plan || [];
    var planRow = plan.filter(function (r) { return r.job === job.id; })[0] || null;
    var label = (fromServer && fromServer.label) || jobLabel(job, nodes);
    var status = (fromServer && fromServer.status) || job.status;

    body.appendChild(el("div", "wp-job", label));
    body.appendChild(el("p", "wp-meta", "Job " + job.id + "  \u00b7  " + status));

    if (!fromServer) {
      var waiting = server.job === selected && server.failed
        ? "The server could not explain this job."
        : "Waiting for the server's answer.";
      body.appendChild(el("p", "wp-note", waiting));
    } else {
      var chosen = fromServer.chosen;
      var cls = status === "held" ? "wp-verdict wp-held" : status === "done" ? "wp-verdict wp-done" : "wp-verdict";
      var first = fromServer.text[0];
      if (status === "held" && fromServer.held_reason) {
        body.appendChild(el("div", "wp-verdict wp-held", "Held: " + fromServer.held_reason));
      } else if (first) {
        body.appendChild(el("div", cls, first));
      }
      if (chosen && chosen.name) {
        var who = chosen.name + (chosen.implement_name ? " with the " + chosen.implement_name : "");
        body.appendChild(el("p", "wp-meta", "Chosen: " + who));
      }
      var others = Array.isArray(fromServer.others) && fromServer.others.length
        ? fromServer.others.map(function (o) { return (o.name || o.tractor) + " " + o.reason + "."; })
        : fromServer.text.slice(1);
      addList(body, "Why not the others", others);
    }

    if (planRow) {
      var t = tractors[planRow.tractor];
      body.appendChild(el("h3", null, "Plan"));
      var planList = el("ul");
      var item = el("li");
      item.appendChild(el("span", "wp-k", "Planned"));
      item.appendChild(el("span", null, ((t && t.name) || planRow.tractor) + ", starts " +
        clock(planRow.start) + ", finishes " + clock(planRow.finish) + "."));
      planList.appendChild(item);
      if (status !== "done") {
        checkRow(planList, "Implement", safetyCheck(IMPLEMENT_CHECK));
        checkRow(planList, "Fuel", safetyCheck(FUEL_CHECK));
      }
      body.appendChild(planList);
    }

    var change = (((foreman.explanation || {}).changes) || []).filter(function (c) {
      return c.indexOf(label + ":") === 0;
    })[0];
    var events = [];
    (foreman.log || []).forEach(function (line) {
      if (line.message && line.message.indexOf(label) !== -1) {
        events.push({ clock: line.clock, text: line.agent + ": " + line.message });
      }
    });
    events = events.slice(-HISTORY_LINES);
    if (change || events.length) {
      body.appendChild(el("h3", null, "Re-plans and events"));
      var hist = el("ul", "wp-hist");
      if (change) {
        var latest = el("li");
        latest.appendChild(el("span", "wp-k", "Latest"));
        latest.appendChild(el("span", null, change));
        hist.appendChild(latest);
      }
      events.forEach(function (e) {
        var li = el("li");
        li.appendChild(el("span", "wp-k", e.clock));
        li.appendChild(el("span", null, e.text));
        hist.appendChild(li);
      });
      body.appendChild(hist);
    }
  }

  function render(newState) {
    if (!els) build();
    if (!newState) return;
    state = newState;
    updateOptions();
    askServer(false);
    draw();
  }

  function poll() {
    if (polling) return;
    polling = true;
    fetch("/api/state")
      .then(function (response) {
        if (!response.ok) throw new Error("HTTP " + response.status);
        return response.json();
      })
      .then(render)
      .catch(function () {
        if (!els) build();
        if (!state) {
          els.body.textContent = "";
          els.body.appendChild(el("div", "wp-empty", "Server not reachable"));
        }
      })
      .then(function () { polling = false; });
  }

  function start() {
    build();
    window.addEventListener("foreman:select-job", function (event) {
      if (event.detail && event.detail.job) show(event.detail.job);
    });
    if (!window.WHY_PANEL_NO_POLL) {
      poll();
      setInterval(poll, POLL_MS);
    }
  }

  window.WhyPanel = { render: render, show: show };

  if (document.readyState === "loading") {
    document.addEventListener("DOMContentLoaded", start);
  } else {
    start();
  }
})();
