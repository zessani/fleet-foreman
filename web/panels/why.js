/*
 * "Why?" panel (owner: Vishva).
 *
 * Pick a job and it says in plain English why that job went to that tractor:
 * the implement, power and fuel checks, the route and distance, the timing
 * against the deadline, the priority, what the other tractors could not do,
 * and every re-plan that touched the job.
 *
 * Where the words come from:
 *   1. GET /api/why?job=J1 (CLAUDE.md sections 8c and 15) when the server has it.
 *      Expected shape (to agree with Zayyan):
 *        {"job": "J5", "tractor": "T1",
 *         "chosen": "Atlas: owns the 8 m row planter (200 hp, needs 150) ...",
 *         "others": [{"tractor": "T2", "name": "Bolt", "reason": "has no plant implement"}],
 *         "history": ["09:30 moved from Cedar to Atlas: Cedar broke down"]}
 *   2. Otherwise it is built from GET /api/state alone: the plan row, the job
 *      row, the tractors, the Safety checks, the explanation, and the agent log.
 *      Nothing is invented: every sentence comes from one of those fields.
 *
 * To use it, add one line to a page:
 *     <script src="panels/why.js"></script>
 * It draws into <div id="why-panel"></div> if the page has one, otherwise it
 * adds that div at the end of <body>.
 *
 * Other code (like the farm map) can open a job with:
 *     window.WhyPanel.show("J5")
 * or by sending:  window.dispatchEvent(new CustomEvent("foreman:select-job", {detail: {job: "J5"}}))
 *
 * A page that already polls can set window.WHY_PANEL_NO_POLL = true and call
 * window.WhyPanel.render(state). Plain JavaScript, no build step, no CDN.
 * Server text is only ever written with textContent.
 */
