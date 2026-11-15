# scenarios

Owner: Vishva

Farm files in JSON. `example_farm.json` is the pattern to copy. The format is CLAUDE.md section 8a.

Job `type` values used so far: `spray`, `mow`, `till`, `plant`.

Units: time in minutes from the start of the working day, distance in km, area in hectares.

Optional tractor reliability inputs:

- `breakdown_risk_per_hour`: assumed probability rate used by preventive planning (default `0.02`).
- `typical_repair_min`: expected repair duration used when estimating recovery (default `45`).

These are transparent scenario assumptions, not predictions claimed from sensor data.

Phase items (CLAUDE.md section 14), Phase 1: sample farm file (3 tractors, 8 fields, 10 jobs).
