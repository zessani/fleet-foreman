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


def test_panel_reads_the_agreed_learning_shape_and_has_an_empty_state() -> None:
    script = (PANELS / "accuracy.js").read_text(encoding="utf-8")
    assert "state.foreman.learning" in script
    for key in ("days", "day", "error_pct", "jobs"):
        assert key in script
    assert "Learning data appears after day 1" in script


def test_today_state_without_learning_data_is_handled() -> None:
    """Until Zayyan adds learning data, the state has none and the panel shows its message."""
    from foreman.engine import Engine

    foreman = Engine().state()["foreman"]
    learning = foreman.get("learning")
    if learning is not None:
        # Once it exists it must match the agreed shape.
        for day in learning["days"]:
            assert {"day", "error_pct"} <= day.keys()
