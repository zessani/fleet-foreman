# foreman/nav

Owner: Ahmed

Road routing between the yard, junctions and fields.

`roadmap.py` precomputes shortest paths over the farm's two-way road graph so
planner queries are plain lookups. It rebuilds routes only when a road is
blocked or reopened. The interface is defined in CLAUDE.md section 8d:

```python
RoadMap(farm)               # farm file as a dict, section 8a
    .distance(a, b) -> km   # math.inf if no route
    .path(a, b) -> [ids]    # a and b included, [] if no route
    .block(a, b)            # close a road
    .open(a, b)             # reopen it
```

Behavior
- Shortest path over `farm["roads"]` (two-way links). If `roads` is missing,
  every pair of nodes is directly linked.
- Work out all routes once at creation and again only after `block` or `open`.
  `distance` and `path` are lookups.
- A blocked road is removed from the graph.
- An unreachable destination returns `math.inf` and an empty path.

`tests/test_roadmap.py` covers routing, closures, disconnections, validation,
and farms without explicit roads.
