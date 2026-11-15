# Foreman: build brief for Claude Code

Read this whole file before writing code. It is the shared context for three
people, each running their own Claude Code session on the same repo.

## 1. The situation

- **Event:** Hack Arizona AgX hackathon, Tucson, Sunday Nov 15, 2026.
- **Challenge:** "Run the Fleet", presented by American Tractor Company (ATC).
- **Hard deadline:** hand-in at **5:00 PM today**. Code freeze at 4:00 PM.
- **Team:** Zayyan, Ahmed, Vishva. See section 9 for who owns what.
- **Goal:** win. Build one complete flow that works without a stumble, and
  prove it with numbers. Few things done very well beat many things.

Time is the main constraint. Prefer simple, readable code that runs today
over clever code. Do not add features that are not in this brief.

## 2. The challenge in one paragraph

Autonomous tractors can work a field alone, but a person still decides which
tractor does which job, in what order, and what happens when a machine breaks
or a road is blocked. Build a coordinator that assigns jobs to a small fleet
of simulated tractors (2 to 3 tractors, 5 to 10 fields) and re-plans when
conditions change.

**Must-haves from the challenge page**
1. Take a job list from an operator.
2. Assign compatible tractors: respect capabilities, implements, fuel, deadlines.
3. Show progress on a dashboard or map.
4. Recover from a breakdown: reassign unfinished work with no conflicting
   assignments and no lost completed work.
5. Explain the new plan in short, plain language.

**Hand-in items**
- Link to the code
- Short README: how to run it, and whether we used AgOpenWeb or our own simulation
- Scenario file (tractors, fields, jobs)
- Event log from the demo run
- Comparison against the simple baseline: jobs completed, travel time, idle time

## 3. How we are judged

**Written rubric (20 points)**

| Row | Points | Top marks for |
|---|---|---|
| Explanations and demo | 5 | Clear plan explanations and a clear demo |
| Constraint compliance | 4 | No violations in any scenario |
| Completed work | 4 | More useful work than the starter dispatcher |
| Operational efficiency | 4 | Less travel and idle time than the starter |
| Adaptation to events | 3 | Replans fast, no lost or duplicated work |

Tiebreaker: "Would a farm operator trust this plan?"

**What ATC's co-founder said in person** (he may or may not be a judge)
- AgOpenGPS already plans the path inside a field. Build the layer above it.
- Pick the right implement for each tractor and task.
- With 2 to 3 tractors, keep them from crossing paths.
- Agents should send correct messages into AgOpenGPS over its UDP network.
  Our own simulator and our own GUI are also allowed.
- **Zero human in the loop.** End to end by agents.
- He judges agents on performance, tool use, and memory, and on how 5 to 6
  agents hand work to each other.
- Breakdown recovery with no human earns extra credit.
- Practical beats flashy. A measured large gain in speed or cost is the wow.
- Presentation and architecture detail matter most.
- Weather and physics do not matter for this track. A 3D view is optional.

## 4. The product

**Foreman** is an orchestration layer that runs a fleet of autonomous
tractors with no human in the loop. Five rule-based roles and two AI agents
work together; rules decide and verify, the AI agents only add caution. It assigns jobs, picks the implement,
sends each tractor to work, and repairs the plan by itself when a tractor
breaks down.

- **User:** a farm operations manager running 2 to 20 machines, who today
  coordinates by phone and whiteboard.
- **Goal of the system:** finish the right work on time with the least wasted
  machine time. Priority order: on time first, machines working second, low
  cost third.

**The product bar**
- Complete: one flow works end to end with no dead buttons.
- Accurate, with proof: zero rule violations across many random test farms.
- Fast, with proof: re-plan time shown live in milliseconds.
- Ready to run: one command starts it with a sample farm loaded.
- Runs offline. No cloud call is needed for the core. The two AI agents are optional and need a model token.

## 5. Farming in five minutes

- A **tractor** is only the engine. The **implement** behind it does the work:
  sprayer, mower, disc (tillage), planter.
- Changing implements happens at the yard and takes time. So a tractor should
  stay on one type of work as long as it can.
- Bigger implements need more horsepower.
- A **job** is one operation on one field, such as "spray Field 7". Progress
  is the share of the field covered.
- Fields are scattered. Driving between them is dead time.
- Jobs have real deadlines and priorities.
- One machine per field at a time.
- Some jobs in a field must follow others (till before plant).
- After a breakdown mid-field, the next tractor does **only the remainder**.
  Redoing the finished part is double application. Skipping the rest is lost work.

We plan travel **between** fields. We do not plan the passes **inside** a
field. AgOpenGPS does that.

## 6. Architecture

Seven roles share one memory and call fixed tools. Five are rule-based code.
Two are AI agents, which means a real model is behind them. Roles decide what
to do. Tools do the exact work, so plans are fast and never break a rule.
The model never assigns jobs and nothing waits on it.

