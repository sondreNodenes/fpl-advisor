"""Transfer, starting XI and captain optimisation (Pyomo MILP, HiGHS with GLPK fallback)."""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass

import numpy as np
import pandas as pd
import pyomo.environ as pyo

SQUAD_SIZE = 15
SQUAD_POSITIONS = {1: 2, 2: 5, 3: 5, 4: 3}  # element_type -> count
XI_MIN = {1: 1, 2: 3, 3: 2, 4: 1}
XI_MAX = {1: 1, 2: 5, 3: 5, 4: 3}
MAX_PER_CLUB = 3
HIT_COST = 4
SOLVERS = ("appsi_highs", "highs", "glpk")
# Keep a player unless this many more same-position players are at least as cheap and at
# least as good every week; the margin covers dominators blocked by the club limit.
DOMINANCE_MARGIN = 5


class OptimizationError(RuntimeError):
    pass


@dataclass
class Lineup:
    """Starting XI, bench and captain for one gameweek of the horizon."""

    starting: list[int]
    bench: list[int]
    captain: int
    vice_captain: int
    points: float  # XI + captain bonus, unweighted


@dataclass
class Plan:
    transfers_out: list[int]
    transfers_in: list[int]
    lineups: list[Lineup]  # one per horizon gameweek; the first is the next gameweek
    expected_points: float  # weighted sum of lineup points over the horizon, before hits
    hit_cost: int
    bank_after: float = 0.0
    gain: float = 0.0  # net vs. making no transfers, over the horizon
    first_gain: float = 0.0  # net vs. making no transfers, next gameweek only
    reason: str = ""

    @property
    def net_points(self) -> float:
        return self.expected_points - self.hit_cost

    @property
    def n_transfers(self) -> int:
        return len(self.transfers_in)

    @property
    def starting(self) -> list[int]:
        return self.lineups[0].starting

    @property
    def bench(self) -> list[int]:
        return self.lineups[0].bench

    @property
    def captain(self) -> int:
        return self.lineups[0].captain

    @property
    def vice_captain(self) -> int:
        return self.lineups[0].vice_captain


def candidates(players: pd.DataFrame, squad: list[int], columns: Sequence[str]) -> pd.DataFrame:
    """Drop players that can never be worth buying, which makes each solve several times faster.

    Player p is dominated by q (same position) when q costs no more and scores at least as
    much in every week, with ties broken by row order so that one of two equal players stays.
    """
    keep = players["player_id"].isin(squad).to_numpy().copy()
    for element_type, count in SQUAD_POSITIONS.items():
        idx = np.flatnonzero((players["element_type"] == element_type).to_numpy())
        pts = players[list(columns)].to_numpy()[idx]
        price = players["price"].to_numpy()[idx]
        at_least = (pts[None, :, :] >= pts[:, None, :]).all(axis=2)  # [p, q]: q >= p
        better = (pts[None, :, :] > pts[:, None, :]).any(axis=2)
        cheaper = price[None, :] <= price[:, None]
        strictly = better | (price[None, :] < price[:, None])
        earlier = np.arange(len(idx))[None, :] < np.arange(len(idx))[:, None]
        dominates = at_least & cheaper & (strictly | earlier)
        np.fill_diagonal(dominates, False)
        keep[idx] |= dominates.sum(axis=1) < count + DOMINANCE_MARGIN
    return players[keep]


def _solver():
    for name in SOLVERS:
        solver = pyo.SolverFactory(name)
        if solver.available(exception_flag=False):
            return solver
    raise OptimizationError("No MILP solver found (install highspy or glpk)")


