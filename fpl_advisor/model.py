"""Train the points model and predict the next gameweek.

Usage: ``python -m fpl_advisor.model`` trains on all available history and saves the model.
"""

from __future__ import annotations

import argparse
import logging
from pathlib import Path

import joblib
import numpy as np
import pandas as pd
from sklearn.ensemble import HistGradientBoostingRegressor

from fpl_advisor import features, ingest

log = logging.getLogger(__name__)

MODEL_PATH = Path("models/model.joblib")
# Status codes that mean the player will not play: injured, suspended, unavailable, not in squad.
_OUT_STATUSES = {"i", "s", "u", "n"}


def make_model(feature_names: list[str]) -> HistGradientBoostingRegressor:
    return HistGradientBoostingRegressor(
        max_iter=300,
        learning_rate=0.05,
        min_samples_leaf=100,
        l2_regularization=1.0,
        categorical_features=[f == "element_type" for f in feature_names],
        random_state=0,
    )


def train(df: pd.DataFrame, feature_names: list[str] = features.FEATURES):
    """Fit on rows with a known target. ``df`` must already have features built."""
    rows = df[df[features.TARGET].notna()]
    model = make_model(feature_names)
    model.fit(rows[feature_names], rows[features.TARGET])
    return model


def save(model, path: Path = MODEL_PATH, **meta) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    joblib.dump({"model": model, "features": list(features.FEATURES), **meta}, path)
    return path


def load(path: Path = MODEL_PATH) -> dict:
    if not path.exists():
        raise FileNotFoundError(f"No model at {path}; run `python -m fpl_advisor.model`")
    return joblib.load(path)


def availability(players: pd.DataFrame) -> pd.Series:
    """Probability of playing, from ``chance_of_playing_next_round`` and ``status``."""
    chance = players["chance_of_playing_next_round"] / 100
    out = players["status"].isin(_OUT_STATUSES).map({True: 0.0, False: 1.0})
    return chance.where(chance.notna(), out).astype(float)


def upcoming_rows(
    players: pd.DataFrame, fixtures: pd.DataFrame, gameweek: int, season: str
) -> pd.DataFrame:
    """One row per player per fixture in ``gameweek`` (none for a blank, two for a double)."""
    gw = fixtures[fixtures["event"] == gameweek]
    sides = pd.concat(
        [
            gw.assign(team=gw["team_h"], opponent_team=gw["team_a"], was_home=True),
            gw.assign(team=gw["team_a"], opponent_team=gw["team_h"], was_home=False),
        ]
    )[["fixture", "kickoff_time", "team", "opponent_team", "was_home"]]
    rows = players[["player_id", "team", "element_type", "web_name", "price"]].merge(
        sides, on="team"
    )
    rows = rows.rename(columns={"price": "value"})
    rows["season"] = season
    rows["round"] = gameweek
    rows[features.TARGET] = np.nan
    return rows


def predict_gameweek(
    model, snapshot: Path, gameweek: int, players: pd.DataFrame | None = None
) -> pd.DataFrame:
    """Expected points per player for ``gameweek``, using history in ``snapshot``.

    ``players`` (from the live bootstrap) overrides the snapshot's players table so that
    prices and injury news are current.
    """
    snap_players = pd.read_parquet(snapshot / "players.parquet")
    players = snap_players if players is None else players
    season = snap_players["season"].iloc[0]
    fixtures = pd.read_parquet(snapshot / "fixtures.parquet")
    history = pd.read_parquet(snapshot / "history.parquet")
    history = features.add_team(
        history.merge(players[["player_id", "element_type", "web_name"]], on="player_id")
    )
    history = history[history["round"] < gameweek]
    upcoming = upcoming_rows(players, fixtures, gameweek, season)
    built = features.build_features(pd.concat([history, upcoming], ignore_index=True))
    rows = built[built[features.TARGET].isna() & (built["round"] == gameweek)].copy()

    # Players never fetched had zero minutes all season: their form is zero, not unknown.
    if not history.empty:
        unseen = ~rows["player_id"].isin(history["player_id"])
        rows.loc[unseen, features.FORM_FEATURES] = 0.0

    rows["pred"] = model.predict(rows[features.FEATURES])
    per_player = rows.groupby("player_id").agg(
        n_fixtures=("fixture", "size"), raw_points=("pred", "sum")
    )
    out = players.set_index("player_id").join(per_player, how="left")
    out["n_fixtures"] = out["n_fixtures"].fillna(0).astype(int)
    out["raw_points"] = out["raw_points"].fillna(0.0)
    out["availability"] = availability(out)
    out["predicted_points"] = out["raw_points"] * out["availability"]
    cols = ["web_name", "team", "position", "element_type", "price", "status", "news"]
    cols += ["n_fixtures", "availability", "raw_points", "predicted_points", "ep_next"]
    return out[[c for c in cols if c in out]].reset_index()


def main(argv: list[str] | None = None) -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--data-dir", type=Path, default=ingest.DATA_DIR)
    parser.add_argument("--out", type=Path, default=MODEL_PATH)
    args = parser.parse_args(argv)
    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(message)s")

    df = features.build_features(features.load_history(args.data_dir))
    model = train(df)
    seasons = sorted(df["season"].unique())
    path = save(model, args.out, seasons=seasons, rows=int(df[features.TARGET].notna().sum()))
    log.info("Saved %s (seasons %s)", path, ", ".join(seasons))


if __name__ == "__main__":
    main()
