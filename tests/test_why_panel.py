"""The "Why?" panel works offline, never trusts server text as HTML, and the
state still has every field and log wording it builds its explanation from."""

import re
from pathlib import Path

from foreman.engine import Engine

PANELS = Path(__file__).resolve().parent.parent / "web" / "panels"


def test_panel_has_no_outside_resources_and_no_html_injection() -> None:
    script = (PANELS / "why.js").read_text(encoding="utf-8")
    page = (PANELS / "why.html").read_text(encoding="utf-8")
    for source in (script, page):
        assert not re.search(r"https?://(?!localhost)", source), "must work with no network"
        assert "innerHTML" not in source and "insertAdjacentHTML" not in source
    assert 'getElementById("why-panel")' in script
    assert "WHY_PANEL_NO_POLL" in script
    assert "/api/why?job=" in script


def test_state_has_the_fields_the_panel_reads() -> None:
    engine = Engine()
    engine.advance(240)  # past the sample farm's 09:30 breakdown
    state = engine.state()
    foreman = state["foreman"]
    assert {"job", "tractor", "implement", "start", "finish", "late_min", "path"} <= foreman["plan"][0].keys()
    assert {"id", "type", "field", "priority", "deadline", "pct", "status", "tractor"} <= foreman["jobs"][0].keys()
    assert {"id", "name", "status", "fuel_pct", "node"} <= foreman["tractors"][0].keys()
    assert {"now", "clock"} <= state.keys()

    # The Safety check ids the panel quotes: 2 is the implement check, 3 the fuel check.
    checks = {check["id"]: check["name"].lower() for check in foreman["safety"]["checks"]}
    assert "implement" in checks[2] and "fuel" in checks[3]


def test_log_wording_the_panel_parses() -> None:
    engine = Engine()
    engine.advance(240)
    log = engine.state()["foreman"]["log"]
    # "Implement picker, Plant West Bench: Atlas can pull the ...; Bolt has no ..."
    picker = [line for line in log if line["agent"] == "Fleet" and line["event"] == "implements"
              and (line.get("data") or {}).get("job")]
    assert picker, "the Fleet agent should log its verdicts for jobs that need a new tractor"
    assert re.match(r"Implement picker, [A-Z][a-z]+ .+: .+ can pull the ", picker[0]["message"])
    # "<tractor> started: <Type> <Field name>" and "... finished: ..."
    assert any(re.search(r" started: [A-Z][a-z]+ ", line["message"]) for line in log)
    assert any(re.search(r" finished: [A-Z][a-z]+ ", line["message"]) for line in log)