def solve(
    players: pd.DataFrame,
    squad: list[int],
    bank: float,
    free_transfers: int,
    n_transfers: int | None,
    selling_prices: dict[int, float] | None = None,
    columns: Sequence[str] = ("predicted_points",),
    weights: Sequence[float] | None = None,
) -> Plan | None:
    """Best plan with exactly ``n_transfers`` transfers made now, or None if infeasible.

    The squad is then held for every gameweek in ``columns`` (one points column per
    gameweek), with its own XI and captain each week; ``weights`` discount later weeks.
    ``n_transfers=None`` allows any number of free transfers (wildcard or free hit).
    ``players`` needs: player_id, element_type, team, price and the ``columns``.
    ``selling_prices`` default to current prices for squad players.
    """
    if missing := set(squad) - set(players["player_id"]):
        raise OptimizationError(f"Squad players missing from player table: {sorted(missing)}")
    df = candidates(players, squad, columns).set_index("player_id")
    weights = list(weights or [1.0] * len(columns))
    sell = {p: df.at[p, "price"] for p in squad} | (selling_prices or {})
    in_squad = set(squad)
    ids = list(df.index)
    pts = [df[c].to_dict() for c in columns]
    weeks = range(len(columns))

    m = pyo.ConcreteModel()
    m.P = pyo.Set(initialize=ids)
    m.G = pyo.Set(initialize=list(weeks))
    m.x = pyo.Var(m.P, domain=pyo.Binary)  # in final squad
    m.s = pyo.Var(m.P, m.G, domain=pyo.Binary)  # starting in week g
    m.c = pyo.Var(m.P, m.G, domain=pyo.Binary)  # captain in week g
    m.extra = pyo.Var(domain=pyo.NonNegativeIntegers)  # paid transfers

    by_pos = {t: [p for p in ids if df.at[p, "element_type"] == t] for t in SQUAD_POSITIONS}
    by_club = df.groupby("team").groups

    m.size = pyo.Constraint(expr=sum(m.x[p] for p in ids) == SQUAD_SIZE)
    m.pos = pyo.Constraint(
        list(SQUAD_POSITIONS),
        rule=lambda m, t: sum(m.x[p] for p in by_pos[t]) == SQUAD_POSITIONS[t],
    )
    m.club = pyo.Constraint(
        list(by_club), rule=lambda m, c: sum(m.x[p] for p in by_club[c]) <= MAX_PER_CLUB
    )
    m.start_in_squad = pyo.Constraint(m.P, m.G, rule=lambda m, p, g: m.s[p, g] <= m.x[p])
    m.xi = pyo.Constraint(m.G, rule=lambda m, g: sum(m.s[p, g] for p in ids) == 11)
    pos_weeks = [(t, g) for t in XI_MIN for g in weeks]
    m.xi_min = pyo.Constraint(
        pos_weeks, rule=lambda m, t, g: sum(m.s[p, g] for p in by_pos[t]) >= XI_MIN[t]
    )
    m.xi_max = pyo.Constraint(
        pos_weeks, rule=lambda m, t, g: sum(m.s[p, g] for p in by_pos[t]) <= XI_MAX[t]
    )
    m.cap_starts = pyo.Constraint(m.P, m.G, rule=lambda m, p, g: m.c[p, g] <= m.s[p, g])
    m.one_cap = pyo.Constraint(m.G, rule=lambda m, g: sum(m.c[p, g] for p in ids) == 1)

    bought = sum(m.x[p] for p in ids if p not in in_squad)
    if n_transfers is None:
        m.hits = pyo.Constraint(expr=m.extra == 0)
    else:
        m.transfers = pyo.Constraint(expr=bought == n_transfers)
        m.hits = pyo.Constraint(expr=m.extra >= n_transfers - free_transfers)
    spend = sum(df.at[p, "price"] * m.x[p] for p in ids if p not in in_squad)
    income = sum(sell[p] * (1 - m.x[p]) for p in squad)
    m.budget = pyo.Constraint(expr=spend <= bank + income + 1e-6)

    m.obj = pyo.Objective(
        expr=sum(weights[g] * pts[g][p] * (m.s[p, g] + m.c[p, g]) for p in ids for g in weeks)
        - HIT_COST * m.extra,
        sense=pyo.maximize,
    )
    result = _solver().solve(m, load_solutions=False)
    if result.solver.termination_condition != pyo.TerminationCondition.optimal:
        return None
    m.solutions.load_from(result)

    chosen = [p for p in ids if pyo.value(m.x[p]) > 0.5]
    lineups = [_lineup(m, g, chosen, pts[g], df) for g in weeks]
    hits = HIT_COST * round(pyo.value(m.extra))
    out = [p for p in squad if p not in chosen]
    into = [p for p in chosen if p not in in_squad]
    # Same position counts go out and in, so sorting both by position pairs the swaps.
    by_position = lambda p: (df.at[p, "element_type"], -pts[0][p])  # noqa: E731
    return Plan(
        transfers_out=sorted(out, key=by_position),
        transfers_in=sorted(into, key=by_position),
        lineups=lineups,
        expected_points=sum(w * lu.points for w, lu in zip(weights, lineups, strict=True)),
        hit_cost=hits,
        bank_after=bank + sum(sell[p] for p in out) - sum(df.at[p, "price"] for p in into),
    )


