"""The accuracy chart works offline, never trusts server text as HTML, and has an empty state."""

import re
from pathlib import Path

PANELS = Path(__file__).resolve().parent.parent / "web" / "panels"


def test_panel_has_no_outside_resources_and_no_html_injection() -> None:
    script = (PANELS / "accuracy.js").read_text(encoding="utf-8")
    page = (PANELS / "accuracy.html").read_text(encoding="utf-8")
    for source in (script, page):
        assert not re.search(r"https?://(?!localhost)", source), "must work with no network"
        assert "innerHTML" not in source and "insertAdjacentHTML" not in source
    assert 'getElementById("accuracy-panel")' in script
    assert "ACCURACY_PANEL_NO_POLL" in script
    assert 'src="accuracy.js"' in page


def test_panel_reads_the_learning_chart_and_has_an_empty_state() -> None:
    script = (PANELS / "accuracy.js").read_text(encoding="utf-8")
    assert "/api/learning/chart" in script
    for key in ("days", "day", "gap_pct", "samples"):
        assert key in script
    assert "No learning data yet" in script


def test_engine_learning_chart_has_days() -> None:
    from foreman.engine import Engine

    chart = Engine().learning_chart()
    assert chart["days"]
    for day in chart["days"]:
        assert {"day", "gap_pct"} <= day.keys()
