# FleetForeman public demo. Runs the server on the port the host gives in $PORT (7860 if none).
# No model, no token and no Claude Code CLI in this image: the two AI agents are off.
FROM python:3.11-slim

RUN useradd --create-home --uid 1000 foreman
WORKDIR /app


RUN pip install --no-cache-dir fastapi uvicorn

COPY --chown=foreman:foreman foreman ./foreman
COPY --chown=foreman:foreman web ./web
COPY --chown=foreman:foreman scenarios ./scenarios
COPY --chown=foreman:foreman tools ./tools
COPY --chown=foreman:foreman bridge ./bridge

USER foreman
ENV FOREMAN_DEMO=1 \
    FOREMAN_HOST=0.0.0.0 \
    FOREMAN_AI=0 \
    PYTHONUNBUFFERED=1
EXPOSE 7860
CMD ["sh", "-c", "exec python -m foreman.server --port ${PORT:-7860} --runs /tmp/foreman-runs"]