def _lineup(m, g: int, chosen: list[int], pts: dict[int, float], df: pd.DataFrame) -> Lineup:
    starting = sorted((p for p in chosen if pyo.value(m.s[p, g]) > 0.5), key=lambda p: -pts[p])
    captain = next(p for p in chosen if pyo.value(m.c[p, g]) > 0.5)
    vice = next(p for p in starting if p != captain)
    bench = [p for p in chosen if p not in starting]
    # FPL bench order: outfield players by expected points, then the backup goalkeeper.
    bench.sort(key=lambda p: (df.at[p, "element_type"] == 1, -pts[p]))
    points = sum(pts[p] for p in starting) + pts[captain]
    return Lineup(starting, bench, captain, vice, points)


def recommend(
    players: pd.DataFrame,
    squad: list[int],
    bank: float,
    free_transfers: int,
    max_transfers: int = 2,
    selling_prices: dict[int, float] | None = None,
    columns: Sequence[str] = ("predicted_points",),
    weights: Sequence[float] | None = None,
) -> list[Plan]:
    """Best plan for 0..max_transfers transfers, ranked by net expected points.

    Ties go to fewer transfers, so 0 transfers is first unless a move strictly gains.
    """
    plans = [
        plan
        for k in range(max_transfers + 1)
        if (
            plan := solve(players, squad, bank, free_transfers, k, selling_prices, columns, weights)
        )
        is not None
    ]
    if not plans:
        raise OptimizationError("No feasible squad (check squad, prices and bank)")
    baseline = next((p for p in plans if p.n_transfers == 0), None)
    for plan in plans:
        plan.gain = plan.net_points - (baseline.net_points if baseline else 0.0)
        first_base = baseline.lineups[0].points if baseline else 0.0
        plan.first_gain = plan.lineups[0].points - plan.hit_cost - first_base
    ranked = sorted(plans, key=lambda p: (-round(p.net_points, 6), p.n_transfers))
    for plan in ranked:
        plan.reason = _reason(plan, len(columns), free_transfers, best=plan is ranked[0])
    return ranked


def _reason(plan: Plan, n_weeks: int, free_transfers: int, best: bool) -> str:
    span = "next gameweek" if n_weeks == 1 else f"next {n_weeks} gameweeks"
    if plan.n_transfers == 0:
        if not best:
            return "Keep the squad (reference for the gains above)"
        roll = (
            f"; rolling the free transfer gives {min(5, free_transfers + 1)} next week"
            if 0 < free_transfers < 5
            else ""
        )
        return f"Keep the squad: no transfer gains more than it costs over the {span}{roll}"
    hit = f", includes -{plan.hit_cost} hit" if plan.hit_cost else ", no hit"
    first = f" ({plan.first_gain:+.1f} this gameweek)" if n_weeks > 1 else ""
    return f"{plan.gain:+.1f} expected points over the {span}{first}{hit}"
