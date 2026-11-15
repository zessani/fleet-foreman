/*
 * Decisions panel (built by Zayyan; lives with the other panels).
 *
 * Shows what the planner, the safety gate and the AI agents just decided, from
 * GET /api/state (CLAUDE.md 8c):
 *   - the latest plan change: headline, one line per changed job, the summary,
 *     how many jobs moved and how many stayed, and the re-plan time
 *   - the safety gate's result: passed or failed, and the nine checks
 *   - the activity log, newest first
 *   - the AI agents card: thinking sign, estimate accuracy, and each model call
 *     (state.foreman.ai and state.foreman.reasoning; both optional)
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
    "Orchestrator": "#dfe7e2", "Planner": "#dfe7e2", "Route finder": "#dfe7e2",
    "Safety gate": "#dfe7e2", "Recovery monitor": "#dfe7e2",
    "AI Recovery agent": "#f2f7f3", "AI Safety agent": "#f2f7f3",
  };
  const AI_COLOR = "#f2f7f3";
  const STATUS = {
    thinking: ["THINKING", "dp-st-think"], done: ["DONE", "dp-st-done"],
    fallback: ["FELL BACK TO RULES", "dp-st-fail"],
  };
  const ACTION_TAGS = { warn: "WARN", hold_job: "HOLD JOB", request_replan: "RE-PLAN" };

  // Sizes come from the box the panel sits in (container queries), so it also
  // works in a narrow sidebar and not only at full page width.
  const CSS = `
.dp-host { container-type: inline-size; }
.dp { --dp-panel: #19221e; --dp-panel2: #202c26; --dp-line: #34453c; --dp-text: #edf5ef;
  --dp-muted: #9fb0a5; --dp-accent: #dfe7e2; --dp-good: #dfe7e2; --dp-bad: #ef6d68;
  font-family: system-ui, -apple-system, "Segoe UI", sans-serif; color: var(--dp-text);
  display: grid; grid-template-columns: repeat(2, minmax(0, 1fr));
  gap: 12px; box-sizing: border-box; }
@container (max-width: 760px) {
  .dp { grid-template-columns: minmax(0, 1fr); }
  .dp-log-line { grid-template-columns: 4.2em minmax(0, 1fr); }
  .dp-banner-text { font-size: clamp(20px, 7cqi, 28px); }
  .dp-log-line > :last-child { grid-column: 1 / -1; }
}
.dp * { box-sizing: border-box; }
.dp-card { background: var(--dp-panel); border: 1px solid var(--dp-line); border-radius: 8px;
  padding: 14px 16px; min-width: 0; }
.dp-wide { grid-column: 1 / -1; }
.dp-title { margin: 0 0 10px; font-size: clamp(12px, 1.1cqi, 15px); font-weight: 700;
  letter-spacing: 0.06em; text-transform: uppercase; color: var(--dp-muted);
  display: flex; justify-content: space-between; gap: 12px; }
.dp-headline { font-size: clamp(20px, 2.4cqi, 34px); font-weight: 700; line-height: 1.2;
  margin: 0 0 8px; }
.dp-summary { font-size: clamp(16px, 1.7cqi, 24px); margin: 0 0 12px; }
.dp-chips { display: flex; flex-wrap: wrap; gap: 8px; margin: 0 0 12px; }
.dp-chip { background: var(--dp-panel2); border: 1px solid var(--dp-line); border-radius: 6px;
  padding: 6px 12px; font-size: clamp(14px, 1.4cqi, 20px); font-variant-numeric: tabular-nums; }
.dp-chip b { font-size: 1.25em; margin-right: 6px; }
.dp-chip.dp-strong { border-color: var(--dp-accent); color: var(--dp-accent); }
.dp-list { list-style: none; margin: 0; padding: 0; }
.dp-list li { padding: 8px 0; border-top: 1px solid var(--dp-line);
  font-size: clamp(15px, 1.5cqi, 21px); line-height: 1.35; }
.dp-badge { display: inline-block; border-radius: 6px; padding: 6px 14px; font-weight: 800;
  font-size: clamp(20px, 2.4cqi, 34px); letter-spacing: 0.03em; margin-bottom: 6px; }
.dp-pass { background: #26332c; color: var(--dp-good); border: 1px solid var(--dp-good); }
.dp-fail { background: #40201f; color: var(--dp-bad); border: 1px solid var(--dp-bad); }
.dp-none { background: var(--dp-panel2); color: var(--dp-muted); border: 1px solid var(--dp-line); }
.dp-line { font-size: clamp(14px, 1.4cqi, 19px); color: var(--dp-muted); margin: 0 0 10px; }
.dp-check { display: grid; grid-template-columns: 1.6em minmax(0, 1fr); gap: 4px 6px;
  padding: 6px 0; border-top: 1px solid var(--dp-line); font-size: clamp(15px, 1.7cqi, 23px); }
.dp-mark { font-weight: 800; font-size: 1.2em; line-height: 1.1; }
.dp-mark.dp-ok { color: var(--dp-good); }
.dp-mark.dp-no { color: var(--dp-bad); }
.dp-mark.dp-warn { color: var(--dp-accent); }
.dp-detail { color: var(--dp-muted); font-size: 0.88em; }
.dp-log { max-height: 46vh; overflow-y: auto; }
.dp-log-line { display: grid; grid-template-columns: 4.2em 10em minmax(0, 1fr); gap: 10px;
  padding: 6px 0; border-top: 1px solid var(--dp-line); font-size: clamp(13px, 1.3cqi, 18px);
  line-height: 1.35; }
.dp-time { color: var(--dp-muted); font-variant-numeric: tabular-nums; }
.dp-agent { font-weight: 700; }
.dp-banner { border: 2px solid var(--dp-accent); border-radius: 8px; padding: 10px 14px;
  margin: 0 0 12px; background: #26332c; animation: dp-pulse 1.6s ease-in-out infinite; }
.dp-banner-text { font-size: clamp(22px, 2.8cqi, 36px); font-weight: 800; color: var(--dp-accent); }
.dp-banner-sub { font-size: clamp(14px, 1.4cqi, 19px); color: var(--dp-text); margin-top: 4px; }
@keyframes dp-pulse { 0%, 100% { opacity: 1; } 50% { opacity: 0.55; } }
@media (prefers-reduced-motion: reduce) { .dp-banner { animation: none; } }
.dp-muted { color: var(--dp-muted); font-size: clamp(14px, 1.4cqi, 19px); margin: 0; }
.dp-call { border-top: 1px solid var(--dp-line); padding: 10px 0; font-size: clamp(14px, 1.4cqi, 19px);
  line-height: 1.4; overflow-wrap: anywhere; }
.dp-call > div { margin: 0 0 4px; }
.dp-call-head { display: flex; flex-wrap: wrap; align-items: center; gap: 4px 10px; }
.dp-call-agent { font-weight: 800; }
.dp-call-status { border-radius: 6px; padding: 2px 8px; font-weight: 800; font-size: 0.85em;
  letter-spacing: 0.03em; border: 1px solid; }
.dp-st-think { color: var(--dp-accent); border-color: var(--dp-accent); }
.dp-st-done { color: var(--dp-good); border-color: var(--dp-good); }
.dp-st-fail { color: var(--dp-bad); border-color: var(--dp-bad); }
.dp-call-bad { color: var(--dp-bad); }
.dp-tag { display: inline-block; border: 1px solid var(--dp-line); border-radius: 4px;
  padding: 0 6px; margin-right: 6px; font-size: 0.8em; font-weight: 800; color: var(--dp-accent); }
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
    root.classList.add("dp-host");
    root.textContent = "";
    const grid = el("div", "dp");
    root.append(grid);

    const change = card("Latest plan change", true);
    const headline = el("p", "dp-headline");
    const summary = el("p", "dp-summary");
    const chips = el("div", "dp-chips");
    const changes = el("ul", "dp-list");
    change.box.append(headline, summary, chips, changes);

    const tune = card("Planner self-tune", true);
    const tuneText = el("p", "dp-summary");
    tune.box.append(tuneText);
    tune.box.hidden = true;

    const safety = card("Safety gate");
    const badge = el("div", "dp-badge dp-none", "NOT CHECKED YET");
    const safetyLine = el("p", "dp-line");
    const checks = el("div");
    safety.box.append(badge, safetyLine, checks);

    const reasoning = card("AI agents");
    const reasoningBody = el("div");
    reasoning.box.append(reasoningBody);

    const log = card("Activity log", true);
    const logBody = el("div", "dp-log");
    log.box.append(logBody);

    const status = el("div", "dp-status");
    grid.append(change.box, tune.box, safety.box, reasoning.box, log.box, status);

    parts = { tuneBox: tune.box, tuneText, headline, summary, chips, changes, changeNote: change.note, badge, safetyLine,
              checks, safetyNote: safety.note, reasoningBody, reasoningNote: reasoning.note,
              logBody, logNote: log.note, status };
    lastKeys = {};
    renderReasoning(null, null);
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
    } else if (planNo === 1) {
      // The first plan lists one sentence per planned job and one per held job.
      const planned = (story.changes || []).length - (story.held || 0);
      parts.chips.append(chip(planned, "jobs in the first plan of the day"));
    }
    if (story.held) parts.chips.append(chip(story.held, "held", true));
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

  function renderTune(foreman) {
    // Older servers have no self-tune field; then the card stays hidden.
    const tune = foreman.selftune;
    const sentence = tune && typeof tune.sentence === "string" ? tune.sentence : "";
    if (!changed("tune", sentence)) return;
    parts.tuneText.textContent = sentence;
    parts.tuneBox.hidden = !sentence;
  }

  function renderSafety(foreman) {
    const safety = foreman.safety || {};
    const checks = safety.checks || [];
    if (!changed("safety", JSON.stringify([safety, foreman.plan_no]))) return;

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
      ? hardFailed + " rule(s) broken. The newest plan was rejected and not sent. Plan " +
        (foreman.plan_no || 0) + " is still in force."
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
      // Evidence only where it matters: a passed check is just its tick and name.
      if (check.detail && !check.passed) body.append(el("div", "dp-detail", check.detail));
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

  function line(cls, text) {
    return el("div", cls, text);
  }

  function answerLines(call) {
    const answer = call.answer;
    const out = [];
    if (answer == null) return out;
    if (call.agent === "AI Recovery agent" && typeof answer === "object") {
      const down = answer.expected_downtime_min != null ? answer.expected_downtime_min
                 : answer.downtime_min;
      out.push(line(null, "Severity: " + (answer.severity != null ? answer.severity : "unknown") +
                          " · Expected downtime: " + (down != null ? down : "unknown") + " min"));
      if (answer.reason) out.push(line(null, String(answer.reason)));
      if (answer.lesson) out.push(line("dp-detail", "Lesson: " + answer.lesson));
    } else if (call.agent === "AI Safety agent" && typeof answer === "object" &&
               Array.isArray(answer.concerns)) {
      if (!answer.concerns.length) {
        out.push(line(null, "No concerns."));
      } else {
        answer.concerns.forEach(function (concern) {
          const row = el("div");
          const action = String(concern.action || "");
          row.append(el("span", "dp-tag", ACTION_TAGS[action] || action.toUpperCase()));
          const text = concern.concern != null ? concern.concern : concern.text;
          row.append(document.createTextNode((text != null ? String(text) : "") +
                     (concern.job ? " (" + concern.job + ")" : "")));
          out.push(row);
        });
      }
    } else {
      out.push(line("dp-detail", JSON.stringify(answer)));
    }
    return out;
  }

  function callBlock(call) {
    const box = el("div", "dp-call");
    const head = el("div", "dp-call-head");
    const name = el("span", "dp-call-agent", call.agent || "AI agent");
    name.style.color = AGENT_COLORS[call.agent] || AI_COLOR;
    const status = STATUS[call.status] || [String(call.status || "").toUpperCase(), "dp-st-think"];
    head.append(name, el("span", "dp-time", call.clock || ""),
                el("span", "dp-time", "Plan " + (call.plan_no != null ? call.plan_no : "?")),
                el("span", "dp-call-status " + status[1], status[0]));
    box.append(head);
    if (call.input) box.append(line(null, "Read: " + call.input));
    const names = [];
    (call.tools || []).forEach(function (tool) {
      if (tool && tool.name != null && names.indexOf(tool.name) < 0) names.push(tool.name);
    });
    if (names.length) {
      const mode = String(call.tools_mode || "tools called");
      box.append(line(null, mode.charAt(0).toUpperCase() + mode.slice(1) + ": " + names.join(", ")));
    }
    answerLines(call).forEach(function (row) { box.append(row); });
    if (call.applied) box.append(line(null, "Did: " + call.applied));
    if (call.status === "fallback") {
      box.append(line("dp-call-bad", "Fell back to the rules: " + (call.error || "no answer")));
    }
    if (typeof call.ms === "number") box.append(line("dp-time", (call.ms / 1000).toFixed(1) + " s"));
    return box;
  }

  function renderReasoning(aiState, reasoningState) {
    const ai = aiState && typeof aiState === "object" ? aiState : {};
    const calls = Array.isArray(reasoningState) ? reasoningState.slice(0, 10) : [];
    if (!changed("reasoning", JSON.stringify([ai, calls]))) return;

    parts.reasoningNote.textContent = ai.enabled && ai.model ? String(ai.model) : "";
    const body = parts.reasoningBody;
    body.textContent = "";
    if (!ai.enabled) {
      body.append(el("p", "dp-muted",
        "AI agents are off. Every decision on this page is made by the planner and the safety gate."));
      return;
    }
    if (ai.thinking) {
      const banner = el("div", "dp-banner");
      banner.setAttribute("role", "status");
      banner.append(el("div", "dp-banner-text", "AI agent thinking…"),
                    el("div", "dp-banner-sub",
                       "The clock runs at 1× until it answers. The rule-based plan is already in force."));
      body.append(banner);
    }
    if (ai.estimates > 0 && typeof ai.downtime_gap_min === "number") {
      const chips = el("div", "dp-chips");
      chips.append(el("span", "dp-chip", "Downtime estimates off by " + ai.downtime_gap_min +
                      " min on average (" + ai.estimates + " so far)"));
      body.append(chips);
    }
    if (!calls.length && !ai.thinking) body.append(el("p", "dp-muted", "No AI agent calls yet."));
    calls.forEach(function (call) { if (call && typeof call === "object") body.append(callBlock(call)); });
  }

  function render(state) {
    if (!parts) build();
    const foreman = (state && state.foreman) || {};
    renderChange(foreman);
    renderTune(foreman);
    renderSafety(foreman);
    renderLog(foreman);
    renderReasoning(foreman.ai, foreman.reasoning);
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
