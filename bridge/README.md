# AgOpenWeb bridge

Owner: Ahmed

This bridge mirrors one tractor from Foreman's live state into AgOpenWeb's
built-in simulator. It uses AgOpenWeb's real `/ws` command protocol, obtains
control authority, sends the required deadman heartbeat, updates GPS position,
and stops the AgOpenWeb tractor when Foreman reports a breakdown.

## Run

1. Start AgOpenWeb and turn on its **SIM** panel. It serves its UI at
   `http://localhost:5174`.
2. Start Foreman with `python -m foreman.server` and press **Start**.
3. In another terminal run:

   ```sh
   python -m bridge.agopenweb --tractor T1 --verbose
   ```

The bridge defaults to `http://127.0.0.1:8000` for Foreman and
`ws://127.0.0.1:5174/ws` for AgOpenWeb. Use `--foreman`, `--agopenweb`,
`--origin-lat`, and `--origin-lon` to override them.

AgOpenWeb has one active tractor per host. The other Foreman tractors continue
to use the deterministic simulator and remain visible on the command-center map.

## Wire behavior

- Commands are WebSocket text frames: `command` or `command|argument`.
- The bridge sends `control.acquire|Foreman AgOpenWeb Bridge` once.
- It sends `control.presence` every 500 ms, inside AgOpenWeb's 1.5 second
  control-authority timeout.
- Farm coordinates are local kilometres. They are projected around the
  configured latitude/longitude origin, then sent through `sim.setCoords`.
- A broken tractor triggers `sim.stop`; a repaired tractor resumes position
  updates.
- AgOpenWeb's binary Tick and Status frames are decoded for connection,
  simulator, position, heading, speed, and steering telemetry.
