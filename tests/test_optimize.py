from collections import Counter

import numpy as np
import pandas as pd
import pytest

from fpl_advisor import optimize as opt


def random_market(seed: int, n_teams: int = 20, per_team: int = 6) -> pd.DataFrame:
    rng = np.random.default_rng(seed)
    rows = []
    pid = 1
    for team in range(1, n_teams + 1):
        for element_type in (1, 2, 2, 3, 3, 4)[:per_team]:
            rows.append(
                {
                    "player_id": pid,
                    "team": team,
                    "element_type": element_type,
                    "price": round(float(rng.uniform(4.0, 12.0)), 1),
                    "predicted_points": float(rng.gamma(2.0, 1.5)),
                }
            )
            pid += 1
    return pd.DataFrame(rows)


def random_squad(market: pd.DataFrame, seed: int) -> list[int]:
    """A valid 15-man squad (2/5/5/3, max 3 per club) drawn at random."""
    rng = np.random.default_rng(seed)
    squad: list[int] = []
    clubs: Counter = Counter()
    for element_type, count in opt.SQUAD_POSITIONS.items():
        pool = market[market["element_type"] == element_type].sample(frac=1, random_state=rng)
        for _, row in pool.iterrows():
            if (
                sum(market.set_index("player_id").loc[squad, "element_type"] == element_type)
                >= count
            ):
                break
            if clubs[row["team"]] < opt.MAX_PER_CLUB:
                squad.append(int(row["player_id"]))
                clubs[row["team"]] += 1
    assert len(squad) == 15
    return squad


def check_plan(plan: opt.Plan, market: pd.DataFrame, squad: list[int], bank: float) -> None:
    df = market.set_index("player_id")
    final = [p for p in squad if p not in plan.transfers_out] + plan.transfers_in
    assert sorted(final) == sorted(plan.starting + plan.bench)
    assert len(set(final)) == 15
    assert Counter(df.loc[final, "element_type"]) == Counter(opt.SQUAD_POSITIONS)
    assert max(Counter(df.loc[final, "team"]).values()) <= opt.MAX_PER_CLUB

    xi = Counter(df.loc[plan.starting, "element_type"])
    assert len(plan.starting) == 11
    for t in opt.XI_MIN:
        assert opt.XI_MIN[t] <= xi[t] <= opt.XI_MAX[t]
    assert plan.captain in plan.starting
    assert plan.vice_captain in plan.starting and plan.vice_captain != plan.captain

    spent = df.loc[plan.transfers_in, "price"].sum()
    earned = df.loc[plan.transfers_out, "price"].sum()
    assert spent <= bank + earned + 1e-6
    assert len(plan.transfers_in) == len(plan.transfers_out) <= 2
    for out, into in zip(plan.transfers_out, plan.transfers_in, strict=True):
        assert df.at[out, "element_type"] == df.at[into, "element_type"]

    pts = df["predicted_points"]
    expected = pts[plan.starting].sum() + pts[plan.captain]
    assert plan.expected_points == pytest.approx(expected)
    assert pts[plan.captain] == pytest.approx(pts[plan.starting].max())


@pytest.mark.parametrize("seed", range(5))
def test_constraints_hold_on_random_inputs(seed: int) -> None:
    market = random_market(seed)
    squad = random_squad(market, seed)
    bank = float(np.random.default_rng(seed).uniform(0, 3))
    plans = opt.recommend(market, squad, bank, free_transfers=1)
    assert {p.n_transfers for p in plans} == {0, 1, 2}
    for plan in plans:
        check_plan(plan, market, squad, bank)
    assert plans[0].net_points >= max(p.net_points for p in plans) - 1e-9
    two = next(p for p in plans if p.n_transfers == 2)
    assert two.hit_cost == opt.HIT_COST  # 1 free transfer, 2 made


def test_zero_transfers_when_no_swap_gains() -> None:
    market = random_market(0)
    squad = random_squad(market, 0)
    market.loc[~market["player_id"].isin(squad), "predicted_points"] = 0.0
    best = opt.recommend(market, squad, bank=5.0, free_transfers=1)[0]
    assert best.n_transfers == 0
    assert best.gain == 0
    assert "Keep the squad" in best.reason


