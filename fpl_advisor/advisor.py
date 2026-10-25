"""Service layer shared by the web app, CLI and MCP server."""

from __future__ import annotations

import threading
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import pandas as pd

from fpl_advisor import api, features, ingest, model, optimize


@dataclass
class Advice:
    team_id: int
    team_name: str
    manager: str
    picks_gameweek: int
    gameweek: int  # the gameweek being predicted
    data_through_gameweek: int
    bank: float
    free_transfers: int
    squad: list[dict[str, Any]]
    recommendations: list[dict[str, Any]]
    notes: list[str] = field(default_factory=list)


class Advisor:
    """Predictions are cached per (snapshot, gameweek); a new snapshot also reloads the model."""

    def __init__(
        self,
        client: api.FPLClient | None = None,
        data_dir: Path | None = None,
        model_path: Path | None = None,
    ) -> None:
        self.data_dir = Path(data_dir or ingest.DATA_DIR)
        self.model_path = Path(model_path or model.MODEL_PATH)
        self.client = client or api.FPLClient(cache_dir=ingest.CACHE_DIR, ttl_seconds=900)
        self._cache: dict[tuple[str, int], pd.DataFrame] = {}
        self._lock = threading.Lock()

    def snapshot(self) -> Path:
        snap = features.latest_snapshot(self.data_dir)
        if snap is None:
            raise FileNotFoundError("No data snapshot; run `python -m fpl_advisor.ingest`")
        return snap

    def predictions(self, gameweek: int | None = None) -> tuple[int, pd.DataFrame]:
        """Predicted points for every player for ``gameweek`` (default: next gameweek)."""
        bootstrap = self.client.bootstrap()
        state = api.gameweek_state(bootstrap)
        target = gameweek or state.next or state.current
        if target is None:
            raise ValueError("Season is over: no upcoming gameweek")
        snap = self.snapshot()
        key = (snap.name, target)
        with self._lock:
            if key not in self._cache:
                fitted = model.load(self.model_path)["model"]  # retrained with each snapshot
                players = api.parse_players(bootstrap)
                preds = model.predict_gameweek(fitted, snap, target, players=players)
                teams = api.parse_teams(bootstrap).set_index("team")["short_name"]
                preds["team_name"] = preds["team"].map(teams)
                self._cache = {key: preds}  # keep only the latest gameweek
            return target, self._cache[key]

    def advise(self, team: str | int, gameweek: int | None = None) -> Advice:
        """Recommendations for the squad held after ``gameweek`` (default: current)."""
        team_id = api.parse_team_id(team)
        state = api.gameweek_state(self.client.bootstrap())
        picks_gw = gameweek or api.parse_gameweek_from_url(team) or state.current
        if picks_gw is None:
            raise ValueError("The season has not started yet: no squad to advise on")

        try:
            entry = self.client.entry(team_id)
        except api.NotFoundError as exc:
            raise api.NotFoundError(f"Team {team_id} not found (deleted or private)") from exc
        try:
            picks = api.parse_picks(self.client.picks(team_id, picks_gw))
        except api.NotFoundError as exc:
            raise api.NotFoundError(f"Team {team_id} has no picks for gameweek {picks_gw}") from exc

        notes = [
            "Selling prices are approximated with current prices (the public API does not "
            "expose them), so the budget may be slightly optimistic."
        ]
        if picks.active_chip == "freehit" and picks_gw > 1:
            # A free hit squad reverts afterwards: advise on the squad from the week before.
            picks = api.parse_picks(self.client.picks(team_id, picks_gw - 1))
            notes.append(f"Free hit played in GW{picks_gw}: using the GW{picks_gw - 1} squad.")

        free = api.free_transfers(self.client.entry_history(team_id), up_to_gameweek=picks_gw)
        target, preds = self.predictions(picks_gw + 1)
        snap_gw = int(self.snapshot().name.split("=")[1])
        if state.last_finished and snap_gw < state.last_finished:
            notes.append(
                f"GW{state.last_finished} has finished but is not ingested yet; "
                f"form data runs to GW{snap_gw}."
            )

        squad_ids = [p.player_id for p in picks.picks]
        plans = optimize.recommend(preds, squad_ids, picks.bank, free)
        view = PlayerView(preds)
        return Advice(
            team_id=team_id,
            team_name=entry["name"],
            manager=f"{entry['player_first_name']} {entry['player_last_name']}",
            picks_gameweek=picks_gw,
            gameweek=target,
            data_through_gameweek=snap_gw,
            bank=picks.bank,
            free_transfers=free,
            squad=view.many(squad_ids, sort=True),
            recommendations=[view.plan(rank, plan) for rank, plan in enumerate(plans, 1)],
            notes=notes,
        )


class PlayerView:
    """Turns player IDs and plans into plain dicts for JSON and tables."""

    def __init__(self, preds: pd.DataFrame) -> None:
        self.df = preds.set_index("player_id")

    def one(self, player_id: int) -> dict[str, Any]:
        r = self.df.loc[player_id]
        return {
            "id": int(player_id),
            "name": r["web_name"],
            "team": r["team_name"],
            "position": r["position"],
            "price": float(r["price"]),
            "predicted_points": round(float(r["predicted_points"]), 2),
            "fixtures": int(r["n_fixtures"]),
            "availability": round(float(r["availability"]), 2),
            "ep_next": None if pd.isna(r["ep_next"]) else float(r["ep_next"]),
            "news": r["news"] or "",
        }

    def many(self, ids: list[int], sort: bool = False) -> list[dict[str, Any]]:
        rows = [self.one(p) for p in ids]
        return sorted(rows, key=lambda r: -r["predicted_points"]) if sort else rows

    def plan(self, rank: int, plan: optimize.Plan) -> dict[str, Any]:
        return {
            "rank": rank,
            "n_transfers": plan.n_transfers,
            "transfers_out": self.many(plan.transfers_out),
            "transfers_in": self.many(plan.transfers_in),
            "hit_cost": plan.hit_cost,
            "expected_points": round(plan.expected_points, 2),
            "net_points": round(plan.net_points, 2),
            "gain": round(plan.gain, 2),
            "reason": plan.reason,
            "captain": self.one(plan.captain),
            "vice_captain": self.one(plan.vice_captain),
            "starting": self.many(plan.starting),
            "bench": self.many(plan.bench),
        }

    def ranked(self, position: str | None = None, limit: int | None = None) -> list[dict]:
        df = self.df if position is None else self.df[self.df["position"] == position]
        ids = df.sort_values("predicted_points", ascending=False).index[:limit]
        return self.many(list(ids))
