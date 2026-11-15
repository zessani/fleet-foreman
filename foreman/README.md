# foreman

Owner: Zayyan

Planner, simulation, agents, memory, safety and server.

- `server.py`: entry point (`python -m foreman.server`) and the API of CLAUDE.md section 8c.
- `engine.py`: runs the Foreman world and the baseline world on one clock and builds the dashboard state.
- `model.py`: farm data model, loader, and the formulas of section 10.
- `state.py`: world state and plan types.
- `evaluator.py`: exact timing and cost of a set of routes.
- `planner.py`: the Foreman planner. `baseline.py`: first come, first served.
- `sim.py`: simulated tractors behind the interface in `adapter.py`, plus the referee.
- `nav/`: road routing (Ahmed).
