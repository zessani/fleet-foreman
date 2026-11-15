# web

Owner: Ahmed

Dashboard page and farm map. The server serves `index.html` at `/`.

Rules (CLAUDE.md section 7): one HTML page, plain JavaScript and Canvas, no build step, no CDN. Poll `GET /api/state` a few times per second. No WebSockets.

Phase items (CLAUDE.md section 14): Phase 1 farm map with tractors moving and field progress. Phase 3 map polish.

The Phase 1 map renders roads, blocked links, farm nodes, planned paths,
tractor positions and states, and field-job progress. It includes the start,
pause, reset, and simulation-speed controls defined by `/api/control`.
