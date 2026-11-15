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
    thinking: ["Thinking", "dp-st-think"], done: ["Answered", "dp-st-done"],
    fallback: ["No answer", "dp-st-fail"],
  };
  const ACTION_TAGS = { warn: "Warning", hold_job: "Hold this job", request_replan: "Asked for a new plan" };
  // What each thing an AI agent can look up is, in plain words.
  const LOOKED_AT = {
    tractor_status: "the tractor's state", jobs_at_risk: "the jobs it had", past_lessons: "lessons from earlier faults",
    plan_summary: "the plan", tractor_history: "each tractor's day so far", recent_log: "the latest activity",
    past_concerns: "its own earlier warnings",
  };

  // Sizes come from the box the panel sits in (container queries), so it also
  // works in a narrow sidebar and not only at full page width.
  const CSS = `
.dp-host { container-type: inline-size; }
.dp { --dp-panel: #19221e; --dp-panel2: #202c26; --dp-line: #34453c; --dp-text: #edf5ef;
  --dp-muted: #9fb0a5; --dp-accent: #dfe7e2; --dp-good: #dfe7e2; --dp-bad: #ef6d68;
  font-family: system-ui, -apple-system, "Segoe UI", sans-serif; color: var(--dp-text);
  display: grid; grid-template-columns: repeat(2, minmax(0, 1fr));
  gap: 12px; box-sizing: border-box; align-items: start; }
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
/* A folded card shows its title and a one-line result; a click opens the rest. */
.dp-fold > summary { cursor: pointer; margin: 0; list-style: none; align-items: baseline; }
.dp-fold > summary::-webkit-details-marker { display: none; }
.dp-fold > summary > span:first-child { white-space: nowrap; }
.dp-fold > summary > span:first-child::before { content: "\\25B8"; display: inline-block; width: 1.3em; }
.dp-fold[open] > summary > span:first-child::before { content: "\\25BE"; }
.dp-fold[open] > summary { margin-bottom: 10px; }
.dp-fold-note { text-transform: none; letter-spacing: 0; font-weight: 600; color: var(--dp-text);
  min-width: 0; overflow: hidden; text-overflow: ellipsis; white-space: nowrap; }
.dp-fold-note.dp-note-bad { color: var(--dp-bad); }
.dp-each { margin: 0; }
.dp-each > summary { cursor: pointer; color: var(--dp-muted); font-size: clamp(14px, 1.4cqi, 19px);
  padding: 4px 0; }
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

  // folded: the card starts closed and opens like a dropdown. Its note stays visible
  // while it is closed, so the result can be read without opening it.
  function card(title, wide, folded) {
    const box = el(folded ? "details" : "section",
                   "dp-card" + (wide ? " dp-wide" : "") + (folded ? " dp-fold" : ""));
    const heading = el(folded ? "summary" : "h2", "dp-title");
    const name = el("span", null, title);
    const note = el("span", folded ? "dp-fold-note" : null);
    heading.append(name, note);
    box.append(heading);
    if (!folded) return { box, note, body: box };
    const body = el("div");
    box.append(body);
    return { box, note, body };
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
    const each = el("details", "dp-each");
    const eachTitle = el("summary");
    each.append(eachTitle, changes);
    change.box.append(headline, summary, chips, each);

    const tune = card("Planner self-tune", true);
    const tuneText = el("p", "dp-summary");
    tune.box.append(tuneText);
    tune.box.hidden = true;

    const safety = card("Safety gate", false, true);
    const badge = el("div", "dp-badge dp-none", "NOT CHECKED YET");
    const safetyLine = el("p", "dp-line");
    const checks = el("div");
    safety.body.append(badge, safetyLine, checks);

    const reasoning = card("AI agents", false, true);
    const reasoningBody = el("div");
    reasoning.body.append(reasoningBody);

    const log = card("Activity log", true, true);
    const logBody = el("div", "dp-log");
    log.body.append(logBody);

    const status = el("div", "dp-status");
    grid.append(change.box, tune.box, safety.box, reasoning.box, log.box, status);

    parts = { tuneBox: tune.box, tuneText, headline, summary, chips, changes, each, eachTitle,
              safetyBox: safety.box, changeNote: change.note, badge, safetyLine,
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
      if (retimed) parts.chips.append(chip(retimed, "of those with new times"));
    } else if (planNo === 1) {
      // The first plan lists one sentence per planned job and one per held job.
      const planned = (story.changes || []).length - (story.held || 0);
      parts.chips.append(chip(planned, "jobs in the first plan of the day"));
    }
    if (story.held) parts.chips.append(chip(story.held, "waiting for a tractor", true));
    if (story.ms != null) {
      const ms = el("span", "dp-chip");
      ms.append(el("b", null, String(story.ms) + " ms"),
                document.createTextNode(planNo > 1 ? "to make the new plan" : "to plan the day"));
      parts.chips.append(ms);
    }

    parts.changes.textContent = "";
    const sentences = story.changes || [];
    sentences.forEach(function (sentence) {
      parts.changes.append(el("li", null, sentence));
    });
    // A short list is shown; a long one stays folded until someone asks for it.
    parts.each.hidden = !sentences.length;
    parts.each.open = sentences.length > 0 && sentences.length <= 3;
    parts.eachTitle.textContent = "Each job (" + sentences.length + ")";
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
    parts.safetyNote.textContent = safety.passed === true
      ? "Passed, " + passedCount + " of " + checks.length + " checks"
      : safety.passed === false ? "Failed, " + hardFailed + " rule(s) broken" : "Not checked yet";
    parts.safetyNote.classList.toggle("dp-note-bad", safety.passed === false);
    if (safety.passed === false) parts.safetyBox.open = true;  // a failure is never hidden
    parts.safetyLine.textContent = safety.passed === false
      ? hardFailed + " rule(s) broken. The newest plan was rejected and not sent. Plan " +
        (foreman.plan_no || 0) + " is still in force."
      : safety.passed === true
        ? "Every plan is checked against these " + checks.length + " rules before it is sent. No human approves it."
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

    parts.logNote.textContent = last ? last.clock + "  " + last.agent + ": " + last.message : "Nothing yet";
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
      const severity = answer.severity != null ? String(answer.severity) : "unknown";
      const fault = severity.charAt(0).toUpperCase() + severity.slice(1) + " fault. ";
      out.push(line(null, fault + (answer.out_for_day ? "Out for the rest of the day."
        : "Expected back in about " + (down != null ? down : "?") + " min.")));
      if (answer.reason) out.push(line(null, String(answer.reason)));
      if (answer.lesson) out.push(line("dp-detail", "Lesson kept: " + answer.lesson));
    } else if (call.agent === "AI Safety agent" && typeof answer === "object" &&
               Array.isArray(answer.concerns)) {
      if (!answer.concerns.length) {
        out.push(line(null, "No concerns."));
      } else {
        answer.concerns.forEach(function (concern) {
          const row = el("div");
          const action = String(concern.action || "");
          row.append(el("span", "dp-tag", ACTION_TAGS[action] || action));
          const text = concern.concern != null ? concern.concern : concern.text;
          row.append(document.createTextNode((text != null ? String(text) : "") +
                     (concern.job ? " (" + concern.job + ")" : "")));
          out.push(row);
        });
      }
    }
    return out;
  }

  function callBlock(call) {
    const box = el("div", "dp-call");
    const head = el("div", "dp-call-head");
    const name = el("span", "dp-call-agent", call.agent || "AI agent");
    name.style.color = AGENT_COLORS[call.agent] || AI_COLOR;
    const status = STATUS[call.status] || ["Thinking", "dp-st-think"];
    head.append(name, el("span", "dp-time", call.clock || ""),
                el("span", "dp-call-status " + status[1], status[0]));
    box.append(head);
    if (call.input) box.append(line(null, "Asked about: " + call.input));
    const looked = [];
    (call.tools || []).forEach(function (tool) {
      const what = tool && LOOKED_AT[tool.name];
      if (what && looked.indexOf(what) < 0) looked.push(what);
    });
    if (looked.length) box.append(line("dp-detail", "Looked at: " + looked.join(", ") + "."));
    answerLines(call).forEach(function (row) { box.append(row); });
    if (call.applied) box.append(line(null, "Foreman did: " + call.applied));
    if (call.status === "fallback") {
      box.append(line("dp-call-bad", "No answer in time. Foreman carried on with its own rules."));
    }
    if (typeof call.ms === "number" && call.status === "done") {
      box.append(line("dp-time", "Answered in " + (call.ms / 1000).toFixed(1) + " s"));
    }
    return box;
  }

  function renderReasoning(aiState, reasoningState) {
    const ai = aiState && typeof aiState === "object" ? aiState : {};
    const calls = Array.isArray(reasoningState) ? reasoningState.slice(0, 10) : [];
    if (!changed("reasoning", JSON.stringify([ai, calls]))) return;

    // The folded card still says the one thing that matters: the latest answer and what was done.
    const latest = calls.length && calls[0] && typeof calls[0] === "object" ? calls[0] : null;
    parts.reasoningNote.textContent = !ai.enabled ? "Off"
      : ai.thinking ? "Thinking…"
      : latest ? String(latest.agent || "AI agent") + ": " + (latest.applied || "no answer")
      : "Not asked yet";
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
                       "The clock is slowed until it answers. Foreman's own plan is already in use."));
      body.append(banner);
    }
    if (ai.estimates > 0 && typeof ai.downtime_gap_min === "number") {
      const chips = el("div", "dp-chips");
      chips.append(el("span", "dp-chip", "Repair-time estimates off by " + ai.downtime_gap_min +
                      " min on average (" + ai.estimates + " so far)"));
      body.append(chips);
    }
    if (!calls.length && !ai.thinking) body.append(el("p", "dp-muted", "The AI agents have not been asked anything yet."));
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
