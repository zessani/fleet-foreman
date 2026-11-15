# Foreman architecture

Foreman coordinates a small fleet of autonomous tractors with no human in the
loop. It assigns jobs, picks the implement for each tractor, sends the tractors
to work, and repairs the plan by itself when a tractor breaks down or a road
closes. It runs on its own simulator, behind a tractor interface that an
AgOpenWeb or real tractor can implement.

The rule in one line: rules decide and verify, two AI agents add caution, and
nothing waits on a model.

There are seven roles. Five are rule-based code (the orchestrator, the planner,
the route finder, the safety gate, the recovery monitor). Two are AI agents, a
real model behind each: the AI Recovery agent and the AI Safety agent.

## The loop

```
 event (jobs, breakdown, road, priority, drift, AI follow-up)
   |
   v
 Orchestrator --ROUTES--> Planner --> Route finder --> Safety gate
                            ^                              |
                            |                    pass      | fail: plan once more,
                            |                              | else send nothing
                            |                              v
                            |                          dispatch ---> tractors (TractorAdapter)
                            |                                            |
                  Recovery monitor <------ status feed, once per minute --+
                            |
                            | breakdown with a fault report
                            v
 AI Recovery agent  reads: tractor status, jobs at risk, past lessons
   can cause: an expected downtime -> planner compares waiting vs moving

 Safety gate passes a plan
                            |
                            v
 AI Safety agent    reads: plan, tractor history, recent log, past lessons
   can cause: warn | hold one job 30 min | one re-plan with a named caution
              (a plan it triggers must pass the safety gate again)
```

The planner dispatches its rule-based plan first. An AI agent's answer arrives
later and comes back as a follow-up event through the same route.

## The seven roles

| Role | Type | Job | Tool | File |
|---|---|---|---|---|
| Orchestrator | rule-based | Runs the loop, routes each event | ROUTES table | `foreman/roles/orchestrator.py` |
| Planner | rule-based | Which tractor does which job, in what order, with which implement | Planner (solver), implement picker | `foreman/roles/fleet.py`, `foreman/planner.py`, `foreman/model.py` |
| Route finder | rule-based | Routes between fields, stops two tractors sharing a field | Road map, field conflict checker | `foreman/roles/navigation.py`, `foreman/nav/` |
| Safety gate | rule-based | Passes or rejects every plan | Rule verifier, nine checks | `foreman/roles/safety_gate.py`, `foreman/safety.py` |
| Recovery monitor | rule-based | Watches tractors, spots a failure | Tractor status feed reader | `foreman/roles/recovery.py`, `foreman/watch.py` |
| AI Recovery agent | AI agent | Reads a fault report. Returns severity, expected downtime, reason, lesson | Read-only: tractor status, jobs at risk, past lessons | `foreman/ai_recovery.py` |
| AI Safety agent | AI agent | Reviews a plan the gate passed. Returns concerns | Read-only: plan, tractor history, recent log, past lessons | `foreman/ai_safety.py` |

The safety checks do not import the planner. They re-derive every rule from the
plan and the world state.

## Event routing

Copied from `ROUTES` in `foreman/roles/orchestrator.py`.

| Event | Roles, in order |
|---|---|
| jobs_submitted | Planner, Route finder, Safety gate |
| breakdown | Recovery monitor, Planner, Route finder, Safety gate |
| repaired | Recovery monitor, Planner, Route finder, Safety gate |
| road_blocked | Route finder, Planner, Route finder, Safety gate |
| road_opened | Route finder, Planner, Route finder, Safety gate |
| priority_changed | Planner, Route finder, Safety gate |
| job_added | Planner, Route finder, Safety gate |
| drift | Recovery monitor, Planner, Route finder, Safety gate |
| downtime_estimate | Planner, Route finder, Safety gate |
| downtime_expired | Planner, Route finder, Safety gate |
| ai_caution | Planner, Route finder, Safety gate |
| hold_released | Planner, Route finder, Safety gate |
| job_done | Recovery monitor |
| work_sample | Recovery monitor |

A route that includes the planner ends in a dispatch if the safety gate passes
the plan. `job_done` and `work_sample` never cause a re-plan. `work_sample` also
feeds the learning loop. Events seen in the same status reading share one re-plan.

## What the AI agents can do

- Estimate how long a broken tractor will be out, from its fault report. The
  planner then compares moving the work now with waiting for the tractor, and
  waits only if that is cheaper. If the tractor is not back 10 minutes after
  the estimate, the work is moved.
- Write a one-line lesson about the fault (AI Recovery agent only).
- Warn about a risk the nine rules do not cover. A warning is logged and shown.
- Hold one job for 30 minutes.
- Ask for one re-plan with one named caution, `spread_dependent_jobs`, which
  adds a cost for keeping a job and the job that depends on it on one tractor.

## What the AI agents cannot do

- Assign jobs to tractors. The planner does that.
- Delay a re-plan. The rule-based plan is dispatched first.
- Approve a plan the safety gate rejected, or change or remove a safety rule.
- Review a plan the safety gate has not passed. The AI Safety agent is asked
  once per plan, and a plan it triggers must pass the gate again.
- Act on a stale answer. An answer for a plan that was replaced is discarded.
- Take longer than 15 seconds. Any error, timeout or bad JSON falls back to the
  rules and is logged.
- Change anything through their tools. The tools are read-only snapshots.

