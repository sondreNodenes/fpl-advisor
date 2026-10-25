import numpy as np
import pandas as pd
import pytest

from fpl_advisor import features
from fpl_advisor.api import HISTORY_COLUMNS


def make_rows(schedule, seed: int = 0) -> pd.DataFrame:
    """schedule: list of (round, fixture, home_team, away_team). Two players per team."""
    rng = np.random.default_rng(seed)
    rows = []
    for rnd, fixture, home, away in schedule:
        hs, as_ = rng.integers(0, 4, size=2)
        for team, opp, was_home in ((home, away, True), (away, home, False)):
            for k in range(2):
                row = dict.fromkeys(HISTORY_COLUMNS, 0.0)
                row.update(
                    season="2026-27",
                    player_id=team * 10 + k,
                    fixture=fixture,
                    round=rnd,
                    kickoff_time=f"2026-08-{rnd:02d}T15:00:00Z",
                    opponent_team=opp,
                    was_home=was_home,
                    team_h_score=hs,
                    team_a_score=as_,
                    total_points=float(rng.integers(0, 12)),
                    minutes=float(rng.choice([0, 90])),
                    expected_goals=float(rng.random()),
                    value=5.0,
                    element_type=3,
                )
                rows.append(row)
    return pd.DataFrame(rows)


SCHEDULE = [(r, r * 10 + i, h, a) for r in range(1, 7) for i, (h, a) in enumerate([(1, 2), (3, 4)])]


def test_add_team_recovers_own_team() -> None:
    df = features.add_team(make_rows(SCHEDULE))
    assert (df["team"] == df["player_id"] // 10).all()


def test_features_use_only_previous_rounds() -> None:
    df = make_rows(SCHEDULE)
    base = features.build_features(df)

    changed = df.copy()
    in_round_4 = changed["round"] == 4
    changed.loc[in_round_4, ["total_points", "minutes", "expected_goals"]] = 99.0
    changed.loc[in_round_4, ["team_h_score", "team_a_score"]] = 9
    after = features.build_features(changed)

    cols = features.FEATURES
    upto_4 = base["round"] <= 4
    pd.testing.assert_frame_equal(
        base.loc[upto_4, cols].reset_index(drop=True),
        after.loc[upto_4, cols].reset_index(drop=True),
    )
    assert not base.loc[base["round"] == 5, cols].equals(after.loc[after["round"] == 5, cols])


def test_rolling_values() -> None:
    df = make_rows(SCHEDULE)
    out = features.build_features(df)
    player = out[out["player_id"] == 10].sort_values("round").set_index("round")
    pts = player["total_points"]
    assert np.isnan(player.loc[1, "pts_3"])
    assert player.loc[5, "pts_3"] == pytest.approx(pts.loc[2:4].mean())
    assert player.loc[6, "pts_5"] == pytest.approx(pts.loc[1:5].mean())
    assert player.loc[6, "pts_season"] == pytest.approx(pts.loc[1:5].mean())


def test_double_gameweek_rows_share_features_and_upcoming_rows_get_features() -> None:
    schedule = [*SCHEDULE, (7, 71, 1, 3), (7, 72, 2, 1)]  # team 1 has two fixtures in GW7
    df = make_rows(schedule)
    df.loc[df["round"] == 7, features.TARGET] = np.nan  # upcoming
    out = features.build_features(df)

    dgw = out[(out["player_id"] == 10) & (out["round"] == 7)]
    assert len(dgw) == 2
    form = [c for c in features.FORM_FEATURES]
    assert dgw[form].nunique().max() == 1
    hist = out[(out["player_id"] == 10) & (out["round"] <= 6)]
    assert dgw["pts_season"].iloc[0] == pytest.approx(hist["total_points"].mean())
    assert dgw["opp_gf_5"].notna().all()
