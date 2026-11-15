# Foreman

Foreman is an agent layer that assigns jobs to a small fleet of simulated autonomous tractors and re-plans when one breaks down. It is built for the HackAGX "Run the Fleet" challenge by American Tractor Company.

## Run it

Needs Python 3.11 (3.10 or newer works).

```
python3.11 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
python -m foreman.server
```

Open http://localhost:8000

Run the tests with `pytest`.

## Status

This is the Phase 0 skeleton. The server returns fake data from `GET /api/state` in the final shape (CLAUDE.md section 8c). `POST /api/control` and `POST /api/event` are stubs that accept anything and return `{"ok": true}`. `scenarios/example_farm.json` is the pattern for farm files.

## Simulation

We use our own simulator. One tractor is planned to be mirrored in AgOpenWeb (see `bridge/`). This section will be updated before hand-in.

## Folders and owners

| Folder | Owner | What goes there |
| --- | --- | --- |
| foreman/ | Zayyan | planner, simulation, agents, memory, safety, server |
| foreman/nav/ | Ahmed | road routing |
| web/ | Ahmed | dashboard page and farm map |
| web/panels/ | Vishva | numbers panel, why panel, accuracy chart |
| bridge/ | Ahmed | AgOpenGPS link |
| scenarios/ | Vishva | farm files |
| tools/ | Vishva | farm checker, random farm maker, exports |
| tests/ | all | each person tests their own part |
| docs/ | Vishva | deck, architecture diagram, README assets |

## Working rules

- Stay inside your own folders.
- Work on your own branch. `main` must always run.
- Merges happen at 2:00, 3:00 and 4:00.
- Small commits with clear messages.
- The shared formats are in CLAUDE.md section 8 and change only by agreement.
- When you finish a task, tick its box in [ROADMAP.md](ROADMAP.md), in your own section only.

Full brief: [CLAUDE.md](CLAUDE.md). Task list by phase: [ROADMAP.md](ROADMAP.md).
