# foreman

Owner: Zayyan

Planner, simulation, agents, memory, safety and server.

- `server.py`: entry point (`python -m foreman.server`) and the API of CLAUDE.md section 8c.
- `engine.py`: the live day on a real-time clock; dashboard state and the comparison report.
- `day.py`: one working day. Foreman world and baseline world on one clock, same events for both.
- `run.py`: one world: its simulation, current plan, and plan number.
- `agents/`: the five agents. `orchestrator.py` holds the event routing table.
- `planner.py`: the Foreman planner (the Fleet agent's tool). `baseline.py`: first come, first served.
- `evaluator.py`: exact timing and cost of a set of routes.
- `safety.py`: the nine checks of section 12 (the Safety agent's tool). It does not import the planner.
- `watch.py`: the tractor status feed reader (the Recovery agent's tool).
- `explain.py`: plain-language explanation of each plan change.
- `memory.py`: the event log (JSON Lines, saved to `runs/event_log.jsonl`) and incident record.
- `sim.py`: simulated tractors behind the interface in `adapter.py`, plus the referee.
- `model.py`: farm data model, loader, and the formulas of section 10. `state.py`: state and plan types.
- `nav/`: road routing (Ahmed).
