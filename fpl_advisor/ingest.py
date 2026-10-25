"""Snapshot finished gameweeks to parquet and download optional past-season data.

Usage: ``python -m fpl_advisor.ingest [--past-seasons 2024-25 2025-26] [--force]``
"""

from __future__ import annotations

import argparse
import io
import logging
import os
from pathlib import Path

import pandas as pd
import requests

from fpl_advisor import api

log = logging.getLogger(__name__)

DATA_DIR = Path(os.environ.get("FPL_DATA_DIR", "data"))
SNAPSHOT_FILES = ("players", "history", "fixtures", "teams")
DONE_MARKER = "_SUCCESS"  # written last; readers ignore folders without it
CACHE_DIR = Path(os.environ.get("FPL_CACHE_DIR", DATA_DIR / "cache"))
# MIT-licensed community dataset (github.com/vaastav/Fantasy-Premier-League).
MAX_PAST_SEASON_BYTES = 100 * 1024 * 1024  # merged_gw.csv is ~10 MB per season
PAST_SEASON_URL = "https://raw.githubusercontent.com/vaastav/Fantasy-Premier-League/master/data/{season}/gws/merged_gw.csv"
DEFAULT_PAST_SEASONS = ("2022-23", "2023-24", "2024-25", "2025-26")
_POSITION_CODES = {"GK": 1, "GKP": 1, "DEF": 2, "MID": 3, "FWD": 4}


def snapshot_dir(data_dir: Path, gameweek: int) -> Path:
    return data_dir / "snapshots" / f"gw={gameweek:02d}"


def snapshot_exists(data_dir: Path, gameweek: int) -> bool:
    return (snapshot_dir(data_dir, gameweek) / DONE_MARKER).exists()


def build_season_history(
    client: api.FPLClient, players: pd.DataFrame, last_finished: int
) -> pd.DataFrame:
    """Per-player per-fixture rows for the current season, up to ``last_finished``.

    Players with zero minutes all season are skipped: their history would be all zeros and
    fetching them costs ~200 extra requests.
    """
    frames = []
    active = players.loc[players["minutes"] > 0, "player_id"].tolist()
    for i, player_id in enumerate(active, 1):
        if i % 50 == 0:
            log.info("element-summary %d/%d", i, len(active))
        frames.append(api.parse_history(client.element_summary(player_id)))
    history = pd.concat(frames, ignore_index=True)
    return history[history["round"] <= last_finished].reset_index(drop=True)


def write_snapshot(data_dir: Path, gameweek: int, tables: dict[str, pd.DataFrame]) -> Path:
    """Write all tables, then a marker file, so readers never see a half-written snapshot.

    A marker rather than a directory rename, because renames are not atomic (or not
    supported) on the Cloud Storage FUSE mount used in deployment.
    """
    folder = snapshot_dir(data_dir, gameweek)
    (folder / DONE_MARKER).unlink(missing_ok=True)
    folder.mkdir(parents=True, exist_ok=True)
    for name, df in tables.items():
        df.to_parquet(folder / f"{name}.parquet", index=False)
    (folder / DONE_MARKER).touch()
    return folder


def snapshot_latest(client: api.FPLClient, data_dir: Path, force: bool = False) -> Path | None:
    """Snapshot the last finished gameweek. Returns the folder, or None if nothing to do."""
    bootstrap = client.bootstrap()
    state = api.gameweek_state(bootstrap)
    if state.last_finished is None:
        log.info("No finished gameweek yet")
        return None
    if snapshot_exists(data_dir, state.last_finished) and not force:
        log.info("Snapshot for GW%d already exists, skipping", state.last_finished)
        return None

    players = api.parse_players(bootstrap)
    players["season"] = state.season
    players["snapshot_gw"] = state.last_finished
    players["ep_next_gw"] = state.next  # ep_next refers to this gameweek
    history = build_season_history(client, players, state.last_finished)
    history["season"] = state.season
    tables = {
        "players": players,
        "history": history,
        "fixtures": api.parse_fixtures(client.fixtures()),
        "teams": api.parse_teams(bootstrap),
    }
    folder = write_snapshot(data_dir, state.last_finished, tables)
    log.info("Wrote %s (%d history rows)", folder, len(history))
    return folder


def parse_past_season(csv_text: str, season: str) -> pd.DataFrame:
    """Normalise a community ``merged_gw.csv`` to ``HISTORY_COLUMNS`` + position, xP, season."""
    df = pd.read_csv(io.StringIO(csv_text))
    df = df[df["position"].isin(_POSITION_CODES)]  # drops 2024-25 "AM" (assistant managers)
    df = df.rename(columns={"element": "player_id", "GW": "gw"})
    if "round" not in df:
        df["round"] = df["gw"]
    for col in api.HISTORY_COLUMNS:
        if col not in df:
            df[col] = pd.NA  # older seasons lack expected_* columns
    out = df[api.HISTORY_COLUMNS].copy()
    out["value"] = out["value"].astype(float) / 10
    out["was_home"] = out["was_home"].astype(str).str.lower().eq("true")
    out["element_type"] = df["position"].map(_POSITION_CODES).astype(int)
    out["web_name"] = df["name"]
    out["xp"] = pd.to_numeric(df["xP"], errors="coerce")
    out["season"] = season
    return out.reset_index(drop=True)


def download_past_seasons(seasons: list[str], data_dir: Path, force: bool = False) -> list[Path]:
    paths = []
    for season in seasons:
        path = data_dir / "external" / f"season={season}.parquet"
        if path.exists() and not force:
            paths.append(path)
            continue
        with requests.get(
            PAST_SEASON_URL.format(season=season),
            headers={"User-Agent": api.USER_AGENT},
            timeout=(5, 60),
            allow_redirects=False,  # fixed host only
            stream=True,
        ) as response:
            if response.status_code != 200:
                log.warning("Past season %s unavailable (HTTP %d)", season, response.status_code)
                continue
            body = api.read_limited(response, MAX_PAST_SEASON_BYTES)
        path.parent.mkdir(parents=True, exist_ok=True)
        parse_past_season(body.decode("utf-8"), season).to_parquet(path, index=False)
        log.info("Wrote %s", path)
        paths.append(path)
    return paths


def main(argv: list[str] | None = None) -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--data-dir", type=Path, default=DATA_DIR)
    parser.add_argument("--force", action="store_true", help="rebuild existing outputs")
    parser.add_argument(
        "--past-seasons",
        nargs="*",
        default=list(DEFAULT_PAST_SEASONS),
        help="community-dataset seasons to download (pass none to skip)",
    )
    args = parser.parse_args(argv)
    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(message)s")

    client = api.FPLClient(cache_dir=CACHE_DIR)
    snapshot_latest(client, args.data_dir, force=args.force)
    if args.past_seasons:
        download_past_seasons(args.past_seasons, args.data_dir, force=args.force)


if __name__ == "__main__":
    main()
