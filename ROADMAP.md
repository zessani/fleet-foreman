# Roadmap

Hand-in: 5:00 PM. Code freeze: 4:00 PM.
Full context and formats are in CLAUDE.md.

## Status
- Current phase: 2
- Last merge: Merge 2 at 2:40 PM (zayyan, ahmed, vishva)
- Blockers: the main page does not yet show the plan change, safety result or comparison numbers; the speed menu snaps back to the server's speed

Only tick boxes in your own section. Zayyan updates Status at each merge.

## Phase 0: Set up (to 1:15)
Goal: a starter repo that runs, so everyone can build on it.

### Zayyan
- [x] Starter structure pushed to main
- [x] Shared formats written in CLAUDE.md section 8

### Ahmed
- [ ] AgOpenWeb installed and opens on a Mac
- [x] Routing interface (section 8d) agreed

### Vishva
- [ ] Repo cloned and server runs
- [ ] Asked organizer: hand-in at 5:00 or 5:30?

## Phase 1: First working version (to 2:00)
Goal: jobs go in, a plan comes out, tractors run the day on a map.
Done when: the sample farm runs a full day, every job finishes, no rule
is broken, and we beat "first come, first served".

### Zayyan
- [x] Planner (starts from the current state)
- [x] Simulation loop
- [x] "First come, first served" baseline

### Ahmed
- [x] Shortest routes and travel times
- [x] Farm map with tractors moving and field progress

### Vishva
- [x] Sample farm file: 3 tractors, 8 fields, 10 jobs
- [x] Farm file checker
- [x] Numbers panel
- [ ] Deck started

**Merge 1 at 2:00**

## Phase 2: Breakdowns and the real tractor (to 3:00)
Goal: a tractor breaks, the plan fixes itself, one tractor moves in AgOpenGPS.
Done when: we can break any tractor and the plan repairs with no human.

### Zayyan
- [x] Breakdown recovery, no lost or repeated work
- [x] Safety check before every plan is sent
- [x] Five named agents with a log
- [x] Plain explanation of each plan change

### Ahmed
- [x] AgOpenGPS link driving one tractor
- [x] "Break this tractor" button
- [x] Breakdown test cases

### Vishva
- [x] Random farm maker
- [x] Export event log and comparison numbers
- [x] Tests for the basic rules
- [ ] Deck: problem, product, architecture

**Merge 2 at 3:00**

## Phase 3: Learning, proof, polish (to 4:00)
Goal: the numbers and finish that make it feel like a real product.

### Zayyan
- [ ] Learning: estimates improve day by day
- [ ] LLM for breakdown reasoning and explanations
- [ ] "Why?" for any job

### Ahmed
- [ ] Proof run on many random farms
- [ ] Blocked roads and changed priorities
- [ ] Map polish

### Vishva
- [x] Accuracy chart
- [x] "Why?" panel
- [ ] Screenshots and numbers in the deck
- [ ] README finished

**Merge 3 at 4:00. Fixes only after this.**

## Phase 4: Present (to 4:50)

### Zayyan
- [ ] Two timed rehearsals
- [ ] Final pitch

### Ahmed
- [ ] Hand-in files checked
- [ ] Ready for questions on the link and tests

### Vishva
- [ ] Backup video recorded
- [ ] Deck final
- [ ] Submitted at 4:50

## If we fall behind
- Behind at 2:00: skip learning and the LLM.
- AgOpenGPS link not working at 3:00: demo on our simulation.
- Broken at 4:00: remove it.

## Open questions
- [ ] Hand-in at 5:00 or 5:30?
- [ ] Does ATC have a starter dispatcher to compare against?
- [ ] What does an hour of tractor time cost?
- [ ] Which LLM for Phase 3?
