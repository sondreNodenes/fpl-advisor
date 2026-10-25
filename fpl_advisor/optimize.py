"""Transfer, starting XI and captain optimisation (Pyomo MILP, HiGHS with GLPK fallback)."""

from __future__ import annotations

from dataclasses import dataclass

import pandas as pd
import pyomo.environ as pyo

SQUAD_SIZE = 15
SQUAD_POSITIONS = {1: 2, 2: 5, 3: 5, 4: 3}  # element_type -> count
XI_MIN = {1: 1, 2: 3, 3: 2, 4: 1}
XI_MAX = {1: 1, 2: 5, 3: 5, 4: 3}
MAX_PER_CLUB = 3
HIT_COST = 4
SOLVERS = ("appsi_highs", "highs", "glpk")


class OptimizationError(RuntimeError):
    pass


@dataclass
class Plan:
    transfers_out: list[int]
    transfers_in: list[int]
    starting: list[int]
    bench: list[int]
    captain: int
    vice_captain: int
    expected_points: float  # XI + captain bonus, before hits
    hit_cost: int
    gain: float = 0.0  # net vs. making no transfers
    reason: str = ""

    @property
    def net_points(self) -> float:
        return self.expected_points - self.hit_cost

    @property
    def n_transfers(self) -> int:
        return len(self.transfers_in)


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
    n_transfers: int,
    selling_prices: dict[int, float] | None = None,
) -> Plan | None:
    """Best plan with exactly ``n_transfers`` transfers, or None if infeasible.

    ``players`` needs: player_id, element_type, team, price, predicted_points.
    ``selling_prices`` default to current prices for squad players.
    """
    df = players.set_index("player_id")
    if missing := set(squad) - set(df.index):
        raise OptimizationError(f"Squad players missing from player table: {sorted(missing)}")
    sell = {p: df.at[p, "price"] for p in squad} | (selling_prices or {})
    in_squad = set(squad)
    ids = list(df.index)
    pts = df["predicted_points"].to_dict()

    m = pyo.ConcreteModel()
    m.P = pyo.Set(initialize=ids)
    m.x = pyo.Var(m.P, domain=pyo.Binary)  # in final squad
    m.s = pyo.Var(m.P, domain=pyo.Binary)  # starting
    m.c = pyo.Var(m.P, domain=pyo.Binary)  # captain
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
    m.start_in_squad = pyo.Constraint(m.P, rule=lambda m, p: m.s[p] <= m.x[p])
    m.xi = pyo.Constraint(expr=sum(m.s[p] for p in ids) == 11)
    m.xi_min = pyo.Constraint(
        list(XI_MIN), rule=lambda m, t: sum(m.s[p] for p in by_pos[t]) >= XI_MIN[t]
    )
    m.xi_max = pyo.Constraint(
        list(XI_MAX), rule=lambda m, t: sum(m.s[p] for p in by_pos[t]) <= XI_MAX[t]
    )
    m.cap_starts = pyo.Constraint(m.P, rule=lambda m, p: m.c[p] <= m.s[p])
    m.one_cap = pyo.Constraint(expr=sum(m.c[p] for p in ids) == 1)

    bought = sum(m.x[p] for p in ids if p not in in_squad)
    m.transfers = pyo.Constraint(expr=bought == n_transfers)
    m.hits = pyo.Constraint(expr=m.extra >= n_transfers - free_transfers)
    spend = sum(df.at[p, "price"] * m.x[p] for p in ids if p not in in_squad)
    income = sum(sell[p] * (1 - m.x[p]) for p in squad)
    m.budget = pyo.Constraint(expr=spend <= bank + income + 1e-6)

    m.obj = pyo.Objective(
        expr=sum(pts[p] * (m.s[p] + m.c[p]) for p in ids) - HIT_COST * m.extra,
        sense=pyo.maximize,
    )
    result = _solver().solve(m, load_solutions=False)
    if result.solver.termination_condition != pyo.TerminationCondition.optimal:
        return None
    m.solutions.load_from(result)

    chosen = [p for p in ids if pyo.value(m.x[p]) > 0.5]
    starting = sorted((p for p in chosen if pyo.value(m.s[p]) > 0.5), key=lambda p: -pts[p])
    captain = next(p for p in ids if pyo.value(m.c[p]) > 0.5)
    vice = next(p for p in starting if p != captain)
    bench = [p for p in chosen if p not in starting]
    # FPL bench order: outfield players by expected points, then the backup goalkeeper.
    bench.sort(key=lambda p: (df.at[p, "element_type"] == 1, -pts[p]))
    hits = HIT_COST * round(pyo.value(m.extra))
    return Plan(
        transfers_out=sorted(p for p in squad if p not in chosen),
        transfers_in=sorted(p for p in chosen if p not in in_squad),
        starting=starting,
        bench=bench,
        captain=captain,
        vice_captain=vice,
        expected_points=sum(pts[p] for p in starting) + pts[captain],
        hit_cost=hits,
    )


def recommend(
    players: pd.DataFrame,
    squad: list[int],
    bank: float,
    free_transfers: int,
    max_transfers: int = 2,
    selling_prices: dict[int, float] | None = None,
) -> list[Plan]:
    """Best plan for 0..max_transfers transfers, ranked by net expected points.

    Ties go to fewer transfers, so 0 transfers is first unless a move strictly gains.
    """
    plans = [
        plan
        for k in range(max_transfers + 1)
        if (plan := solve(players, squad, bank, free_transfers, k, selling_prices)) is not None
    ]
    if not plans:
        raise OptimizationError("No feasible squad (check squad, prices and bank)")
    baseline = next((p for p in plans if p.n_transfers == 0), None)
    base = baseline.net_points if baseline else 0.0
    for plan in plans:
        plan.gain = plan.net_points - base
    ranked = sorted(plans, key=lambda p: (-round(p.net_points, 6), p.n_transfers))
    for plan in ranked:
        plan.reason = _reason(plan, best=plan is ranked[0])
    return ranked


def _reason(plan: Plan, best: bool) -> str:
    if plan.n_transfers == 0:
        if best:
            return "Keep the squad: no transfer gains more than it costs this gameweek"
        return "Keep the squad (reference for the gains above)"
    hit = f", includes -{plan.hit_cost} hit" if plan.hit_cost else ", no hit"
    return f"{plan.gain:+.1f} expected points over next gameweek{hit}"
