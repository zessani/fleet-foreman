# foreman/nav

Owner: Ahmed

Road routing between the yard, junctions and fields.

`roadmap.py` holds a **stand-in** `RoadMap` that uses straight-line distance
and ignores roads. It exists so the planner can be built before real routing
is ready.

**Ahmed: replace the inside of `RoadMap`, keep the interface.** The interface
is CLAUDE.md section 8d (proposed, pending your agreement):

```python
RoadMap(farm)               # farm file as a dict, section 8a
    .distance(a, b) -> km   # math.inf if no route
    .path(a, b) -> [ids]    # a and b included, [] if no route
    .block(a, b)            # close a road
    .open(a, b)             # reopen it
```

What the real version needs
- Shortest path over `farm["roads"]` (two-way links). If `roads` is missing,
  every pair of nodes is directly linked.
- Work out all routes once at creation and again only after `block` or
  `open`. The planner calls `distance` thousands of times per plan, so it must
  be a lookup.
- A blocked road is removed from the graph.

`tests/test_skeleton.py` checks the interface. If you want to change the
interface, say so before editing; the planner depends on it.
