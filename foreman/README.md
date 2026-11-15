# foreman

Owner: Zayyan

Planner, simulation, agents, memory, safety and server. `server.py` is the entry point (`python -m foreman.server`). `fake_state.py` is placeholder data and is replaced in Phase 1. `adapter.py` is the tractor interface from CLAUDE.md section 8b.

Phase items (CLAUDE.md section 14):
- Phase 1: planner, simulation loop, baseline.
- Phase 2: breakdown recovery, Safety gate, six named agents with a log, plain-language explanation of each re-plan.
- Phase 3: learning loop, LLM layer, "Why?" for any job.
