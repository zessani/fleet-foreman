"""The decisions panel works offline and never trusts server text as HTML."""

import re
from pathlib import Path

PANELS = Path(__file__).resolve().parent.parent / "web" / "panels"


def test_panel_has_no_outside_resources_and_no_html_injection() -> None:
    script = (PANELS / "decisions.js").read_text(encoding="utf-8")
    page = (PANELS / "decisions.html").read_text(encoding="utf-8")
    for source in (script, page):
        assert not re.search(r"https?://(?!localhost)", source), "must work with no network"
        assert "innerHTML" not in source and "insertAdjacentHTML" not in source
    assert 'getElementById("decisions-panel")' in script
    assert "DECISIONS_PANEL_NO_POLL" in script


def test_panel_reads_only_fields_the_state_has() -> None:
    from foreman.engine import Engine

    script = (PANELS / "decisions.js").read_text(encoding="utf-8")
    foreman = Engine().state()["foreman"]
    for key in ("explanation", "safety", "log", "plan_no"):
        assert key in foreman and f"foreman.{key}" in script
    for key in ("headline", "summary", "changes", "ms", "moved", "retimed", "unchanged", "held"):
        assert key in foreman["explanation"] and f"story.{key}" in script
    assert {"id", "name", "passed", "severity", "detail"} <= foreman["safety"]["checks"][0].keys()
    assert {"clock", "agent", "message"} <= foreman["log"][0].keys()
