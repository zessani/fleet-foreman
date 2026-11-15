"""Loading a built-in farm or a random farm resets the day and tells the dashboard which farm it is."""

from foreman.engine import Engine, find_scenario


def test_both_built_in_scenarios_load() -> None:
    engine = Engine()
    for name, tractors, jobs in (("sample_farm", 3, 10), ("large_farm", 5, 25)):
        engine.load(find_scenario(name))
        state = engine.state()
        assert state["scenario"] == name and state["seed"] is None and state["clock"] == "06:00"
        assert len(state["foreman"]["tractors"]) == tractors and len(state["foreman"]["jobs"]) == jobs


def test_random_farm_is_checked_loaded_and_repeatable() -> None:
    engine = Engine()
    first = engine.load_random(seed=4242)
    assert first["ok"] and first["seed"] == 4242 and first["scenario"] == "random_4242"
    state = engine.state()
    assert state["seed"] == 4242 and state["scenario"] == "random_4242" and state["clock"] == "06:00"
    nodes = state["farm"]["nodes"]
    again = Engine()
    again.load_random(seed=4242)
    assert again.state()["farm"]["nodes"] == nodes  # the same seed gives the same farm
    other = engine.load_random()
    assert other["ok"] and isinstance(other["seed"], int)


def test_a_random_farm_runs_a_clean_day() -> None:
    engine = Engine()
    engine.load_random(seed=7)
    engine.control("start", 60)
    while not engine.finished:
        engine.tick(0.5)
    totals = engine.state()["foreman"]["totals"]
    assert totals["violations"] == 0 and totals["jobs_done"] > 0
