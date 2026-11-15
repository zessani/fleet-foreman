# Foreman against first come, first served

## Sample farm

Sample farm: 3 tractors, 10 jobs, Cedar fails at 09:30

Scenario file: sample_farm.json
Hidden speed factors: off

Scripted events:
- 09:30 Cedar breaks down

| Number | Foreman | First come first served | Difference |
|---|---|---|---|
| Jobs completed | 10 of 10 | 10 of 10 | +0 |
| Jobs on time | 10 | 10 | +0 |
| Travel time in minutes | 90.5 | 103.7 | -13.2 |
| Idle time in minutes | 0.9 | 135.9 | -135 |
| Yard service in minutes | 110 | 70 | +40 |
| Wasted minutes: travel + idle + service | 201.4 | 309.6 | -108.2 |
| Distance driven in km | 44.2 | 52.4 | -8.2 |
| Working share of the day in % | 80.2 | 72.5 | +7.7 |
| Day finished at | 14:02 | 14:02 | +0 min |
| Rule violations | 0 | 0 | +0 |

Re-plan times:
- Plan 1 at 06:00: 7.43 ms from event to new plan
- Plan 2 at 09:30: 0.31 ms from event to new plan

## Large farm

Large farm: 5 tractors and 25 jobs

Scenario file: large_farm.json
Hidden speed factors: off

Scripted events:
- 11:15 Ember breaks down

| Number | Foreman | First come first served | Difference |
|---|---|---|---|
| Jobs completed | 21 of 25 | 16 of 25 | +5 |
| Jobs on time | 20 | 13 | +7 |
| Travel time in minutes | 305.3 | 319.9 | -14.6 |
| Idle time in minutes | 42.2 | 547.2 | -505 |
| Yard service in minutes | 305 | 321.4 | -16.4 |
| Wasted minutes: travel + idle + service | 652.5 | 1188.5 | -536 |
| Distance driven in km | 155.8 | 162.7 | -6.9 |
| Working share of the day in % | 77.4 | 59.8 | +17.6 |
| Day finished at | not finished | not finished | n/a |
| Rule violations | 0 | 0 | +0 |

Re-plan times:
- Plan 1 at 06:00: 357.39 ms from event to new plan
- Plan 2 at 11:15: 12.58 ms from event to new plan

## All test days

196 days on 6 farms: every farm run quiet and with each tractor breaking at 10 different times, hidden speed factors off, 2507 jobs in total.

| Number | Foreman | First come first served | Difference |
|---|---|---|---|
| Jobs on time | 1946 | 1343 | +603 |
| Wasted minutes | 63637 | 104355 | -40717.6 |
| Idle minutes | 5361.2 | 38203.2 | -32842 |
| Rule violations | 0 | 0 | +0 |
| Jobs completed | 2119 | 1824 | +295 |
| Travel minutes | 29043.3 | 30569.1 | -1525.8 |
| Yard service minutes | 29232.5 | 35583.1 | -6350.6 |

On 2 of the 196 test days first come, first served finishes more jobs on time than Foreman: example farm, Cedar breaks at minute 45; example farm, Cedar breaks at minute 70.
