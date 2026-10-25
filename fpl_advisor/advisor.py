"""Service layer shared by the web app, CLI and MCP server."""

from __future__ import annotations

import threading
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any

import pandas as pd

from fpl_advisor import api, chips, features, ingest, model, optimize

HORIZON = 3  # gameweeks the transfer plan looks ahead
DISCOUNT = 0.9  # weight per week ahead: later predictions are less certain


def points_column(gameweek: int) -> str:
    return f"pts_gw{gameweek}"


def horizon_weights(n: int) -> list[float]:
    return [DISCOUNT**i for i in range(n)]


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
    gameweeks: list[int] = field(default_factory=list)  # the planning horizon
    weights: list[float] = field(default_factory=list)
    chips: list[dict[str, Any]] = field(default_factory=list)


class Advisor:
    """Predictions are cached per snapshot and gameweek; a new snapshot also reloads the model."""

    def __init__(
        self,
        client: api.FPLClient | None = None,
        data_dir: Path | None = None,
        model_path: Path | None = None,
    ) -> None:
        self.data_dir = Path(data_dir or ingest.DATA_DIR)
        self.model_path = Path(model_path or model.MODEL_PATH)
        self.client = client or api.FPLClient(cache_dir=ingest.CACHE_DIR, ttl_seconds=900)
        self._snapshot_name = ""
        self._cache: dict[int, pd.DataFrame] = {}
        self._lock = threading.Lock()

    def snapshot(self) -> Path:
        snap = features.latest_snapshot(self.data_dir)
        if snap is None:
            raise FileNotFoundError("No data snapshot; run `python -m fpl_advisor.ingest`")
        return snap

    def forecast(self, start: int | None = None) -> tuple[list[int], pd.DataFrame]:
        """Predicted points per player for the horizon starting at ``start`` (default: next).

        One row per player: the first gameweek's columns (``predicted_points``,
        ``opponents``, ...) plus ``pts_gw{N}`` and ``opp_gw{N}`` for each horizon gameweek.
        """
        bootstrap = self.client.bootstrap()
        state = api.gameweek_state(bootstrap)
        start = start or state.next or state.current
        if start is None:
            raise ValueError("Season is over: no upcoming gameweek")
        gameweeks = list(range(start, min(start + HORIZON, 39)))
        snap = self.snapshot()
        with self._lock:
            if snap.name != self._snapshot_name:  # retrained with each snapshot
                self._snapshot_name, self._cache = snap.name, {}
            missing = [gw for gw in gameweeks if gw not in self._cache]
            if missing:
                fitted = model.load(self.model_path)["model"]
                players = api.parse_players(bootstrap)
                teams = api.parse_teams(bootstrap).set_index("team")["short_name"]
                for gw in missing:
                    preds = model.predict_gameweek(fitted, snap, gw, players=players)
                    preds["team_name"] = preds["team"].map(teams)
                    preds["opponents"] = preds["opponents"].map(
                        lambda opps: [
                            {"opponent": teams.get(t, str(t)), "home": bool(h), "difficulty": d}
                            for t, h, d in opps
                        ]
                    )
                    self._cache[gw] = preds
            frames = {gw: self._cache[gw].set_index("player_id") for gw in gameweeks}
        out = frames[start].copy()
        for gw, frame in frames.items():
            out[points_column(gw)] = frame["predicted_points"]
            out[f"opp_gw{gw}"] = frame["opponents"]
        out["horizon_points"] = out[[points_column(gw) for gw in gameweeks]].sum(axis=1)
        return gameweeks, out.reset_index()

    def advise(self, team: str | int, gameweek: int | None = None) -> Advice:
        """Recommendations for the squad held after ``gameweek`` (default: current)."""
        team_id = api.parse_team_id(team)
        bootstrap = self.client.bootstrap()
        state = api.gameweek_state(bootstrap)
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

        history = self.client.entry_history(team_id)
        free = api.free_transfers(history, up_to_gameweek=picks_gw)
        gameweeks, preds = self.forecast(picks_gw + 1)
        snap_gw = int(self.snapshot().name.split("=")[1])
        if state.last_finished and snap_gw < state.last_finished:
            notes.append(
                f"GW{state.last_finished} has finished but is not ingested yet; "
                f"form data runs to GW{snap_gw}."
            )
        weights = horizon_weights(len(gameweeks))
        if len(gameweeks) > 1:
            later = ", ".join(f"{w:.2f}" for w in weights[1:])
            notes.append(
                f"Transfers are planned over GW{gameweeks[0]}-{gameweeks[-1]}: the squad after "
                f"this week's transfers is held for {len(gameweeks)} gameweeks, with a new XI "
                f"and captain each week. Later weeks are weighted {later} because predictions "
                "further ahead are less certain."
            )

        squad_ids = [p.player_id for p in picks.picks]
        columns = [points_column(gw) for gw in gameweeks]
        plans = optimize.recommend(
            preds, squad_ids, picks.bank, free, columns=columns, weights=weights
        )
        baseline = next((p for p in plans if p.n_transfers == 0), None)
        season_chips = bootstrap.get("chips", [])
        usable = chips.available(season_chips, history.get("chips", []), gameweeks[0])
        chip_advice = chips.advise(
            preds,
            squad_ids,
            picks.bank,
            plans[0],
            gameweeks,
            columns,
            weights,
            usable,
            next_windows=chips.unlocks(season_chips, gameweeks[0]),
        )
        view = PlayerView(preds, gameweeks)
        return Advice(
            team_id=team_id,
            team_name=entry["name"],
            manager=f"{entry['player_first_name']} {entry['player_last_name']}",
            picks_gameweek=picks_gw,
            gameweek=gameweeks[0],
            data_through_gameweek=snap_gw,
            bank=picks.bank,
            free_transfers=free,
            squad=view.many(squad_ids, sort=True),
            recommendations=[view.plan(rank, plan, baseline) for rank, plan in enumerate(plans, 1)],
            notes=notes,
            gameweeks=gameweeks,
            weights=[round(w, 3) for w in weights],
            chips=[asdict(c) for c in chip_advice],
        )


