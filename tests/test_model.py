import numpy as np
import pandas as pd
import pytest

from fpl_advisor import features, model
from tests.test_features import SCHEDULE, make_rows


def players_table() -> pd.DataFrame:
    ids = [10, 11, 20, 21, 30, 31, 40, 41]
    return pd.DataFrame(
        {
            "player_id": ids,
            "web_name": [f"P{i}" for i in ids],
            "team": [i // 10 for i in ids],
            "element_type": 3,
            "position": "MID",
            "price": 5.0,
            "status": ["a", "d", "a", "i", "a", "a", "a", "a"],
            "chance_of_playing_next_round": [np.nan, 50, np.nan, np.nan, np.nan, 0, 75, np.nan],
            "news": "",
            "ep_next": 1.0,
            "season": "2026-27",
        }
    )


def test_availability() -> None:
    got = model.availability(players_table()).tolist()
    assert got == [1.0, 0.5, 1.0, 0.0, 1.0, 0.0, 0.75, 1.0]


def test_upcoming_rows_blank_and_double() -> None:
    fixtures = pd.DataFrame(
        {
            "fixture": [71, 72],
            "event": [7, 7],
            "kickoff_time": ["t1", "t2"],
            "team_h": [1, 2],
            "team_a": [3, 1],
        }
    )
    rows = model.upcoming_rows(players_table(), fixtures, 7, "2026-27")
    per_player = rows.groupby("player_id").size()
    assert per_player[10] == 2  # team 1 plays twice
    assert per_player[20] == 1
    assert 40 not in per_player  # team 4 blanks
    assert rows["total_points"].isna().all()


class OnePointModel:
    def predict(self, x: pd.DataFrame) -> np.ndarray:
        assert list(x.columns) == features.FEATURES
        return np.ones(len(x))


def test_predict_gameweek_sums_fixtures_and_applies_availability(tmp_path) -> None:
    make_rows(SCHEDULE).drop(columns=["element_type"]).to_parquet(tmp_path / "history.parquet")
    players_table().to_parquet(tmp_path / "players.parquet")
    pd.DataFrame(
        {
            "fixture": [71, 72],
            "event": [7, 7],
            "kickoff_time": ["t1", "t2"],
            "team_h": [1, 2],
            "team_a": [3, 1],
            "finished": False,
        }
    ).to_parquet(tmp_path / "fixtures.parquet")

    out = model.predict_gameweek(OnePointModel(), tmp_path, 7).set_index("player_id")
    assert out.loc[10, "predicted_points"] == pytest.approx(2.0)  # double gameweek
    assert out.loc[11, "predicted_points"] == pytest.approx(1.0)  # double, 50% chance
    assert out.loc[21, "predicted_points"] == 0.0  # injured
    assert out.loc[40, "predicted_points"] == 0.0  # blank gameweek
    assert out.loc[40, "n_fixtures"] == 0
