"""Feature building. One row per player per fixture; features only use earlier gameweeks.

Rolling stats are computed on per-round aggregates and shifted by one round, so a row in
gameweek N (including both legs of a double gameweek) only sees gameweeks before N.
Rows whose target is NaN are treated as upcoming fixtures (used for prediction).
"""

from __future__ import annotations

from pathlib import Path

import pandas as pd

from fpl_advisor import ingest

TARGET = "total_points"
KEYS = ["season", "player_id"]
WINDOWS = (3, 5)

_PLAYER_STATS = {
    "pts": "total_points",
    "min": "minutes",
    "xg": "expected_goals",
    "xa": "expected_assists",
    "ict": "ict_index",
    "starts": "starts",
    "bonus": "bonus",
}
_TEAM_STATS = ["gf", "ga", "xg", "xga"]

FORM_FEATURES = [f"{name}_{w}" for w in WINDOWS for name in _PLAYER_STATS] + [
    "pts_season",
    "min_season",
]
CONTEXT_FEATURES = [
    "was_home",
    "value",
    "element_type",
    *[f"opp_{c}_5" for c in _TEAM_STATS],
    *[f"team_{c}_5" for c in _TEAM_STATS],
]
FEATURES = FORM_FEATURES + CONTEXT_FEATURES


def add_team(df: pd.DataFrame) -> pd.DataFrame:
    """Derive each row's own team from the opposite side's ``opponent_team`` in that fixture."""
    sides = (
        df.groupby(["season", "fixture", "was_home"])["opponent_team"]
        .first()
        .rename("team")
        .reset_index()
    )
    sides["was_home"] = ~sides["was_home"].astype(bool)  # home rows' team = away rows' opponent
    out = df.drop(columns=["team"], errors="ignore")
    return out.merge(sides, on=["season", "fixture", "was_home"], how="left")


def _prior_mean(frame: pd.DataFrame, by: list[str], cols: list[str], window: int | None):
    """Per-fixture mean of ``cols`` over the previous ``window`` rounds (None = season so far).

    ``frame`` has one row per group per round, with per-round sums in ``cols`` and the
    number of fixtures played in ``n``.
    """
    shifted = frame.groupby(by, sort=False)[[*cols, "n"]].shift(1)
    shifted[by] = frame[by]
    grouped = shifted.groupby(by, sort=False)[[*cols, "n"]]
    if window is None:
        totals = grouped.cumsum()
    else:
        totals = grouped.rolling(window, min_periods=1).sum()
        totals = totals.reset_index(level=list(range(len(by))), drop=True).reindex(frame.index)
    return totals[cols].div(totals["n"].where(totals["n"] > 0), axis=0)


def _player_form(df: pd.DataFrame) -> pd.DataFrame:
    agg = {name: (col, "sum") for name, col in _PLAYER_STATS.items()}
    # count() skips NaN targets, so an upcoming round adds zero played fixtures.
    rounds = df.groupby([*KEYS, "round"]).agg(**agg, n=(TARGET, "count")).reset_index()
    cols = list(_PLAYER_STATS)
    out = rounds[[*KEYS, "round"]].copy()
    for w in WINDOWS:
        out[[f"{c}_{w}" for c in cols]] = _prior_mean(rounds, KEYS, cols, w).to_numpy()
    out[["pts_season", "min_season"]] = _prior_mean(rounds, KEYS, ["pts", "min"], None).to_numpy()
    return out


def _team_strength(df: pd.DataFrame) -> pd.DataFrame:
    """Goals and xG for/against per fixture, averaged over each team's previous 5 rounds."""
    played = df[df[TARGET].notna()]
    per_fixture = (
        played.groupby(["season", "fixture", "team", "round"])
        .agg(
            was_home=("was_home", "first"),
            h=("team_h_score", "first"),
            a=("team_a_score", "first"),
            xg=("expected_goals", "sum"),
        )
        .reset_index()
    )
    home = per_fixture["was_home"].astype(bool)
    per_fixture["gf"] = per_fixture["h"].where(home, per_fixture["a"]).astype(float)
    per_fixture["ga"] = per_fixture["a"].where(home, per_fixture["h"]).astype(float)
    other = per_fixture[["season", "fixture", "team", "xg"]].rename(
        columns={"team": "other", "xg": "xga"}
    )
    per_fixture = per_fixture.merge(other, on=["season", "fixture"])
    per_fixture = per_fixture[per_fixture["team"] != per_fixture["other"]]

    by = ["season", "team", "round"]
    stats = per_fixture.groupby(by).agg(
        **{c: (c, "sum") for c in _TEAM_STATS}, n=("fixture", "size")
    )
    # Upcoming rounds get n = 0 so the shift carries the last played rounds onto them.
    rounds = (
        df[by]
        .drop_duplicates()
        .merge(stats.reset_index(), on=by, how="left")
        .fillna(dict.fromkeys([*_TEAM_STATS, "n"], 0))
        .sort_values(by)
        .reset_index(drop=True)
    )
    out = rounds[by].copy()
    means = _prior_mean(rounds, ["season", "team"], _TEAM_STATS, 5)
    out[[f"{c}_5" for c in _TEAM_STATS]] = means.to_numpy()
    return out


def build_features(df: pd.DataFrame) -> pd.DataFrame:
    """Return fixture rows with ``FEATURES`` added. Needs a ``team`` column or derives it."""
    df = df.copy()
    df["was_home"] = df["was_home"].astype(bool)
    if "team" not in df:
        df = add_team(df)
    df = df.merge(_player_form(df), on=[*KEYS, "round"], how="left")

    team = _team_strength(df)
    stat_cols = [f"{c}_5" for c in _TEAM_STATS]
    own = team.rename(columns={c: f"team_{c}" for c in stat_cols})
    opp = team.rename(columns={"team": "opponent_team", **{c: f"opp_{c}" for c in stat_cols}})
    df = df.merge(own, on=["season", "team", "round"], how="left")
    df = df.merge(opp, on=["season", "opponent_team", "round"], how="left")
    df["was_home"] = df["was_home"].astype(int)
    return df


def latest_snapshot(data_dir: Path = ingest.DATA_DIR) -> Path | None:
    snapshots = sorted(
        p for p in (data_dir / "snapshots").glob("gw=*") if p.is_dir() and p.suffix != ".tmp"
    )
    return snapshots[-1] if snapshots else None


def load_history(data_dir: Path = ingest.DATA_DIR) -> pd.DataFrame:
    """Past seasons (if downloaded) plus the current season from the latest snapshot."""
    frames = [pd.read_parquet(p) for p in sorted((data_dir / "external").glob("season=*.parquet"))]
    latest = latest_snapshot(data_dir)
    if latest is not None:
        current = pd.read_parquet(latest / "history.parquet")
        players = pd.read_parquet(latest / "players.parquet")
        current = current.merge(players[["player_id", "element_type", "web_name"]], on="player_id")
        frames.append(current)
    if not frames:
        raise FileNotFoundError(f"No data in {data_dir}; run `python -m fpl_advisor.ingest`")
    df = pd.concat(frames, ignore_index=True)
    df = add_team(df)
    return df.sort_values(["season", "round", "kickoff_time", "player_id"]).reset_index(drop=True)