def _single_upgrade(gain: float) -> tuple[pd.DataFrame, list[int], int]:
    """A market where exactly one transfer improves the XI by ``gain`` points.

    Every squad player scores 5 except a fixed 20-point captain, so a better midfielder
    replaces a 5-point starter (+gain) without changing the captain.
    """
    market = random_market(1)
    squad = random_squad(market, 1)
    df = market.set_index("player_id")
    market["predicted_points"] = np.where(market["player_id"].isin(squad), 5.0, 0.0)
    market.loc[market["player_id"] == squad[-1], "predicted_points"] = 20.0  # a forward
    mids = [p for p in squad if df.at[p, "element_type"] == 3]
    clubs = Counter(df.loc[squad, "team"])
    target = next(
        p
        for p in df.index
        if p not in squad
        and df.at[p, "element_type"] == 3
        and df.at[p, "price"] <= df.loc[mids, "price"].max()
        and clubs[df.at[p, "team"]] < opt.MAX_PER_CLUB
    )
    market.loc[market["player_id"] == target, "predicted_points"] = 5.0 + gain
    return market, squad, target


def test_hit_only_taken_when_worth_it() -> None:
    market, squad, target = _single_upgrade(gain=3.0)
    assert opt.recommend(market, squad, bank=0.0, free_transfers=0)[0].n_transfers == 0

    market, squad, target = _single_upgrade(gain=5.0)
    best = opt.recommend(market, squad, bank=0.0, free_transfers=0)[0]
    assert best.transfers_in == [target]
    assert best.gain == pytest.approx(1.0)
    assert "includes -4 hit" in best.reason


def test_free_transfer_used_without_hit() -> None:
    market, squad, target = _single_upgrade(gain=1.0)
    best = opt.recommend(market, squad, bank=0.0, free_transfers=1)[0]
    assert best.transfers_in == [target]
    assert best.hit_cost == 0
    assert best.reason == "+1.0 expected points over the next gameweek, no hit"


def test_horizon_buys_for_later_weeks() -> None:
    """A player who blanks next week but scores well after is bought only with a horizon."""
    market, squad, target = _single_upgrade(gain=3.0)
    market["w1"] = market["predicted_points"]
    market.loc[market["player_id"] == target, "w1"] = 0.0  # blank next gameweek
    market["w2"] = market["predicted_points"]
    market["w3"] = market["predicted_points"]

    one_week = opt.recommend(market, squad, 0.0, 1, columns=["w1"])[0]
    assert one_week.n_transfers == 0

    plans = opt.recommend(market, squad, 0.0, 1, columns=["w1", "w2", "w3"], weights=[1, 1, 1])
    best = plans[0]
    assert best.transfers_in == [target]
    assert target in best.bench  # benched in the blank week
    assert all(target in lu.starting for lu in best.lineups[1:])
    assert best.gain == pytest.approx(6.0) and best.first_gain == pytest.approx(0.0)
    assert "over the next 3 gameweeks" in best.reason
    for lu in best.lineups:
        assert len(lu.starting) == 11 and lu.captain in lu.starting


def test_candidates_drops_only_dominated_players(monkeypatch) -> None:
    market = random_market(0)
    squad = random_squad(market, 0)
    kept = opt.candidates(market, squad, ["predicted_points"])
    assert set(squad) <= set(kept["player_id"])
    assert len(kept) < len(market)
    # Pruning must not change the optimum.
    pruned = opt.solve(market, squad, 2.0, 2, 2).net_points
    monkeypatch.setattr(opt, "candidates", lambda players, squad, columns: players)
    assert opt.solve(market, squad, 2.0, 2, 2).net_points == pytest.approx(pruned)


def test_candidates_keeps_one_of_identical_players() -> None:
    market = random_market(0)
    gk = market[market["element_type"] == 1].copy()
    clones = pd.concat([gk.iloc[[0]]] * 12, ignore_index=True)
    clones["player_id"] = range(1000, 1012)
    clones[["price", "predicted_points"]] = [3.0, 99.0]  # best and cheapest: dominate others
    market = pd.concat([market, clones], ignore_index=True)
    kept = opt.candidates(market, [], ["predicted_points"])
    n_kept = kept["player_id"].isin(clones["player_id"]).sum()
    assert n_kept == opt.SQUAD_POSITIONS[1] + opt.DOMINANCE_MARGIN
