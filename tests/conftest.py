from dataclasses import asdict

import pandas as pd
import pytest

from fpl_advisor import chips, optimize
from fpl_advisor.advisor import Advice, PlayerView, horizon_weights, points_column
from fpl_advisor.api import POSITIONS
from tests.test_optimize import random_market, random_squad

GAMEWEEKS = [9, 10, 11]
CHIPS = [
    {"name": name, "number": 1, "start_event": 1, "stop_event": 19}
    for name in ("wildcard", "freehit", "bboost", "3xc")
]


def sample_predictions() -> pd.DataFrame:
    preds = random_market(3)
    preds["web_name"] = "Player" + preds["player_id"].astype(str)
    preds["team_name"] = "T" + preds["team"].astype(str)
    preds["position"] = preds["element_type"].map(POSITIONS)
    preds["n_fixtures"] = 1
    preds["availability"] = 1.0
    preds["ep_next"] = 2.0
    preds["news"] = ""
    preds["opponents"] = [[{"opponent": "ARS", "home": True, "difficulty": 4}]] * len(preds)
    for i, gw in enumerate(GAMEWEEKS):
        preds[points_column(gw)] = preds["predicted_points"] * (1 - 0.1 * i)
        preds[f"opp_gw{gw}"] = preds["opponents"]
    preds["horizon_points"] = preds[[points_column(gw) for gw in GAMEWEEKS]].sum(axis=1)
    return preds


def sample_advice() -> Advice:
    preds = sample_predictions()
    squad = random_squad(preds, 3)
    columns = [points_column(gw) for gw in GAMEWEEKS]
    weights = horizon_weights(len(GAMEWEEKS))
    plans = optimize.recommend(preds, squad, 1.0, 1, columns=columns, weights=weights)
    usable = chips.available(CHIPS, [{"name": "bboost", "event": 3}], GAMEWEEKS[0])
    chip_advice = chips.advise(preds, squad, 1.0, plans[0], GAMEWEEKS, columns, weights, usable)
    baseline = next(p for p in plans if p.n_transfers == 0)
    view = PlayerView(preds, GAMEWEEKS)
    return Advice(
        team_id=5767275,
        team_name="Test FC",
        manager="Test Manager",
        picks_gameweek=8,
        gameweek=9,
        data_through_gameweek=7,
        bank=1.0,
        free_transfers=1,
        squad=view.many(squad, sort=True),
        recommendations=[view.plan(i, p, baseline) for i, p in enumerate(plans, 1)],
        notes=["test note"],
        gameweeks=GAMEWEEKS,
        weights=weights,
        chips=[asdict(c) for c in chip_advice],
    )


class FakeAdvisor:
    """Stands in for ``Advisor``; raises ``error`` when set."""

    def __init__(self) -> None:
        self.error: Exception | None = None
        self.calls: list[tuple] = []

    def advise(self, team, gameweek=None):
        self.calls.append((team, gameweek))
        if self.error:
            raise self.error
        return sample_advice()

    def forecast(self, start=None):
        if self.error:
            raise self.error
        return GAMEWEEKS, sample_predictions()


@pytest.fixture
def fake_advisor() -> FakeAdvisor:
    return FakeAdvisor()
