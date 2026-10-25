import json
from pathlib import Path

import pandas as pd

from fpl_advisor import api, ingest

DATA = Path(__file__).parent / "data"


class FakeClient:
    """Serves the saved samples; every player gets the same element-summary."""

    def __init__(self) -> None:
        self.summary_calls = 0

    def bootstrap(self):
        return json.loads((DATA / "bootstrap-static.json").read_text())

    def fixtures(self):
        return json.loads((DATA / "fixtures.json").read_text())

    def element_summary(self, player_id: int):
        self.summary_calls += 1
        summary = json.loads((DATA / "element-summary.json").read_text())
        for row in summary["history"]:
            row["element"] = player_id
            row["round"] = row["round"] * 3  # spread rounds so some are after GW7
        return summary


def test_snapshot_writes_parquet_and_is_idempotent(tmp_path) -> None:
    client = FakeClient()
    folder = ingest.snapshot_latest(client, tmp_path)
    assert folder == tmp_path / "snapshots" / "gw=07"
    for name in ingest.SNAPSHOT_FILES:
        assert (folder / f"{name}.parquet").exists()

    history = pd.read_parquet(folder / "history.parquet")
    assert history["round"].max() <= 7  # in-progress/future rounds excluded
    assert set(history["season"]) == {"2026-27"}
    players = pd.read_parquet(folder / "players.parquet")
    assert set(players["ep_next_gw"]) == {9}

    calls = client.summary_calls
    assert ingest.snapshot_latest(client, tmp_path) is None
    assert client.summary_calls == calls  # second run does nothing
    assert len(pd.read_parquet(folder / "history.parquet")) == len(history)


def test_parse_past_season_normalises_columns() -> None:
    df = ingest.parse_past_season((DATA / "merged_gw.csv").read_text(), "2024-25")
    assert len(df) == 4  # assistant-manager row dropped
    assert set(api.HISTORY_COLUMNS) <= set(df.columns)
    assert df["element_type"].between(1, 4).all()
    assert df["was_home"].dtype == bool
    assert df["value"].max() < 20  # converted from tenths
