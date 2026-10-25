import pytest

from fpl_advisor import chips, optimize
from tests.conftest import CHIPS, GAMEWEEKS, sample_predictions
from tests.test_optimize import random_squad

WINDOWS = [
    {"name": "3xc", "number": 1, "start_event": 1, "stop_event": 19},
    {"name": "3xc", "number": 1, "start_event": 20, "stop_event": 38},
    {"name": "freehit", "number": 1, "start_event": 2, "stop_event": 19},
]


def test_available_respects_windows_and_usage() -> None:
    assert chips.available(WINDOWS, [], 1) == {"3xc": 19}  # free hit opens in GW2
    assert chips.available(WINDOWS, [{"name": "3xc", "event": 5}], 9) == {"freehit": 19}
    assert chips.available(WINDOWS, [{"name": "3xc", "event": 5}], 20) == {"3xc": 38}
    assert chips.unlocks(WINDOWS, 9) == {"3xc": 20}


def _advise(preds, used=()):
    squad = random_squad(preds, 3)
    columns = [f"pts_gw{gw}" for gw in GAMEWEEKS]
    weights = [1.0, 0.9, 0.81]
    plan = optimize.recommend(preds, squad, 1.0, 1, columns=columns, weights=weights)[0]
    usable = chips.available(CHIPS, [{"name": n, "event": 3} for n in used], GAMEWEEKS[0])
    advice = chips.advise(preds, squad, 1.0, plan, GAMEWEEKS, columns, weights, usable)
    return {a.chip: a for a in advice}, plan


def test_triple_captain_played_on_a_big_week() -> None:
    preds = sample_predictions()
    advice, plan = _advise(preds)
    assert advice["3xc"].values[0]["value"] == pytest.approx(
        preds.set_index("player_id").at[plan.captain, "pts_gw9"], abs=0.01
    )
    assert sum(a.verdict == "play" for a in advice.values()) <= 1  # one chip per gameweek

    preds.loc[preds["player_id"] == plan.captain, "pts_gw9"] = 30.0  # a double gameweek haul
    advice, _ = _advise(preds, used=["wildcard", "freehit", "bboost"])
    assert advice["3xc"].verdict == "play"
    assert "Play it now" in advice["3xc"].detail


def test_used_chip_is_unavailable() -> None:
    advice, _ = _advise(sample_predictions(), used=["bboost"])
    assert advice["bboost"].verdict == "unavailable"
    assert advice["bboost"].values == []
    assert advice["freehit"].verdict in {"play", "save"}
    assert all(v["value"] >= -1e-6 for v in advice["freehit"].values)  # never worse than plan
