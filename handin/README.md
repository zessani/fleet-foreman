scenario.json: the farm file exactly as it was loaded.
event_log.jsonl: every event of the day, one JSON object per line.
scenario_large_farm.json and event_log_large_farm.jsonl: the same for the large farm.
comparison.md and comparison.json: Foreman against first come, first served.
comparison.md also has an "All test days" section: the 196 test days, Foreman against first come, first served.
Section list of comparison.md: the day itself, and with --test-days the "All test days" section (also under "test_days" in comparison.json).
To make these files again: python -m foreman.handin
With the 196 test days: python -m foreman.handin --test-days
The busy day (scenarios/busy_day.json, written to handin/busy_day/): python -m foreman.handin --scenario busy_day
explanations.md: each plan change of the day, explained in plain words.
README.md: this file. Made with: python -m foreman.handin --test-days
