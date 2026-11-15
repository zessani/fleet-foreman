/*
 * Decisions panel (built by Zayyan; lives with the other panels).
 *
 * Shows what the agents just decided, from GET /api/state (CLAUDE.md 8c):
 *   - the latest plan change: headline, one line per changed job, the summary,
 *     how many jobs moved and how many stayed, and the re-plan time
 *   - the Safety agent's result: passed or failed, and the nine checks
 *   - the agent log, newest first
 *   - an area for the AI agent's reasoning, which Phase 3 fills
 *
 * To use it, add two lines to a page:
 *     <div id="decisions-panel"></div>
 *     <script src="panels/decisions.js"></script>
 * Without the div it adds itself at the end of <body>.
 *
 * It polls /api/state by itself. A page that already polls can set
 * window.DECISIONS_PANEL_NO_POLL = true before loading this file and call
 * window.DecisionsPanel.render(state) instead.
 *
 * Plain JavaScript, no build step, no CDN. Server text is only ever written
 * with textContent.
 */
(function () {
  "use strict";

  const POLL_MS = 500;
  const LOG_LINES = 60;
  const AGENT_COLORS = {
    Orchestrator: "#f0ae4d", Fleet: "#7fb2ff", Navigation: "#5ccfc0",
    Safety: "#67c587", Recovery: "#ef8d68",
  };

  const CSS = `
.dp { --dp-panel: #19221e; --dp-panel2: #202c26; --dp-line: #34453c; --dp-text: #edf5ef;
  --dp-muted: #9fb0a5; --dp-accent: #f0ae4d; --dp-good: #67c587; --dp-bad: #ef6d68;
  font-family: system-ui, -apple-system, "Segoe UI", sans-serif; color: var(--dp-text);
  display: grid; grid-template-columns: repeat(2, minmax(0, 1fr));
  gap: 12px; box-sizing: border-box; }
@media (max-width: 760px) { .dp { grid-template-columns: minmax(0, 1fr); } }
.dp * { box-sizing: border-box; }
.dp-card { background: var(--dp-panel); border: 1px solid var(--dp-line); border-radius: 8px;
  padding: 14px 16px; min-width: 0; }
.dp-wide { grid-column: 1 / -1; }
.dp-title { margin: 0 0 10px; font-size: clamp(12px, 1.1vw, 15px); font-weight: 700;
  letter-spacing: 0.06em; text-transform: uppercase; color: var(--dp-muted);
  display: flex; justify-content: space-between; gap: 12px; }
.dp-headline { font-size: clamp(20px, 2.4vw, 34px); font-weight: 700; line-height: 1.2;
  margin: 0 0 8px; }
.dp-summary { font-size: clamp(16px, 1.7vw, 24px); margin: 0 0 12px; }
.dp-chips { display: flex; flex-wrap: wrap; gap: 8px; margin: 0 0 12px; }
.dp-chip { background: var(--dp-panel2); border: 1px solid var(--dp-line); border-radius: 6px;
  padding: 6px 12px; font-size: clamp(14px, 1.4vw, 20px); font-variant-numeric: tabular-nums; }
.dp-chip b { font-size: 1.25em; margin-right: 6px; }
.dp-chip.dp-strong { border-color: var(--dp-accent); color: var(--dp-accent); }
.dp-list { list-style: none; margin: 0; padding: 0; }
.dp-list li { padding: 8px 0; border-top: 1px solid var(--dp-line);
  font-size: clamp(15px, 1.5vw, 21px); line-height: 1.35; }
.dp-badge { display: inline-block; border-radius: 6px; padding: 6px 14px; font-weight: 800;
  font-size: clamp(20px, 2.4vw, 34px); letter-spacing: 0.03em; margin-bottom: 6px; }
.dp-pass { background: #1f3a2a; color: var(--dp-good); border: 1px solid var(--dp-good); }
.dp-fail { background: #40201f; color: var(--dp-bad); border: 1px solid var(--dp-bad); }
.dp-none { background: var(--dp-panel2); color: var(--dp-muted); border: 1px solid var(--dp-line); }
.dp-line { font-size: clamp(14px, 1.4vw, 19px); color: var(--dp-muted); margin: 0 0 10px; }
.dp-check { display: grid; grid-template-columns: 1.6em minmax(0, 1fr); gap: 4px 6px;
  padding: 6px 0; border-top: 1px solid var(--dp-line); font-size: clamp(14px, 1.4vw, 19px); }
.dp-mark { font-weight: 800; font-size: 1.2em; line-height: 1.1; }
.dp-mark.dp-ok { color: var(--dp-good); }
.dp-mark.dp-no { color: var(--dp-bad); }
.dp-mark.dp-warn { color: var(--dp-accent); }
.dp-detail { color: var(--dp-muted); font-size: 0.88em; }
.dp-log { max-height: 46vh; overflow-y: auto; }
.dp-log-line { display: grid; grid-template-columns: 4.2em 7.6em minmax(0, 1fr); gap: 10px;
  padding: 6px 0; border-top: 1px solid var(--dp-line); font-size: clamp(13px, 1.3vw, 18px);
  line-height: 1.35; }
.dp-time { color: var(--dp-muted); font-variant-numeric: tabular-nums; }
.dp-agent { font-weight: 700; }
.dp-reasoning { min-height: 110px; border: 1px dashed var(--dp-line); border-radius: 6px;
  padding: 12px; color: var(--dp-muted); font-size: clamp(14px, 1.4vw, 19px);
  white-space: pre-wrap; }
.dp-reasoning.dp-filled { color: var(--dp-text); border-style: solid; }
.dp-status { grid-column: 1 / -1; font-size: 13px; color: var(--dp-muted); min-height: 1em; }
.dp-status.dp-error { color: var(--dp-bad); font-weight: 700; }
`;

  let parts = null;      // the elements that get re-filled
  let lastKeys = {};     // what each section last showed, so nothing is rebuilt for no reason

  function el(tag, className, text) {
    const node = document.createElement(tag);
    if (className) node.className = className;
    if (text != null) node.textContent = text;
    return node;
  }

  function card(title, wide) {
    const box = el("section", "dp-card" + (wide ? " dp-wide" : ""));
    const heading = el("h2", "dp-title");
    const name = el("span", null, title);
    const note = el("span");
    heading.append(name, note);
    box.append(heading);
    return { box, note };
  }

  function build() {
    const style = el("style");
    style.textContent = CSS;
    document.head.appendChild(style);

    let root = document.getElementById("decisions-panel");
    if (!root) {
      root = el("div");
      root.id = "decisions-panel";
      document.body.appendChild(root);
    }
    root.classList.add("dp");
    root.textContent = "";

    const change = card("Latest plan change", true);
    const headline = el("p", "dp-headline");
    const summary = el("p", "dp-summary");
    const chips = el("div", "dp-chips");
    const changes = el("ul", "dp-list");
    change.box.append(headline, summary, chips, changes);

    const safety = card("Safety agent");
    const badge = el("div", "dp-badge dp-none", "NOT CHECKED YET");
    const safetyLine = el("p", "dp-line");
    const checks = el("div");
    safety.box.append(badge, safetyLine, checks);

    const reasoning = card("AI agent reasoning");
    const reasoningBody = el("div", "dp-reasoning");
    reasoning.box.append(reasoningBody);

    const log = card("Agent log", true);
    const logBody = el("div", "dp-log");
    log.box.append(logBody);

    const status = el("div", "dp-status");
    root.append(change.box, safety.box, reasoning.box, log.box, status);

    parts = { headline, summary, chips, changes, changeNote: change.note, badge, safetyLine,
              checks, safetyNote: safety.note, reasoningBody, reasoningNote: reasoning.note,
              logBody, logNote: log.note, status };
    lastKeys = {};
    renderReasoning(null);
  }

  function changed(section, key) {
    if (lastKeys[section] === key) return false;
    lastKeys[section] = key;
    return true;
  }

  function chip(count, label, strong) {
    const node = el("span", "dp-chip" + (strong ? " dp-strong" : ""));
    node.append(el("b", null, String(count)), document.createTextNode(label));
    return node;
  }

  function renderChange(foreman) {
    const story = foreman.explanation || {};
    const planNo = foreman.plan_no || 0;
    if (!changed("change", JSON.stringify([planNo, story]))) return;

    parts.changeNote.textContent = planNo ? "Plan " + planNo : "";
    parts.headline.textContent = story.headline || "No plan yet.";
    parts.summary.textContent = story.summary || "";

    parts.chips.textContent = "";
    const moved = story.moved || 0;
    const retimed = story.retimed || 0;
    const unchanged = story.unchanged || 0;
    if (planNo > 1) {
      // A job that kept its tractor stayed put, whether or not its times shifted.
      parts.chips.append(chip(moved, moved === 1 ? "job moved" : "jobs moved", moved > 0));
      parts.chips.append(chip(retimed + unchanged, "stayed with their tractor"));
      if (retimed) parts.chips.append(chip(retimed, "of those retimed"));
      if (story.held) parts.chips.append(chip(story.held, "held", true));
    } else if (planNo === 1) {
      parts.chips.append(chip((story.changes || []).length, "jobs in the first plan of the day"));
    }
    if (story.ms != null) {
      const ms = el("span", "dp-chip");
      ms.append(el("b", null, String(story.ms) + " ms"),
                document.createTextNode(planNo > 1 ? "from event to new plan" : "to plan the day"));
      parts.chips.append(ms);
    }

    parts.changes.textContent = "";
    (story.changes || []).forEach(function (sentence) {
      parts.changes.append(el("li", null, sentence));
    });
  }

  function renderSafety(foreman) {
    const safety = foreman.safety || {};
    const checks = safety.checks || [];
    if (!changed("safety", JSON.stringify(safety))) return;

    const hardFailed = checks.filter(c => !c.passed && c.severity !== "warning").length;
    const passedCount = checks.filter(c => c.passed).length;
    if (safety.passed === true) {
      parts.badge.textContent = "PASSED";
      parts.badge.className = "dp-badge dp-pass";
    } else if (safety.passed === false) {
      parts.badge.textContent = "FAILED";
      parts.badge.className = "dp-badge dp-fail";
    } else {
      parts.badge.textContent = "NOT CHECKED YET";
      parts.badge.className = "dp-badge dp-none";
    }
    parts.safetyNote.textContent = checks.length ? passedCount + " of " + checks.length : "";
    parts.safetyLine.textContent = safety.passed === false
      ? hardFailed + " rule(s) broken. This plan was not sent to the tractors."
      : safety.passed === true
        ? "Every plan is checked against the nine rules before it is sent. No human approves it."
        : "";

    parts.checks.textContent = "";
    checks.forEach(function (check) {
      const row = el("div", "dp-check");
      const warning = !check.passed && check.severity === "warning";
      const mark = el("span", "dp-mark " + (check.passed ? "dp-ok" : warning ? "dp-warn" : "dp-no"),
                      check.passed ? "✓" : warning ? "!" : "✗");
      mark.setAttribute("aria-label", check.passed ? "passed" : warning ? "warning" : "failed");
      const body = el("div");
      body.append(el("div", null, check.id + ". " + check.name));
      if (check.detail) body.append(el("div", "dp-detail", check.detail));
      row.append(mark, body);
      parts.checks.append(row);
    });
  }

  function renderLog(foreman) {
    const lines = foreman.log || [];
    const last = lines.length ? lines[lines.length - 1] : null;
    if (!changed("log", JSON.stringify([lines.length, last]))) return;

    parts.logNote.textContent = lines.length ? "newest first" : "";
    parts.logBody.textContent = "";
    lines.slice(-LOG_LINES).reverse().forEach(function (line) {
      const row = el("div", "dp-log-line");
      const agent = el("span", "dp-agent", line.agent);
      agent.style.color = AGENT_COLORS[line.agent] || "inherit";
      row.append(el("span", "dp-time", line.clock), agent, el("span", null, line.message));
      parts.logBody.append(row);
    });
  }

  function renderReasoning(text) {
    if (!changed("reasoning", text || "")) return;
    if (text) {
      parts.reasoningBody.textContent = text;
      parts.reasoningBody.className = "dp-reasoning dp-filled";
      parts.reasoningNote.textContent = "";
    } else {
      parts.reasoningBody.textContent =
        "Nothing here yet. Every decision on this page is made by the planner and the rule " +
        "checker, with no AI model. The AI agent's reasoning will appear here.";
      parts.reasoningBody.className = "dp-reasoning";
      parts.reasoningNote.textContent = "coming in Phase 3";
    }
  }

  function render(state) {
    if (!parts) build();
    const foreman = (state && state.foreman) || {};
    renderChange(foreman);
    renderSafety(foreman);
    renderLog(foreman);
    // Phase 3 will send the model's text in state.foreman.reasoning.
    renderReasoning(typeof foreman.reasoning === "string" ? foreman.reasoning : null);
    parts.status.textContent = state && state.clock ? "As of " + state.clock : "";
    parts.status.className = "dp-status";
  }

  function showError() {
    if (!parts) build();
    parts.status.textContent = "Server not reachable";
    parts.status.className = "dp-status dp-error";
  }

  let polling = false;
  function poll() {
    if (polling) return;
    polling = true;
    fetch("/api/state", { cache: "no-store" })
      .then(function (response) {
        if (!response.ok) throw new Error("HTTP " + response.status);
        return response.json();
      })
      .then(render)
      .catch(showError)
      .finally(function () { polling = false; });
  }

  function start() {
    build();
    if (!window.DECISIONS_PANEL_NO_POLL) {
      poll();
      setInterval(poll, POLL_MS);
    }
  }

  window.DecisionsPanel = { render: render };

  if (document.readyState === "loading") {
    document.addEventListener("DOMContentLoaded", start);
  } else {
    start();
  }
})();