| Role | AI agent? | Job | Tool it calls |
|---|---|---|---|
| Orchestrator | No | Runs the loop, routes each event to the right roles | Task queue, the ROUTES table |
| Planner (`FleetPlanner`) | No | Decides which tractor does which job, in what order, and with which implement | Planner (solver) in `planner.py`, implement picker in `model.py` |
| Route finder | No | Routes between fields, stops tractors sharing a field | `foreman/nav` RoadMap, field conflict checker |
| Safety gate | No | Passes or rejects every plan. Replaces human approval | Rule verifier (`safety.py`, nine checks, does not import the planner) |
| Recovery monitor | No | Watches tractors, spots a failure, starts the re-plan, records the incident | Tractor status feed reader (`watch.py`) |
| AI Recovery agent | Yes | Reads a broken tractor's fault report. Returns severity, expected downtime in minutes, `out_for_day` (true when the tractor will not work again today), a reason and a one-line lesson | Read-only: `tractor_status`, `jobs_at_risk`, `past_lessons` |
| AI Safety agent | Yes | Reviews a plan the safety gate already passed. Returns a list of concerns, each `warn`, `hold_job` or `request_replan` | Read-only: `plan_summary`, `tractor_history`, `recent_log`, `past_lessons` |

Log lines carry the role name in the `agent` key (the key name is kept for
compatibility): `Orchestrator`, `Planner`, `Route finder`, `Safety gate`,
`Recovery monitor`, `AI Recovery agent`, `AI Safety agent`.

**The loop**
1. Jobs arrive (or an event happens).
2. The planner and the route finder build a plan. The planner picks each implement with the implement picker tool.
3. The safety gate checks it. A plan that fails a hard check is never sent.
   If it fails, the orchestrator plans once more from scratch. If that fails too, nothing is sent.
4. The plan is dispatched to the tractors.
5. The recovery monitor watches for trouble.
6. On a breakdown or blocked road, go to step 2 with finished work frozen.
7. Beside the loop: after a breakdown with a fault report, the AI Recovery agent
   estimates the downtime. After the safety gate passes a plan, the AI Safety
   agent reviews it. Their answers come back later as follow-up events (below).

**Event routing table** (explicit in code as `ROUTES` in `foreman/roles/orchestrator.py`, it is part of the pitch)

| Event | Roles, in order |
|---|---|
| jobs_submitted | Planner, Route finder, Safety gate, dispatch |
| breakdown | Recovery monitor, Planner, Route finder, Safety gate, dispatch |
| repaired | Recovery monitor, Planner, Route finder, Safety gate, dispatch |
| road_blocked / road_opened | Route finder, Planner, Route finder, Safety gate, dispatch |
| priority_changed / job_added | Planner, Route finder, Safety gate, dispatch |
| drift (a job finished 15+ min behind plan) | Recovery monitor, Planner, Route finder, Safety gate, dispatch |
| downtime_estimate (AI Recovery agent answered) | Planner, Route finder, Safety gate, dispatch |
| downtime_expired (the tractor is not back 10 min after the estimate) | Planner, Route finder, Safety gate, dispatch |
| ai_caution (AI Safety agent asked for a re-plan) | Planner, Route finder, Safety gate, dispatch |
| hold_released (a 30 minute hold ended) | Planner, Route finder, Safety gate, dispatch |
| job_done / work_sample | Recovery monitor (logs it), no re-plan. work_sample also goes to the learning loop |

Events seen in the same status reading share one re-plan. The recovery monitor
reads the tractor status feed once per simulated minute. A failure is a
reported fault, or a tractor that says it is working but makes no progress for
5 minutes.

**Rules for the AI agents, enforced in code**
- The model never assigns jobs. The planner does.
- A re-plan never waits on the model. The rule-based plan is dispatched first,
  and the model's answer is applied when it arrives.
- Each call has a 15 second limit. The client enforces it, and `AiCrew`
  abandons a call 2 seconds later as a backstop.
- Any error, timeout or bad JSON falls back to the rules and is logged.
- The AI Safety agent is asked only after the safety gate has passed a plan,
  once per plan. It can only add caution. Any plan it triggers must pass the
  safety gate again. An answer for a plan that has been replaced is discarded.
- The clock drops to 1x while an AI agent is thinking (`clock_speed`).

**How answers change the plan**
- An expected downtime goes to `planner.plan_with_return`. It scores moving the
  work now and waiting for the tractor, both in the same world. A job is held
  for the tractor only if waiting is cheaper (by more than `WAIT_MARGIN`,
  10 minutes) and, in the waiting schedule, the job and every job that depends
  on it still finish at least `HOLD_DEADLINE_MARGIN_MIN` (30 minutes) before
  their deadlines. A job that fails the second test is moved now, even if
  others are held. If the tractor is not back 10 minutes after the estimate,
  the held work is moved.
