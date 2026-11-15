# foreman

Owner: Zayyan

The engine, the API and the two AI agents. `ARCHITECTURE.md` at the repo root explains how the parts fit.

Rule-based core
- `server.py`: entry point (`python -m foreman.server`) and the API of CLAUDE.md section 8c.
- `engine.py`: the live day on a real-time clock; dashboard state and the comparison report.
- `day.py`: one working day. Foreman world and baseline world on one clock, same events for both.
- `run.py`: one world: its simulation, current plan, and plan number.
- `roles/`: the five rule-based roles. `orchestrator.py` holds the event routing table.
- `planner.py`: the planner. `baseline.py`: first come, first served.
- `evaluator.py`: exact timing and cost of a set of routes.
- `safety.py`: the safety gate's nine checks. It does not import the planner.
- `watch.py`: the tractor status feed reader used by the recovery monitor.
- `explain.py`: plain-language explanation of each plan change. `why.py`: why a job went to its tractor.
- `sim.py`: simulated tractors behind the interface in `adapter.py`, plus the referee.
- `model.py`: farm data model, loader, and the formulas of section 10. `state.py`: state and plan types.
- `memory.py`: the event log (`runs/event_log.jsonl`), incidents, and AI lessons (`runs/lessons.jsonl`).
- `learning.py`: learned work rates per tractor, implement and field (`runs/learned.json`).
- `sweep.py`: `python -m foreman.sweep` re-runs the idle-weight comparison across every test day.
- `nav/`: road routing (Ahmed).

AI agents (a real model behind each; off when there is no key)
- `ai_base.py`: the one model client interface, and the record of an AI agent call.
- `llm.py`: the model client (Claude Code with the token from `.env`) and a fake model for tests.
- `tool_server.py`: the read-only tool server the model calls.
- `ai_recovery.py`: the AI Recovery agent. `ai_safety.py`: the AI Safety agent.
- `ai_crew.py`: runs calls in the background so nothing waits. `ai_desk.py`: asks the agents and applies their answers.
- `faults.py`: sample fault reports with the real repair time the simulator uses.