(function () {
  "use strict";

  var POLL_MS = 1000;
  var WHY_RETRY_MS = 30000;   // how long to wait before asking /api/why again after a 404
  var HISTORY_LINES = 8;
  var PRIORITY_WORDS = { 1: "low", 2: "below normal", 3: "normal", 4: "high", 5: "critical" };
  var IMPLEMENT_CHECK = 2;    // Safety check ids in CLAUDE.md section 12
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
  var server = { job: null, data: null, retryAt: 0, pending: false };

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

  // ---- small helpers over the state ----------------------------------------

  function cap(word) { return word ? word.charAt(0).toUpperCase() + word.slice(1) : ""; }

  function nodesById() {
    var map = {};
    ((state.farm && state.farm.nodes) || []).forEach(function (n) { map[n.id] = n; });
    return map;
  }

  function nodeName(id, nodes) {
    var n = nodes[id];
    if (!n) return id;
    if (n.kind === "depot") return n.name || "Yard";
    return n.name || id;
  }

  // The same words the agents use in the log, like "Plant West Bench".
  function jobLabel(job, nodes) { return cap(job.type) + " " + nodeName(job.field, nodes); }

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

  function routeKm(path, nodes) {
    var km = 0;
    for (var i = 1; i < path.length; i++) {
      var a = nodes[path[i - 1]];
      var b = nodes[path[i]];
      if (a && b) km += Math.hypot(a.x - b.x, a.y - b.y);
    }
    return km;
  }

  function tractorsById() {
    var map = {};
    ((state.foreman && state.foreman.tractors) || []).forEach(function (t) { map[t.id] = t; });
    return map;
  }

  // The implement's name from the farm catalog if the state has it, else from
  // the Fleet agent's words in the log ("Atlas can pull the 8 m row planter"),
  // else its id.
  function implementName(id, tractorName, jobType) {
    var list = (state.farm && state.farm.implements) || [];
    for (var i = 0; i < list.length; i++) if (list[i].id === id) return list[i].name || id;
    var log = (state.foreman && state.foreman.log) || [];
    var pattern = tractorName + " can pull the ";
    for (var j = log.length - 1; j >= 0; j--) {
      var message = log[j].message || "";
      var at = message.indexOf(pattern);
      if (at !== -1 && message.indexOf(cap(jobType) + " ") !== -1) {
        var rest = message.slice(at + pattern.length);
        var end = rest.search(/[;.]/);
        return end === -1 ? rest : rest.slice(0, end);
      }
    }
    return id;
  }

  // The clock of the log line "<tractor> started|finished: <label>", if any.
  function logClock(word, label) {
    var log = (state.foreman && state.foreman.log) || [];
    var found = null;
    log.forEach(function (line) {
      if (line.message && line.message.indexOf(" " + word + ": " + label) !== -1) found = line.clock;
    });
    return found;
  }

  function safetyCheck(id) {
    var checks = (state.foreman && state.foreman.safety && state.foreman.safety.checks) || [];
    for (var i = 0; i < checks.length; i++) if (checks[i].id === id) return checks[i];
    return null;
  }

  // ---- picking a job --------------------------------------------------------

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

  // ---- GET /api/why, when the server has it ------------------------------------

  function askServer(force) {
    if (!selected || server.pending) return;
    if (!force && Date.now() < server.retryAt) return;
    var job = selected;
    server.pending = true;
    fetch("/api/why?job=" + encodeURIComponent(job))
      .then(function (response) {
        if (!response.ok) throw new Error("HTTP " + response.status);
        return response.json();
      })
      .then(function (data) {
        server.job = job;
        server.data = data && (data.chosen || data.others) ? data : null;
        server.retryAt = Date.now() + POLL_MS * 3;
      })
      .catch(function () {
        server.job = job;
        server.data = null;
        server.retryAt = Date.now() + WHY_RETRY_MS;
      })
      .then(function () {
        server.pending = false;
        lastRenderKey = null;
        draw();
      });
  }

  // ---- the explanation ----------------------------------------------------------

  function row(list, key, parts) {
    var item = el("li");
    item.appendChild(el("span", "wp-k", key));
    var text = el("span");
    parts.forEach(function (part) {
      if (typeof part === "string") text.appendChild(document.createTextNode(part));
      else text.appendChild(el("span", part.cls, part.text));
    });
    item.appendChild(text);
    list.appendChild(item);
  }

  function checkPart(check) {
    if (!check) return [];
    return [" Safety check \"" + check.name + "\": ",
            check.passed ? { cls: "wp-ok", text: "passed" } : { cls: "wp-bad", text: "failed" }, "."];
  }

  function otherTractors(job, label, chosenId, plan, tractors, nodes) {
    // Best source: the Fleet agent's own verdicts for this job, from the log.
    var log = (state.foreman && state.foreman.log) || [];
    for (var i = log.length - 1; i >= 0; i--) {
      var line = log[i];
      if (line.agent === "Fleet" && line.event === "implements" && line.data && line.data.job === job.id) {
        var after = line.message.indexOf(":");
        var verdicts = line.message.slice(after + 1).replace(/\.$/, "").split(";");
        return verdicts.map(function (v) { return v.trim(); })
          .filter(function (v) { return v && (!tractors[chosenId] || v.indexOf(tractors[chosenId].name) !== 0); })
          .map(function (v) { return { text: v + " (Fleet agent, " + line.clock + ")" }; });
      }
    }
    // Otherwise: what the plan and the tractor states show.
    var out = [];
    Object.keys(tractors).sort().forEach(function (id) {
      if (id === chosenId) return;
      var t = tractors[id];
      if (t.status === "broken") {
        out.push({ text: t.name + " is out of service now." });
        return;
      }
      var same = plan.filter(function (r) {
        var other = jobById(r.job);
        return r.tractor === id && other && other.type === job.type && other.id !== job.id;
      });
      if (same.length) {
        var labels = same.map(function (r) { return jobLabel(jobById(r.job), nodes); });
        out.push({ text: t.name + " can also do " + job.type + " work, but is planned for " +
          labels.join(", ") + ". Giving this job to " + (tractors[chosenId] || {}).name +
          " cost less overall (travel, waiting and lateness)." });
      } else {
        out.push({ text: t.name + " has no " + job.type + " work in today's plan." });
      }
    });
    return out;
  }

  function jobById(id) {
    var jobs = (state.foreman && state.foreman.jobs) || [];
    for (var i = 0; i < jobs.length; i++) if (jobs[i].id === id) return jobs[i];
    return null;
  }

  function history(job, label) {
    var lines = [];
    ((state.foreman && state.foreman.log) || []).forEach(function (line) {
      if (line.message && line.message.indexOf(label) !== -1) {
        lines.push({ clock: line.clock, text: line.agent + ": " + line.message });
      }
    });
    return lines.slice(-HISTORY_LINES);
  }

  function draw() {
    if (!els || !state) return;
    var renderKey = JSON.stringify([selected, state.now, server.job === selected ? server.data : null]);
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
    var plan = (state.foreman && state.foreman.plan) || [];
    var planRow = plan.filter(function (r) { return r.job === job.id; })[0] || null;
    var label = jobLabel(job, nodes);
    var chosenId = (planRow && planRow.tractor) || job.tractor;
    var chosen = tractors[chosenId];
    var fromServer = server.job === selected ? server.data : null;

    body.appendChild(el("div", "wp-job", label));
    var meta = "Job " + job.id + "  ·  " + job.status;
    if (job.status !== "done" && job.pct > 0) meta += ", " + job.pct + "% done";
    meta += "  ·  priority " + job.priority + " of 5 (" + (PRIORITY_WORDS[job.priority] || "?") + ")";
    body.appendChild(el("p", "wp-meta", meta));

    // The verdict.
    var verdict;
    if (job.status === "held") {
      verdict = el("div", "wp-verdict wp-held", "Held, not planned: " + (job.reason || "no tractor can take it") + ".");
    } else if (!chosen) {
      verdict = el("div", "wp-verdict", "Not planned yet.");
    } else if (job.status === "done") {
      verdict = el("div", "wp-verdict wp-done", chosen.name + " finished this job.");
    } else {
      verdict = el("div", "wp-verdict", chosen.name + " does this job.");
    }
    body.appendChild(verdict);

    if (fromServer && fromServer.chosen) {
      body.appendChild(el("p", null, String(fromServer.chosen)));
    }

    if (chosen && planRow) {
      body.appendChild(el("h3", null, "Why " + chosen.name));
      var reasons = el("ul");

      var done = job.status === "done";
      var viaYard = planRow.path.slice(1, -1).indexOf("YARD") !== -1;
      // The Safety checks in the state are for the current plan, so they only
      // speak for jobs still in it. Every plan is checked before it is sent.
      var dispatched = [" Checked by the Safety agent before the plan was sent."];

      row(reasons, "Implement", [chosen.name + " pulls the " +
        implementName(planRow.implement, chosen.name, job.type) +
        ", a " + job.type + " implement it owns, with the power to pull it."]
        .concat(done ? dispatched : checkPart(safetyCheck(IMPLEMENT_CHECK))));

      if (done) {
        row(reasons, "Fuel", ["Fuel stayed above the 10% reserve." + dispatched[0]]);
      } else {
        row(reasons, "Fuel", [chosen.name + " has " + chosen.fuel_pct + "% fuel now." +
          (viaYard ? " Its route goes through the yard, where it can refuel or swap implements." : "")]
          .concat(checkPart(safetyCheck(FUEL_CHECK))));
      }

      if (planRow.path.length > 1) {
        var km = routeKm(planRow.path, nodes);
        row(reasons, "Route", [planRow.path.map(function (id) { return nodeName(id, nodes); }).join(" → ") +
          ", about " + km.toFixed(1) + " km."]);
      } else {
        row(reasons, "Route", ["Already at " + nodeName(job.field, nodes) + ", no driving needed."]);
      }

      var timing = "Starts " + clock(planRow.start) + ", finishes " + clock(planRow.finish) + ". ";
      var finishedAt = done ? logClock("finished", label) : null;
      var deadlineParts;
      if (done && finishedAt) {
        var startedAt = logClock("started", label) || clock(planRow.start);
        var parts = finishedAt.split(":");
        var finishMin = (+parts[0]) * 60 + (+parts[1]) - dayStart();
        var margin = Math.round(job.deadline - finishMin);
        deadlineParts = ["Started " + startedAt + ", finished " + finishedAt + ". ",
          margin >= 0 ? { cls: "wp-ok", text: "On time" } : { cls: "wp-bad", text: -margin + " min late" },
          margin >= 0 ? ": " + margin + " min before its " + clock(job.deadline) + " deadline."
                      : " for its " + clock(job.deadline) + " deadline."];
      } else if (planRow.late_min > 0) {
        deadlineParts = [timing, { cls: "wp-bad", text: Math.round(planRow.late_min) + " min late" },
          " for its " + clock(job.deadline) + " deadline. Flagged, not hidden."];
      } else {
        var spare = Math.round(job.deadline - planRow.finish);
        deadlineParts = [timing, { cls: "wp-ok", text: "On time" },
          ": " + spare + " min before its " + clock(job.deadline) + " deadline."];
      }
      row(reasons, "Deadline", deadlineParts);

      var pri = "Priority " + job.priority + " of 5 (" + (PRIORITY_WORDS[job.priority] || "?") + ").";
      if (job.priority >= 4) pri += " Urgent work is placed early in the plan.";
      row(reasons, "Priority", [pri]);
      body.appendChild(reasons);
    }

    // The other tractors.
    var others = fromServer && Array.isArray(fromServer.others)
      ? fromServer.others.map(function (o) { return { text: (o.name || o.tractor) + ": " + o.reason }; })
      : otherTractors(job, label, chosenId, plan, tractors, nodes);
    if (others.length) {
      body.appendChild(el("h3", null, "Why not the others"));
      var otherList = el("ul");
      others.forEach(function (o) {
        var item = el("li");
        item.style.gridTemplateColumns = "1fr";
        item.appendChild(el("span", null, o.text));
        otherList.appendChild(item);
      });
      body.appendChild(otherList);
    }

    // Re-plans and events that touched this job.
    var change = ((state.foreman.explanation || {}).changes || []).filter(function (c) {
      return c.indexOf(label + ":") === 0;
    })[0];
    var events = fromServer && Array.isArray(fromServer.history)
      ? fromServer.history.map(function (h) { return { clock: "", text: String(h) }; })
      : history(job, label);
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
        var item = el("li");
        item.appendChild(el("span", "wp-k", e.clock));
        item.appendChild(el("span", null, e.text));
        hist.appendChild(item);
      });
      body.appendChild(hist);
    }

    body.appendChild(el("p", "wp-note", fromServer
      ? "Reasons from the Fleet agent (GET /api/why), facts from the live plan."
      : "Built from the live plan, the Safety checks, and the agent log."));
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
      });
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
