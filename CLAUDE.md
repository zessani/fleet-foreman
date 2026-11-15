# Foreman: build brief for Claude Code

Read this whole file before writing code. It is the shared context for three
people, each running their own Claude Code session on the same repo.

## 1. The situation

- **Event:** Hack Arizona AgX hackathon, Tucson, Sunday Nov 15, 2026.
- **Challenge:** "Run the Fleet", presented by American Tractor Company (ATC).
- **Hard deadline:** hand-in at **5:00 PM today**. Code freeze at 4:00 PM.
- **Team:** Zayyan, Ahmed, Vishwa. See section 9 for who owns what.
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

**Foreman** is an agent orchestration layer that runs a fleet of autonomous
tractors with no human in the loop. It assigns jobs, picks the implement,
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
- Runs offline. No cloud call is needed for the core.

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

Six agents share one memory and call fixed tools. Agents decide what to do.
Tools do the exact work, so plans are fast and never break a rule.

| Agent | Job | Tool it calls |
|---|---|---|
| Orchestrator | Runs the loop, routes each event to the right agents | Task queue |
| Fleet | Decides which tractor does which job, in what order | Planner (solver) |
| Implement | Picks the implement for each job, checks the tractor can pull it | Implement catalog |
| Navigation | Routes between fields, stops tractors sharing a field | Route finder, conflict checker |
| Safety | Passes or rejects every plan. Replaces human approval | Rule verifier |
| Recovery | Watches tractors, spots a failure, starts the re-plan, records lessons | Tractor status feed |

**The loop**
1. Jobs arrive (or an event happens).
2. Implement, Fleet, and Navigation build a plan.
3. Safety checks it. A plan that fails a hard check is never sent.
4. The plan is dispatched to the tractors.
5. Recovery watches for trouble.
6. On a breakdown or blocked road, go to step 2 with finished work frozen.

**Event routing table** (keep this explicit in code, it is part of the pitch)

| Event | Agents, in order |
|---|---|
| jobs_submitted | Implement, Fleet, Navigation, Safety, dispatch |
| breakdown | Recovery, Implement, Fleet, Navigation, Safety, dispatch |
| repaired | Recovery, Fleet, Navigation, Safety, dispatch |
| road_blocked / road_opened | Navigation, Fleet, Navigation, Safety, dispatch |
| priority_changed / job_added | Implement, Fleet, Navigation, Safety, dispatch |
| job_done / work_sample | Recovery (learn), no re-plan |

**Memory, three kinds**
- **World state:** where each tractor is, what is done, what is half done.
- **Event log:** every decision and its reason, in order. Append only. This is
  also the hand-in log. Save as JSON Lines.
- **Learned facts:** measured field speeds and breakdown history. Kept across days.

**Agents in Phase 1 and 2 are plain Python modules** with one clear job each.
No LLM is needed for them to work. The LLM is added in Phase 3 for incident
reasoning and for wording explanations, behind one setting. If the LLM or the
network fails, the system falls back to the tools alone.

**Safety rules are fixed.** Learning may tune estimates. It may never relax a rule.

## 7. Tech choices

- Python 3.10+ for the engine and API. FastAPI + uvicorn.
- Dashboard: one HTML page with plain JavaScript and Canvas. No build step, no
  CDN, so it works offline.
- The dashboard polls `GET /api/state` a few times per second. No WebSockets.
- Planner: regret insertion plus local search over an exact route evaluator.
  Google OR-Tools is optional as a second opinion. Do not block on it.
- Tests: pytest for the rules and the planner.
- One command to run: `python -m foreman.server` then open `http://localhost:8000`.
- Ask before adding any other dependency.

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
    def dispatch(self, stops: list[dict]) -> None: ...
        # Replace the tractor's to-do list. Work in progress is not interrupted.
        # stop: {job, field, implement, via_yard, swap, refuel, path: [node ids]}
    def stop(self) -> None: ...