The model is Claude Haiku 5.5, reached through the Claude Code command line with
a token in `.env` (git-ignored). It sits behind one interface, `ModelClient` in
`foreman/ai_base.py`, so a normal API key is a change to `foreman/llm.py`.
`FOREMAN_AI=0` turns the AI agents off. They are also off with no token or no
`claude` command. Then every decision is rule-based.

## Memory

| Store | What is in it | File | Across days |
|---|---|---|---|
| World state | Where each tractor is, what is done, what is half done | the simulation (`foreman/sim.py`), read through `Run.world()` | No |
| Event log | Every decision and its reason, append only, JSON Lines | `runs/event_log.jsonl` | No, a new day starts a new file |
| Incidents | One record per breakdown | `foreman/memory.py` | No |
| Downtime estimates | AI Recovery agent estimate against the real repair time | `foreman/memory.py` | No |
| AI lessons | One-line lessons, written only by the AI Recovery agent, read by the AI Safety agent | `runs/lessons.jsonl` | Yes |
| Learned work rates | A factor per tractor, implement and field | `runs/learned.json` | Yes |

Learning only tunes time and fuel estimates. Safety rules are fixed.

## The tractor interface

`foreman/adapter.py` defines `TractorAdapter`, three calls every tractor
implements: `status()`, `dispatch(stops, plan_no)` and `stop()`. A dispatch with
a plan number lower than one already accepted is ignored, and work in progress
is not interrupted.

Today the simulator in `foreman/sim.py` implements it for every tractor. The
bridge in `bridge/agopenweb.py` does not implement the interface. It reads
`GET /api/state` and mirrors one tractor into AgOpenWeb.

## File map: `foreman/`

| File | What it is |
|---|---|
| `server.py` | `python -m foreman.server`: the API and the dashboard host |
| `engine.py` | The live day on a real-time clock. Builds `state`, the report, `why`, the learning report |
| `day.py` | One working day: Foreman world and baseline world on one clock, same events |
| `run.py` | One world: its simulation, current plan, plan number |
| `roles/orchestrator.py` | The orchestrator and the ROUTES table |
| `roles/fleet.py` | The planner role |
| `roles/navigation.py` | The route finder role and the field conflict checker |
| `roles/safety_gate.py` | The safety gate role |
| `roles/recovery.py` | The recovery monitor role |
| `roles/base.py` | Shared role base and the cycle record |
| `planner.py` | Regret insertion plus local search; `plan`, `plan_with_return` |
| `baseline.py` | First come, first served dispatcher for the comparison |
| `evaluator.py` | Exact timing and cost of a set of routes |
| `safety.py` | The nine safety checks |
| `watch.py` | Reads the tractor status feed, finds faults and stalls |
| `explain.py` | Plain-language explanation of each plan change |
| `why.py` | "Why?" for a job: the chosen tractor and the reason for each other |
| `memory.py` | Event log, incidents, downtime estimates, lessons |
| `learning.py` | Learned work rates and the multi-day run |
| `sim.py` | Simulated tractors behind the adapter, and the referee |
| `adapter.py` | The tractor interface |
| `model.py` | Farm data model, loader, formulas, implement picker |
| `state.py` | State and plan types |
| `faults.py` | Sample fault reports with real repair times |
| `ai_base.py` | `ModelClient` interface, `Tool`, the AI call record |
| `llm.py` | The Claude Code command line client and a fake for tests |
| `tool_server.py` | Read-only tool server the model reads data through |
| `ai_recovery.py` | The AI Recovery agent: prompt, schema, answer check |
| `ai_safety.py` | The AI Safety agent: prompt, schema, answer check |
| `ai_crew.py` | Runs AI calls in the background with a time limit |
| `ai_desk.py` | Asks the two AI agents and applies their answers |
| `sweep.py` | `python -m foreman.sweep`: the proof run across every test day |
| `nav/` | Road routing |

## How we know it works

- Tests: `.venv/bin/python -m pytest -q` gives 292 passed (measured today).
- The referee inside the simulator checks the rules at the moment work starts
  and counts violations for any planner.
- `python -m foreman.sweep` ran 196 test days on 6 farms (each farm quiet, and
  with each tractor breaking at 10 different times), 2,507 jobs, 0 violations.
  Foreman finished 1,946 jobs on time and wasted 63,637 minutes. The baseline
  finished 1,343 on time and wasted 104,355 (measured at 3:05 PM today). The
  sweep and `GET /api/report` run with no model and no learning, so they are
  repeatable.
- Learning: with the hidden speed factors in the example farm, the gap between
  predicted and actual work minutes is 2.2% on day 1 and 0.0% from day 2. With
  stronger factors it is 19.1% and 0.0%.

Known limits (CLAUDE.md section 13):

- On 2 of 196 days the baseline finishes more jobs on time. Both are on the
  example farm when Cedar (T3) breaks at minute 45 or 70. Foreman finishes 3 of
  5 on time, the baseline 4 of 5. The day plan gives Cedar both the tilling and
  the planting on West Bench, and Atlas is already committed to a long mowing
  job. The planner does not hedge its day plan against a breakdown.
- On 1 day (the ten-job test farm, Cedar breaks at minute 180) the baseline
  finishes 9 jobs and Foreman finishes 8.
- On 5 days the baseline wastes fewer minutes (travel, idle and service).
