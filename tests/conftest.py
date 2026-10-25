import pandas as pd
import pytest

from fpl_advisor import optimize
from fpl_advisor.advisor import Advice, PlayerView
from fpl_advisor.api import POSITIONS
from tests.test_optimize import random_market, random_squad


def sample_predictions() -> pd.DataFrame:
    preds = random_market(3)
    preds["web_name"] = "Player" + preds["player_id"].astype(str)
    preds["team_name"] = "T" + preds["team"].astype(str)
    preds["position"] = preds["element_type"].map(POSITIONS)
    preds["n_fixtures"] = 1
    preds["availability"] = 1.0
    preds["ep_next"] = 2.0
    preds["news"] = ""
    return preds


def sample_advice() -> Advice:
    preds = sample_predictions()
    squad = random_squad(preds, 3)
    plans = optimize.recommend(preds, squad, bank=1.0, free_transfers=1)
    view = PlayerView(preds)
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
        recommendations=[view.plan(i, p) for i, p in enumerate(plans, 1)],
        notes=["test note"],
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

    def predictions(self, gameweek=None):
        if self.error:
            raise self.error
        return 9, sample_predictions()


@pytest.fixture
def fake_advisor() -> FakeAdvisor:
    return FakeAdvisor()