- "Out for the day" (`out_for_day: true`, or an estimate that reaches past the
  end of the working day) holds nothing and sets no timer. The rule-based
  re-plan that already moved the work stands, and the log says so ("Cedar is
  out for the day. Its work stays with the other tractors."). A repair later
  that day still goes through the normal `repaired` re-plan.
- The real outcome of every estimate is saved as one lesson: the real repair
  time when the tractor comes back, a missed estimate when it runs out, and at
  the end of the day either "not repaired that day" or, for an out-for-the-day
  answer, that the answer proved right.
- `request_replan` with the one caution `spread_dependent_jobs` makes the
  planner add a cost (`CHAIN_COST`) for keeping a job and the job that depends
  on it on one tractor.
- `hold_job` holds one job for 30 minutes.
- `warn` is only logged and shown.

**Memory, five kinds**
- **World state:** where each tractor is, what is done, what is half done.
- **Event log:** every decision and its reason, in order. Append only. This is
  also the hand-in log. Save as JSON Lines (`runs/event_log.jsonl`). A new day starts a new file.
- **Incidents and downtime estimates:** one record per breakdown, and the AI
  Recovery agent's estimate against the real repair time. Current day only.
- **AI lessons:** one-line lessons, written only by the AI Recovery agent, in
  `runs/lessons.jsonl`. Kept across days. The AI Safety agent reads them.
- **Learned work rates:** a running factor per tractor, implement and field, in
  `runs/learned.json`. Kept across days. See section 16.

**The five rule-based roles are plain Python modules** (in `foreman/roles/`)
with one clear job each. They need no model. The two AI agents are
`foreman/ai_recovery.py` and `foreman/ai_safety.py`, started by `foreman/ai_desk.py`
through `AiCrew`. When the model is off, unreachable or wrong, every decision is rule-based.

**Planner self-tune** (`foreman/selftune.py`, no model). When a live day ends, Foreman replays
the days it has already run (the latest 12) with a few idle weights around the current one
(for 0.7: 0.5, 0.6, 0.85, 1.0), using the sweep code. It adopts a candidate only if it finishes
at least as many jobs on time, wastes no more minutes, is clearly better on one of the two, and
breaks no rule. It never adopts on fewer than 5 past days. Every run is logged in
`runs/selftune/selftune.jsonl` and the latest sentence is on the decisions panel. Trial weights
are seen only by the self-tune's own thread, so a live plan never uses one.

**AI Safety agent memory.** Each warning or hold it raises is saved with what Foreman did and,
at the end of the day, what happened after (jobs on time, rule violations, whether the job or
tractor it named had trouble) in `runs/safety_concerns.jsonl`. It reads them on later reviews
through the read-only tool `past_concerns`. It still cannot approve anything.

**Resilience step (Ahmed, `foreman/resilience.py`).** After the normal search, the planner may
pick a relocate or swap alternative within 5% of the cheapest plan if it cuts an estimated
breakdown damage by at least 5%. It is off during recovery events. Measured on the 196 test
days it changes no result (same jobs on time, wasted minutes and zero violations).

**A scripted breakdown with no report** keeps the tractor out all day. With the AI agents on,
the AI Recovery agent is given the sample report for a fault that is not repaired today.

**Safety rules are fixed.** Learning may tune estimates. It may never relax a rule.
The AI Safety agent can ask for more caution. It cannot remove a check.

## 7. Tech choices

- Python 3.10+ for the engine and API. FastAPI + uvicorn.
- Dashboard: one HTML page with plain JavaScript and Canvas. No build step, no
  CDN, so it works offline.
- The dashboard polls `GET /api/state` a few times per second. No WebSockets.
- Planner: regret insertion plus local search over an exact route evaluator.
  Google OR-Tools is optional as a second opinion. Do not block on it.
- Tests: pytest for the rules and the planner.
- One command to run: `python -m foreman.server` then open `http://localhost:8000`.
- Ask before adding any other dependency. The model needs none.
- **The model (AI Recovery agent and AI Safety agent):** Claude Haiku 5.5, reached
  through the Claude Code command line (`claude`) in non-interactive mode. It
  uses a token in `.env` (`CLAUDE_CODE_OAUTH_TOKEN`; the file is git-ignored and
  was never committed). Everything sits behind one interface, `ModelClient` in
  `foreman/ai_base.py`, so switching to a normal API key is a change to one file
  (`foreman/llm.py`). The model reads data through a small read-only tool server
  (`foreman/tool_server.py`).
- Settings: `FOREMAN_LLM_MODEL` picks the model. `FOREMAN_AI=0` forces the AI
  agents off. `FOREMAN_AI_TOOLS=prompt` passes the data in the prompt instead of
  through the tool server.
- The AI agents are off when there is no token, when the `claude` command is not
  installed, or when `FOREMAN_AI=0`. Then every decision is rule-based.

## 8. The three contracts

Everyone builds to these. Change them only by agreement, in this file.

### 8a. Farm file (JSON)

Time is minutes from the start of the working day. Distance is km. Area is hectares.

```json
{
  "name": "Desert Bloom Farm",
  "day_start_min": 360,
  "horizon_min": 660,
  "nodes": [
    {"id": "YARD", "kind": "depot", "name": "Yard", "x": 0.0, "y": 0.0},
    {"id": "F1", "kind": "field", "name": "North 40", "x": 1.2, "y": 2.4, "area_ha": 16, "crop": "lettuce"},
    {"id": "X1", "kind": "junction", "x": 1.0, "y": 1.0}
  ],
  "roads": [["YARD", "X1"], ["X1", "F1"]],
  "implements": [
    {"id": "sprayer_18", "type": "spray", "name": "18 m boom sprayer",
     "width_m": 18, "speed_kmh": 12, "min_hp": 100, "efficiency": 0.8}
  ],
  "tractors": [
    {"id": "T1", "name": "Atlas", "hp": 180,
     "implements": ["disc_6", "mower_4"], "mounted": "disc_6",
     "speed_kmh": 28, "fuel_capacity": 300, "fuel": 300,
     "burn_km": 0.6, "burn_work": 0.45, "node": "YARD"}
  ],
  "jobs": [
    {"id": "J1", "type": "spray", "field": "F1", "priority": 5,
     "deadline": 240, "release": 0, "after": null}
  ],
  "truth": {"T2|mow|F6": 0.8},
  "events": [{"at": 215, "type": "breakdown", "tractor": "T2"}]
}
```

Field notes
- `kind` is `depot`, `field`, or `junction`. Exactly one depot (the yard).
- `roads` are two-way links between node ids. If `roads` is missing, every
  pair of nodes is directly linked.
- `priority` runs 1 (low) to 5 (critical). `deadline` and `release` are minutes.
- `after` names a job that must finish first. Optional.
- A job's area defaults to its field's `area_ha`.
- `truth` holds hidden real-world speed factors the planner must learn
  (key is `tractor|job type|field`, `*` allowed). Only the simulator reads it.
- `events` are scripted demo events. Optional.

### 8b. Tractor interface

Every tractor sits behind the same three calls. The simulator implements it
now. An AgOpenGPS tractor or a real ATC tractor can implement it later.

```python
class TractorAdapter(Protocol):
    def status(self) -> dict: ...
        # {id, x, y, node, heading, state, job, pct, fuel, mounted}
        # state: idle | traveling | servicing | working | waiting | broken
    def dispatch(self, stops: list[dict], plan_no: int) -> None: ...
        # Replace the tractor's to-do list. Work in progress is not interrupted.
        # stop: {job, field, implement, via_yard, swap, refuel, path: [node ids]}
        # plan_no counts up with every dispatched plan. A command with a number
        # lower than one the tractor already accepted is ignored.
    def stop(self) -> None: ...
```

### 8c. Dashboard state: `GET /api/state`

```json
{
  "now": 215.0, "clock": "09:35", "running": true, "speed": 4, "clock_speed": 4,
  "farm": {"name": "...", "nodes": [], "roads": [], "blocked": [["X1", "F1"]],
           "implements": [], "day_start_min": 360, "horizon_min": 660},
  "foreman": {
    "tractors": [{"id": "T1", "name": "Atlas", "x": 1.1, "y": 2.0, "status": "working",
                  "job": "J3", "fuel_pct": 72, "mounted": "disc_6", "color": "#E8A33D"}],
    "jobs": [{"id": "J1", "type": "spray", "field": "F1", "priority": 5,
              "deadline": 240, "pct": 60, "status": "active", "tractor": "T2"}],
    "plan": [{"job": "J1", "tractor": "T2", "implement": "sprayer_18",
              "start": 30, "finish": 85, "late_min": 0, "path": ["YARD", "X1", "F1"]}],
    "plan_no": 3,
    "totals": {"jobs_done": 4, "on_time": 4, "travel_min": 61, "idle_min": 12,
               "unassigned_min": 40, "service_min": 25,
               "wasted_min": 98, "finish_min": 402,
               "working_share": 78.5, "km": 24.1, "violations": 0},
    "explanation": {"headline": "...", "summary": "...", "changes": [], "ms": 38},
    "safety": {"passed": true, "checks": []},
    "log": [],
    "ai": {"enabled": true, "thinking": false, "model": "claude-haiku-5-5 via Claude Code",
           "estimates": 1, "downtime_gap_min": 5.0},
    "reasoning": []
  },
  "baseline": {"tractors": [], "jobs": [], "plan": [], "totals": {}}
}
```

Shapes inside the state
- Job `status`: `pending | active | done | held`. A half-done job waiting for
  a new tractor is `pending` with its `pct` kept.
- Tractor `status`: `idle | traveling | servicing | working | waiting | broken`.
- `explanation.changes`: a list of plain sentences, one per changed job.
- `safety.checks[]`: `{id, name, passed, severity, detail}`. `id` is the rule
  number from section 12. `severity` is `hard` or `warning`.
- `log[]`: `{t, clock, agent, event, message}`. `t` is minutes from the start
  of the day. `agent` is one of the seven role names (section 6).
- `speed` is simulated minutes per real second: 1, 4, 16, or 60. `clock_speed`
  is the speed the clock really runs at: 1 while an AI agent is thinking,
  otherwise `speed`.
- `farm.implements` is the implement list from the farm file (section 8a).
  `farm.day_start_min` and `farm.horizon_min` are the farm file values.
- `foreman.ai`: `{enabled, thinking, model, estimates, downtime_gap_min}`.
  `enabled` is false when the AI agents are off. `model` is null then.
  `estimates` counts AI Recovery agent downtime estimates scored today.
  `downtime_gap_min` is the average gap in minutes between those estimates and
  the real repair time, or null.
- `foreman.reasoning`: AI agent calls, newest first (the latest 10). Each is
  `{agent, plan_no, at, clock, input, status, tools, tools_mode, answer, applied, ms, error}`.
  `agent` is `AI Recovery agent` or `AI Safety agent`. `status` is `thinking`,
  `done` or `fallback`. `tools` is `[{name, args}]`, the tools the model called.
  `tools_mode` is `tools called` or `data read` (data passed in the prompt).
  `answer` is the checked answer or null. `applied` is what Foreman did with it.
  `error` says why a call fell back.
- Job and implement `type` values: `spray | mow | till | plant`.
- `totals` meanings are in section 13. `baseline` has the same `tractors`,
  `jobs`, `plan`, `plan_no`, and `totals` as `foreman`.
- Extra keys, safe to ignore: top level `finished` (the day is over) and
  `scenario` (file name without `.json`); each tractor has `heading` (degrees)
  and `node`; a `held` job has `reason`.
- `safety.passed` is `null` until the Safety gate has checked a plan.

Event payloads for `POST /api/event`
- `{"type": "breakdown", "tractor": "T2"}`. Add `"silent": true` for a tractor
  that keeps reporting "working" but stops making progress. Optional fields:
  `report` (the fault report as text) and `repair_after_min` (the real repair
  time, used only by the simulator; null keeps the tractor out all day). A
  breakdown sent without a `report` gets the next sample from `foreman/faults.py`.
- `{"type": "repair", "tractor": "T2"}`
- `{"type": "block_road", "a": "X1", "b": "F1"}`
- `{"type": "open_road", "a": "X1", "b": "F1"}`
- `{"type": "set_priority", "job": "J4", "priority": 5}`
- `{"type": "add_job", "job": {...a job as in section 8a...}}`

The same shapes, plus `at` in minutes, are used in a farm file's `events` list.
The reply is `{"ok": true, "at": 94.2}` or `{"ok": false, "error": "..."}`.
A breakdown reply also carries `report` when the fault has one, so the caller
sees the text that was used. The simulated tractor comes back by itself after
`repair_after_min`, in both worlds. The AI Recovery agent reads the report and
estimates the downtime.
A breakdown or repair only changes the tractor. The recovery monitor notices it
on the next status reading, so the clock must be running for the re-plan.
A log line may also carry `data`, a small object with the numbers behind the
message (for example `plan_no`, `ms`, `detection_min`).
`explanation` also has `safety` (the safety gate line) and the counts `moved`,
`retimed`, `unchanged`, `held`.

Report: `GET /api/report`
```json
{
  "scenario": "example_farm", "farm": "...", "jobs": 5, "plan_ms": 0.5,
  "main": {"hidden_factors": false, "foreman": {}, "baseline": {}, "difference": {}},
  "with_hidden_factors": {"hidden_factors": true, "foreman": {}, "baseline": {}, "difference": {}},
  "live": {"clock": "09:35", "finished": false, "foreman": {}, "baseline": {}, "difference": {}}
}
```
- `foreman` and `baseline` are `totals` objects. `difference` is Foreman minus baseline.
- `main` is a full day run off screen with hidden speed factors off. It is the
  headline comparison. `with_hidden_factors` is the same day with them on.
  `live` is the run on screen, which may still be in progress. All three get
  the same events, including ones sent to `POST /api/event`.
- `replans` lists every dispatched plan of the live run:
  `{t, plan_no, ms, events, moved, retimed, unchanged, held, detection_min}`.
  `ms` is the time from the event reaching the Orchestrator to dispatch.

Other endpoints
- `GET /api/learning/chart`: the five-day learning curve for the chart only,
  `{seed, noise_pct, hidden_factors, days: [{day, gap_pct, samples, violations, jobs_done, on_time}], factors}`.
  It uses its own seeded hidden factors (10 to 25% off the catalog rate per
  field) and 3% day-to-day noise, with a fresh memory that is never saved, so
  no other number depends on it. Sample farm, seed 7: 8.9%, 1.9%, 2.2%, 2.0%, 1.1%.

Commands
- `python -m foreman.server`: the app, on http://localhost:8000.
- `python -m foreman.handin`: runs the full demo day off screen with the
  scripted breakdown (no model, same result every time) and writes `handin/`:
  `scenario.json`, `event_log.jsonl`, `comparison.md` and `.json`,
  `explanations.md`, `README.md`.
- `python -m foreman.lessons reset | seed | show`: puts the AI agents' lessons
  file in a known state before a demo (empty, or one earlier day of lessons).
  Press Reset on the dashboard afterwards.
- `python -m foreman.sweep`: the comparison across every test day.

- `python -m foreman.selftune report [--days N] | reset | show`: the planner's self-tune (below).
  `reset` puts the saved settings back to the defaults before a demo.

- `POST /api/control` with `{action: start|pause|reset, speed}`
- `POST /api/scenario/random` with `{seed}` (optional): builds a farm with `tools/make_farm.py`,
  passes it through `check_farm`, loads it and resets the day. The state then carries `seed`
  and `scenario` is `random_<seed>`.
- `GET /api/selftune`: `{settings, latest, min_days, history}`. `state.foreman.selftune` is
  `{sentence, decision, current, days}` or null.
- `POST /api/scenario` with `{name}` to load a farm file
- `POST /api/event` with `{type: breakdown|repair|block_road|open_road|set_priority|add_job, ...}`
- `GET /api/why?job=J1`: `{job, label, status, chosen, held_reason, others, text}`.
  `chosen` is the tractor that got the job (or null). `others` is
  `[{tractor, name, reason}]` for every other tractor. `text` is plain sentences.
  An unknown job gives `{"ok": false, "error": "..."}`.
- `GET /api/learning`: `{days: [{day, gap_pct, samples}], factors: [{tractor, implement,
  field, factor, samples, hectares}], days_run, lessons: [...],
  downtime_estimates: {count, avg_gap_min, items}}`. `lessons` are the AI
  Recovery agent's lessons (latest 30). `downtime_estimates.items` are today's
  estimates against the real repair time.
- `POST /api/learning/run` with `{days}` (a whole number, 1 to 30): runs that many
  quiet days off screen with hidden speed factors on and learns from them.
  Reply `{"ok": true, "days": [{day, gap_pct, samples, violations, jobs_done, on_time, finish_min}]}`.
- `GET /api/log` (JSON Lines), `GET /api/report` (baseline comparison, shape above;
  it runs with no model and no learning, so it is repeatable)

Every event is applied to both the Foreman run and the baseline run at the
same simulated time, so the comparison is fair.

### 8d. Road map: `foreman.nav.RoadMap` (proposed, pending Ahmed)

The planner asks for distances thousands of times per plan, so routing is a
small class built once from the farm, not a function that searches each time.

```python
from foreman.nav import RoadMap

class RoadMap:
    def __init__(self, farm: dict) -> None: ...
        # farm is the farm file as a dict (section 8a)
    def distance(self, a: str, b: str) -> float: ...
        # road distance in km. math.inf if there is no route
    def path(self, a: str, b: str) -> list[str]: ...
        # node ids from a to b, both included. Empty list if there is no route
    def block(self, a: str, b: str) -> None: ...
        # close the road between two linked nodes
    def open(self, a: str, b: str) -> None: ...
        # reopen it
```

Notes
- It works out all routes once at creation and again only after `block` or
  `open`. `distance` and `path` are then plain lookups.
- `distance(a, a)` is `0.0` and `path(a, a)` is `[a]`.
- Phase 0 ships a stand-in that uses straight-line distance and ignores roads.
  Ahmed replaces the inside and keeps this interface.

## 9. Repo layout and owners

```
foreman/            Zayyan   planner, simulation, roles, AI agents, memory, safety, server
foreman/nav/        Ahmed    road routing
web/                Ahmed    dashboard page and farm map
web/panels/         Vishva   numbers panel, why panel, accuracy chart
bridge/             Ahmed    AgOpenGPS link
scenarios/          Vishva   farm files
tools/              Vishva   farm checker, random farm maker, exports
tests/              all      each person tests their own part
docs/               Vishva   deck, architecture diagram, README assets
```

**At the start of each session, ask which teammate you are working with**, and
stay inside their folders. If a change is needed in someone else's folder,
say so and stop. Do not edit it.

**Working rules**
- Work on a branch. The main branch must always run.
- We merge at 2:00, 3:00, and 4:00. Aim to have your part working 10 minutes before.
- Small commits with clear messages.
- If something will take more than 20 minutes and is not in the current
  phase, do not start it.
- When you finish a task, tick its box in ROADMAP.md, in your owner's section only.

## 10. Core model and formulas

- **Work rate:** hectares per hour = `width_m * speed_kmh / 10 * efficiency`.
  Minutes for a job = remaining hectares / rate.
- **Travel time:** road distance / tractor road speed. Roads are a graph.
  Use shortest path. A blocked road is removed from the graph.
- **Fuel:** travel burns `burn_km` per km. Work burns `burn_work` per minute.
  Never plan a tractor below **10%** of its tank.
- **Fuel to get home:** stricter than the line above. A tractor must finish
  every job with enough fuel to drive back to the yard and still be above the
  10% reserve. Otherwise it could be stranded in a field.
- **Fuel margin:** real field speeds differ from the catalog, and a slower job
  burns more fuel. The planner budgets 30% extra work fuel for every job, so
  a field that runs up to 30% longer than planned cannot eat the reserve.
- **One tank per job:** there is no refuel in the middle of a job. A job that
  no capable tractor can finish on a full tank, with the drive out and the
  drive home, is held with that reason. Split it into smaller jobs.
- **Implement swap:** happens at the yard and takes **25 minutes**. A tractor
  that needs a different implement drives to the yard first.
- **Refuel:** at the yard, **10 minutes**. If a tractor passes the yard below
  60% fuel, it refuels. If a job would break the reserve, route via the yard first.
- **Picking the implement:** among the tractor's implements of the right type
  that it has the power to pull, take the fastest.

## 11. Planner requirements

1. **Input is the current state**, not "everyone at the yard at 6 AM". For each
   working tractor: where it is, when it is next free, its fuel then, what is
   hitched. A re-plan is the same call with a new state.
2. Work in progress stays with its tractor unless that tractor is down.
3. A job interrupted by a breakdown keeps its progress. Only the remainder is planned.
4. Cost, most important first: jobs left out (weighted by priority), late jobs,
   then driving and service time, then waiting, then finishing urgent work early.
5. **Change as little as possible on a re-plan.** Start from the previous plan
   and add a small cost for moving a job to another tractor.
6. If a job cannot meet its deadline, still schedule it and flag it late. Never hide it.
7. If no tractor can do a job, report it as held, with the reason.
8. A re-plan for 3 tractors and 10 jobs should take well under one second.
   Measure it and show it.

**Baseline: first come, first served.** Take jobs in the order they were
submitted. Give each to the compatible tractor that frees up first. No
look-ahead, no reordering. It still obeys the hard rules, so the comparison is honest.

## 12. Safety checks

Run all of these on every plan before dispatch. Re-derive each from the plan
and the world state. Do not trust the planner.

1. Each job has exactly one tractor. No finished job is rescheduled.
2. Right implement type, the tractor owns it, and has enough power.
3. Fuel stays above the 10% reserve, and each job ends with enough fuel to
   drive back to the yard above the reserve (section 10).
4. One machine per field at a time.
5. Jobs in a field follow their order (`after`).
6. No route uses a closed road.
7. Only working tractors get jobs.
8. No work lost or repeated: every unfinished job is planned or reported as held.
9. Deadlines met. This one is a warning, not a hard stop.

Also add an independent **referee** inside the simulator that checks rules at
the moment work starts and counts violations for any planner. The proof run
uses this count.

## 13. Numbers we must be able to show

Measured on the same farm with the same events, Foreman against the baseline.

| Number | Meaning |
|---|---|
| Jobs finished on time | Work that kept its value |
| Working share of the day | Work minutes / (work + travel + waiting + service) |
| Travel time and distance | Driving that earned nothing |
| Idle minutes | A tractor with no task while there is unfinished work it could do (it has the right implement, the job is released, its predecessor is done, and nobody is already working it), plus minutes waiting at a field |
| Unassigned minutes | A working tractor with no task while any job remains, whether or not it could do that job. Always at least the no-task part of idle |
| Yard service minutes | Time spent on implement swaps and refuels |
| Wasted minutes | Travel + idle + yard service. Machine time that covered no ground |
| Day finish time | The minute the last job finished (the time so far while jobs remain) |

**Known limits.** From `python -m foreman.sweep`: 196 test days on 6 farms
(each farm run quiet, and with each tractor breaking at 10 different times),
hidden factors off, idle weight 0.7. Zero rule violations on every day.
- On 2 of the 196 days the baseline finishes more jobs on time than Foreman.
  Both are on the example farm when Cedar (T3) breaks early, at minute 45 or
  minute 70: Foreman 3 of 5 on time, baseline 4 of 5. The day plan gives Cedar
  both the tilling and the planting on West Bench. When Cedar breaks, Atlas is
  already committed to a long mowing job and cannot take the tilling in time.
  The planner does not hedge its day plan against a breakdown.
- On 1 day (the ten-job test farm, Cedar breaks at minute 180) the baseline
  finishes 9 jobs and Foreman finishes 8.
- On 5 days the baseline wastes fewer minutes (travel + idle + service).
- The AI Safety agent does not fix the two Cedar days. Tested with the real
  model on both (Cedar breaks at minute 45 or 70): it raises the concern by
  itself on the first plan ("Cedar holds J2 and J3, and J3 depends on J2"),
  and the one re-plan it asks for passes the safety gate. With that caution
  all five jobs get finished instead of four, and the day ends at 14:05, not
  at the end of the working day. But the on-time count stays at 3 of 5
  against the baseline's 4: the re-plan gives Cedar both spray jobs, so when
  Cedar breaks those are the ones that finish late.
- Over all 196 days: Foreman 1,946 jobs on time and 63,637 wasted minutes;
  baseline 1,343 and 104,355.
| Re-plan time | Milliseconds from event to dispatched plan |
| Rule violations | Count from the referee. Target is zero |
| Estimate error by day | How far predicted job times were from actual (learning loop) |

## 14. Phases and how we know each is done

### Phase 1: first working version (1:00 to 2:00)
No breakdowns yet.
- Zayyan: planner, simulation loop, baseline.
- Ahmed: road routing, farm map with tractors moving and field progress.
- Vishva: sample farm file (3 tractors, 8 fields, 10 jobs), farm checker
  script, numbers panel.

**Done when:** `python -m foreman.server` starts with the sample farm loaded.
Pressing start runs a full day on screen. Every job finishes. The referee
reports zero violations. Foreman beats the baseline on travel and idle time.

### Phase 2: breakdowns and the real tractor (2:00 to 3:00)
- Zayyan: breakdown recovery, Safety gate, five rule-based roles with a log,
  plain-language explanation of each re-plan.
- Ahmed: AgOpenGPS link driving one tractor, "break this tractor" control,
  breakdown test cases.
- Vishva: random farm maker, export of event log and comparison, small rule tests.

**Done when:** breaking any tractor at any time leads to a new plan with no
human. Finished work is kept. A half-done field is finished once. The Safety
gate passes every dispatched plan. An explanation appears. One tractor moves
in AgOpenGPS.

### Phase 3: learning, proof, polish (3:00 to 4:00)
- Zayyan: learning loop, LLM layer, "Why?" for any job.
- Ahmed: proof run over random farms, blocked road and priority events, map polish.
- Vishva: accuracy chart, "Why?" panel, README.

**Done when:** the proof run reports violations, travel, idle, and re-plan
time across at least 200 random farms. The accuracy chart shows estimate
error falling over simulated days.

### Phase 4: present (4:00 to 4:50)
Code freeze at 4:00. Fixes only. Submit at 4:50.

**If we fall behind:** at 2:00 drop the learning loop and LLM layer. At 3:00,
if the AgOpenGPS link is not working, demo on our simulation and show the
message format in the deck.

## 15. Explanations

Every re-plan produces an explanation built from the plan data, not invented.

- Headline: what happened. "Bolt went down at 09:40 while spraying East
  Orchard (60% done)."
- One line per changed job: "Spray East Orchard: the remaining 40% moves from
  Bolt to Cedar. Finishes 10:25, 35 min before its 11:00 deadline."
- Summary: "2 jobs moved, 1 retimed, 6 unchanged. All deadlines are still met."
- Safety line: "Safety gate passed 9 of 9 checks."
- Re-plan time in milliseconds.

**"Why?" for a job:** show the chosen tractor, then every other tractor with
the real reason it was not chosen: no such implement, not enough power, out
of service, or "would finish 10:40, 25 min past the deadline".

## 16. Learning loop (Phase 3)

Plan, run, measure, update, plan better. Built in `foreman/learning.py`.

- Each time a tractor finishes or stops work on a job, the simulator emits a
  work sample: hectares covered and minutes spent. Foreman compares it with the
  catalog rate.
- `Learning` keeps a running work-rate factor per tractor, implement and field.
  It falls back to the same implement on the same field, then to the same
  tractor and implement elsewhere, then to 1.0. Factors are clamped to 0.4 to 1.6.
- The planner and the safety gate use the learned factor for time and fuel
  estimates. Safety rules are fixed. Learning only tunes estimates.
- Factors are saved to `runs/learned.json` and loaded at start, so day 2 starts
  with them.
- `run_days` (and `POST /api/learning/run`) runs several quiet days off screen
  with no clock and the hidden factors on. `GET /api/learning` returns the
  estimate error per day, the factors, and the AI lessons.
- Breakdown incidents and downtime estimate accuracy are kept for the current
  day only. Lessons from the AI Recovery agent are kept across days in
  `runs/lessons.jsonl`.
- **Measured** (average gap between predicted and actual work minutes):
  - With the hidden factors in `scenarios/example_farm.json`: 2.2% on day 1
    and 0.0% from day 2.
  - With the stronger hidden factors used in `tests/test_learning.py`
    (`*|mow|F3` 0.7, `*|till|F4` 0.8, `T2|spray|*` 1.15) on the same farm:
    19.1% on day 1 and 0.0% from day 2.
- The gap drops in one day because the simulator's hidden factors are constant.
  This is shown in simulation, where hidden true speeds differ from the catalog.
- The comparison report (`GET /api/report`) and `python -m foreman.sweep` run
  with no model and no learning, so they stay repeatable.

## 17. AgOpenGPS link (Phase 2, Ahmed)

The challenge points to **AgOpenWeb**, a rewrite of AgOpenGPS:
https://github.com/AgOpenGPS-Official/AgOpenWeb

Verified by running it today
- It is a single-tractor GPS guidance app. It has no fleet or job-list concept.
  One running host is one tractor.
- Prebuilt downloads are on the repo's Beta release, including
  `agopenweb-macos-arm64.dmg`. No .NET install is needed for those.
- Headless mode: `AgOpenWeb.Desktop --headless`. It serves its screen at
  `http://localhost:5174`.
- `AGOPENWEB_DATA` sets the data folder. Inside it, `AgOpenWeb/Tools/*.json`
  are implement profiles, `AgOpenWeb/Vehicles/*.json` are vehicle profiles,
  and `AgOpenWeb/Fields/` holds fields.
- It has a built-in simulator (SIM button) with steer, speed, and position controls.
- The browser talks to the host over a WebSocket at `/ws` with a custom binary
  codec. See `Shared/AgOpenWeb.RemoteServer/WireCodec.cs` and
  `Shared/AgOpenWeb.RemoteWiring/RemoteServerWiring.cs`. Commands seen there
  include `control.acquire`, `sim.toggleEnable`, `sim.setSteer`,
  `sim.setCoords`, `section.toggle`, and `config.set`.
- UDP message formats are documented in `Docs/PGN.md`: steer module, machine
  module, IMU, GPS, each with its own port. The repo also ships virtual
  modules under `Simulators/AgOpenWeb.VirtualModules` that act as tractor hardware.

Partial live map: `FOREMAN_AGOPENWEB_URLS="T1=ws://127.0.0.1:5174/ws"` runs
only T1 in AgOpenWeb. Every tractor left out stays on `SimTractor`, in the same
run and on the same clock. The simulated tractors are stepped by simulated time;
the live one is sampled from its telemetry each step. An id that is not in the
farm is an error. A dropped connection reads as `broken` and is re-planned onto
the simulated tractors. The baseline world is always fully simulated. This
supersedes `bridge/README.md` where it says every tractor needs a URL.

Not verified
- Whether two hosts can run on one machine without port clashes.
- The exact bytes needed to drive a tractor from outside. Read the two files
  above and `Docs/PGN.md` before writing the bridge.

Target for today: **one** tractor in AgOpenWeb follows what Foreman dispatches
(position and heading at minimum, implement profile if time allows). The other
tractors stay on our simulator behind the same `TractorAdapter`. Time-box this
to 45 minutes. If it is not working by 2:45, stop and document the message format.

## 18. Demo script (3 minutes)

1. Problem, 20 s. A person still decides who goes where and redoes it when
   something breaks.
2. Product, 20 s. Five rule-based roles and two AI agents, no human in the loop.
3. Live demo, 90 s. Submit jobs, watch the plan run next to the baseline,
   break a tractor, watch the plan repair and explain itself. Show the
   tractor in AgOpenGPS.
4. Proof, 30 s. Jobs on time, working share, travel, idle. Zero violations
   across the test farms. Re-plan time in milliseconds.
5. Architecture and next step, 20 s. One diagram. Tractors plug in through
   one interface.

Build toward this script. Anything that does not appear in it is lower priority.

## 19. Open questions

- Is hand-in at 5:00 or 5:30? Plan for 5:00.
- Does ATC provide a "starter dispatcher", or is it first come, first served?
- What does an hour of tractor time cost, so we can turn saved hours into dollars?
- Which LLM do we use in Phase 3: an API key, or a local model? Answered: Claude
  Haiku 5.5 through the Claude Code command line with a token in `.env`, behind
  one interface so a normal API key is a one-file change. See section 7.