```

### 8c. Dashboard state: `GET /api/state`

```json
{
  "now": 215.0, "clock": "09:35", "running": true, "speed": 4,
  "farm": {"name": "...", "nodes": [], "roads": [], "blocked": [["X1", "F1"]]},
  "foreman": {
    "tractors": [{"id": "T1", "name": "Atlas", "x": 1.1, "y": 2.0, "status": "working",
                  "job": "J3", "fuel_pct": 72, "mounted": "disc_6", "color": "#E8A33D"}],
    "jobs": [{"id": "J1", "type": "spray", "field": "F1", "priority": 5,
              "deadline": 240, "pct": 60, "status": "active", "tractor": "T2"}],
    "plan": [{"job": "J1", "tractor": "T2", "implement": "sprayer_18",
              "start": 30, "finish": 85, "late_min": 0, "path": ["YARD", "X1", "F1"]}],
    "totals": {"jobs_done": 4, "on_time": 4, "travel_min": 61, "idle_min": 12,
               "working_share": 78.5, "km": 24.1, "violations": 0},
    "explanation": {"headline": "...", "summary": "...", "changes": [], "ms": 38},
    "safety": {"passed": true, "checks": []},
    "log": []
  },
  "baseline": {"tractors": [], "jobs": [], "plan": [], "totals": {}}
}
```

Other endpoints
- `POST /api/control` with `{action: start|pause|reset, speed}`
- `POST /api/scenario` with `{name}` to load a farm file
- `POST /api/event` with `{type: breakdown|repair|block_road|open_road|set_priority|add_job, ...}`
- `GET /api/why?job=J1`
- `GET /api/log` (JSON Lines), `GET /api/report` (baseline comparison)

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
foreman/            Zayyan   planner, simulation, agents, memory, safety, server
foreman/nav/        Ahmed    road routing
web/                Ahmed    dashboard page and farm map
web/panels/         Vishwa   numbers panel, why panel, accuracy chart
bridge/             Ahmed    AgOpenGPS link
scenarios/          Vishwa   farm files
tools/              Vishwa   farm checker, random farm maker, exports
tests/              all      each person tests their own part
docs/               Vishwa   deck, architecture diagram, README assets
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
3. Fuel stays above the 10% reserve.
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
| Idle minutes | A working tractor with nothing to do while jobs remain, plus waiting at a field |
| Re-plan time | Milliseconds from event to dispatched plan |
| Rule violations | Count from the referee. Target is zero |
| Estimate error by day | How far predicted job times were from actual (learning loop) |

## 14. Phases and how we know each is done

### Phase 1: first working version (1:00 to 2:00)
No breakdowns yet.
- Zayyan: planner, simulation loop, baseline.
- Ahmed: road routing, farm map with tractors moving and field progress.
- Vishwa: sample farm file (3 tractors, 8 fields, 10 jobs), farm checker
  script, numbers panel.

**Done when:** `python -m foreman.server` starts with the sample farm loaded.
Pressing start runs a full day on screen. Every job finishes. The referee
reports zero violations. Foreman beats the baseline on travel and idle time.

### Phase 2: breakdowns and the real tractor (2:00 to 3:00)
- Zayyan: breakdown recovery, Safety gate, six named agents with a log,
  plain-language explanation of each re-plan.
- Ahmed: AgOpenGPS link driving one tractor, "break this tractor" control,
  breakdown test cases.
- Vishwa: random farm maker, export of event log and comparison, small rule tests.

**Done when:** breaking any tractor at any time leads to a new plan with no
human. Finished work is kept. A half-done field is finished once. The Safety
Agent passes every dispatched plan. An explanation appears. One tractor moves
in AgOpenGPS.

### Phase 3: learning, proof, polish (3:00 to 4:00)
- Zayyan: learning loop, LLM layer, "Why?" for any job.
- Ahmed: proof run over random farms, blocked road and priority events, map polish.
- Vishwa: accuracy chart, "Why?" panel, README.

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
- Safety line: "Safety Agent passed 9 of 9 checks."
- Re-plan time in milliseconds.

**"Why?" for a job:** show the chosen tractor, then every other tractor with
the real reason it was not chosen: no such implement, not enough power, out
of service, or "would finish 10:40, 25 min past the deadline".

## 16. Learning loop (Phase 3)

Plan, run, measure, update, plan better.

- When a tractor finishes or stops work on a job, record hectares covered and
  minutes spent. Compare with the catalog rate.
- Keep a running average speed factor per tractor, implement, and field.
- The planner uses the learned factor for its time estimates.
- Record breakdowns per tractor and a one-line lesson per incident.
- Save learned facts to a file so day 2 starts with them.
- Demo: run several simulated days fast. Chart the average estimate error per day.
- State plainly that this is shown in simulation, where hidden true speeds
  differ from the catalog.

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
2. Product, 20 s. Six agents, no human in the loop.
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
- Which LLM do we use in Phase 3: an API key, or a local model?
