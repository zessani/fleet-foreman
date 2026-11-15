# AgOpenWeb bridge

Owner: Ahmed

The project now has two AgOpenWeb modes:

- **Live execution (recommended):** Foreman creates an `AgOpenWebTractorAdapter`
  for every tractor. AgOpenWeb owns GPS movement and worked-area progress.
- **Legacy mirror:** this module's CLI copies one deterministic simulator
  tractor into AgOpenWeb. It remains only as a protocol diagnostic.

Live execution uses AgOpenWeb's real `/ws` command protocol, obtains control
authority, keeps the deadman heartbeat alive, dispatches fields/jobs and
recorded paths, and reads GPS, guidance, section and coverage telemetry.

## Live execution

Run one isolated AgOpenWeb host per tractor. Each must have the matching farm
fields and recorded paths named `Foreman-<job id>` (for example `Foreman-J1`).
Then start Foreman with one URL for every tractor:

```sh
FOREMAN_AGOPENWEB_URLS='T1=ws://tractor-1.local:5174/ws;T2=ws://tractor-2.local:5174/ws;T3=ws://tractor-3.local:5174/ws' \
FOREMAN_AGOPENWEB_ORIGIN='33.4484,-112.0740' \
python -m foreman.server
```

Foreman refuses partial or duplicated live configuration: every farm tractor
needs its own URL on a different host. AgOpenWeb itself enforces one running
instance per computer and binds port 5174, so fleet mode uses separate
computers/VMs/containers, not ports 5174/5175/5176 on one Mac. With no
`FOREMAN_AGOPENWEB_URLS`, the server uses deterministic simulation.

In live mode:

- assignments become `field.startJob`, `recpath.selectFile`, and `recpath.play`;
- AgOpenWeb controls simulator speed, steering/autosteer, and sections;
- `/api/state` positions and job percentages come from AgOpenWeb telemetry;
- completion is based on AgOpenWeb `WorkedAreaSqM`, not a Foreman work timer;
- `SimTractor.step()` is never called for the Foreman run.

## Legacy mirror diagnostic

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