class PlayerView:
    """Turns player IDs and plans into plain dicts for JSON and tables."""

    def __init__(self, preds: pd.DataFrame, gameweeks: list[int] | None = None) -> None:
        self.df = preds.set_index("player_id")
        self.gameweeks = [
            gw for gw in gameweeks or [] if points_column(gw) in self.df
        ]  # empty when only one gameweek's predictions are known

    def one(self, player_id: int) -> dict[str, Any]:
        r = self.df.loc[player_id]
        row = {
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
            "form_points": round(float(r.get("form_points", 0.0)), 2),
            "form_minutes": round(float(r.get("form_minutes", 0.0)), 1),
            "opponents": _opponents(r.get("opponents")),
        }
        if self.gameweeks:
            row["horizon"] = [
                {
                    "gameweek": gw,
                    "points": round(float(r[points_column(gw)]), 2),
                    "opponents": _opponents(r[f"opp_gw{gw}"]),
                }
                for gw in self.gameweeks
            ]
            row["horizon_points"] = round(float(r["horizon_points"]), 2)
        return row

    def many(self, ids: list[int], sort: bool = False) -> list[dict[str, Any]]:
        rows = [self.one(p) for p in ids]
        return sorted(rows, key=lambda r: -r["predicted_points"]) if sort else rows

    def ranked(
        self, position: str | None = None, limit: int | None = None, by: str = "predicted_points"
    ) -> list[dict]:
        df = self.df if position is None else self.df[self.df["position"] == position]
        ids = df.sort_values(by, ascending=False).index[:limit]
        return self.many(list(ids))

    def plan(
        self, rank: int, plan: optimize.Plan, baseline: optimize.Plan | None = None
    ) -> dict[str, Any]:
        pairs = zip(plan.transfers_out, plan.transfers_in, strict=True)
        names = self.df["web_name"]
        gameweeks = self.gameweeks or [None] * len(plan.lineups)
        return {
            "rank": rank,
            "n_transfers": plan.n_transfers,
            "transfers_out": self.many(plan.transfers_out),
            "transfers_in": self.many(plan.transfers_in),
            "transfers": [self._transfer(o, i, plan, baseline) for o, i in pairs],
            "hit_cost": plan.hit_cost,
            "expected_points": round(plan.expected_points, 2),
            "net_points": round(plan.net_points, 2),
            "gain": round(plan.gain, 2),
            "first_gain": round(plan.first_gain, 2),
            "bank_after": round(plan.bank_after, 1),
            "reason": plan.reason,
            "captain": self.one(plan.captain),
            "vice_captain": self.one(plan.vice_captain),
            "starting": self.many(plan.starting),
            "bench": self.many(plan.bench),
            "lineups": [
                {
                    "gameweek": gw,
                    "points": round(lu.points, 2),
                    "captain": names[lu.captain],
                    "bench_points": round(float(sum(self._points(p, gw) for p in lu.bench)), 2),
                }
                for gw, lu in zip(gameweeks, plan.lineups, strict=True)
            ],
        }

    def _points(self, player_id: int, gameweek: int | None) -> float:
        col = "predicted_points" if gameweek is None else points_column(gameweek)
        return float(self.df.at[player_id, col])

    def _transfer(
        self, out_id: int, in_id: int, plan: optimize.Plan, baseline: optimize.Plan | None
    ) -> dict[str, Any]:
        """One swap with the reasons behind it, in plain sentences."""
        o, i = self.one(out_id), self.one(in_id)
        key = "horizon_points" if self.gameweeks else "predicted_points"
        diff = i[key] - o[key]
        span = (
            f"GW{self.gameweeks[0]}-{self.gameweeks[-1]}"
            if len(self.gameweeks) > 1
            else "the next gameweek"
        )
        why = [
            f"Over {span}, {i['name']} is expected to score {i[key]:.1f} points vs "
            f"{o['name']}'s {o[key]:.1f} ({diff:+.1f})."
        ]
        if self.gameweeks:
            weeks = "; ".join(
                f"GW{a['gameweek']} {_fixtures_text(b['opponents'])} {b['points']:.1f} vs "
                f"{_fixtures_text(a['opponents'])} {a['points']:.1f}"
                for a, b in zip(o["horizon"], i["horizon"], strict=True)
            )
            why.append(f"Fixtures and expected points, {i['name']} vs {o['name']}: {weeks}.")
        why.append(
            f"Last 3 gameweeks: {i['name']} averaged {i['form_points']:.1f} points and "
            f"{i['form_minutes']:.0f} minutes; {o['name']} {o['form_points']:.1f} points and "
            f"{o['form_minutes']:.0f} minutes."
        )
        if o["availability"] < 1:
            news = f": {o['news']}" if o["news"] else ""
            why.append(f"{o['name']} is flagged ({o['availability']:.0%} chance of playing){news}.")
        if baseline and out_id in baseline.bench and in_id in plan.starting:
            why.append(
                f"{o['name']} is on your bench in the no-transfer lineup; {i['name']} starts, "
                "so the swap adds points to the XI."
            )
        price = i["price"] - o["price"]
        if abs(price) >= 0.05:
            verb = "costs" if price > 0 else "frees"
            why.append(f"The swap {verb} £{abs(price):.1f}m.")
        return {"out": o, "in": i, "points_gain": round(diff, 2), "why": why}


def _opponents(value: Any) -> list[dict[str, Any]]:
    if not isinstance(value, list):
        return []
    return [
        {
            "opponent": str(f["opponent"]),
            "home": bool(f["home"]),
            "difficulty": None if pd.isna(f["difficulty"]) else int(f["difficulty"]),
        }
        for f in value
    ]


def _fixtures_text(opponents: list[dict[str, Any]]) -> str:
    if not opponents:
        return "blank"
    return " + ".join(f"{f['opponent']} ({'H' if f['home'] else 'A'})" for f in opponents)
