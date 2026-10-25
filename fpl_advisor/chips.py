"""Chip timing advice (wildcard, free hit, bench boost, triple captain) over the horizon.

Each chip gets an expected value per gameweek from the same predictions the optimizer uses.
The thresholds are rules of thumb, not fitted values: a chip is only worth playing now when
this week is its best week in the horizon and it clears the threshold.
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass, field
from typing import Any

import pandas as pd

from fpl_advisor import optimize

NAMES = {
    "wildcard": "Wildcard",
    "freehit": "Free Hit",
    "bboost": "Bench Boost",
    "3xc": "Triple Captain",
}
# Expected extra points needed before a chip is worth spending now.
THRESHOLDS = {"wildcard": 15.0, "freehit": 12.0, "bboost": 12.0, "3xc": 9.0}


@dataclass
class ChipAdvice:
    chip: str
    name: str
    verdict: str  # "play", "save" or "unavailable"
    available_until: int | None
    values: list[dict[str, Any]] = field(default_factory=list)  # [{gameweek, value}]
    threshold: float = 0.0
    detail: str = ""


def available(chips: list[dict], played: list[dict], gameweek: int) -> dict[str, int]:
    """Chips usable in ``gameweek``, mapped to the last gameweek of their window.

    ``chips`` is bootstrap-static ``chips`` (one entry per chip per half-season window);
    ``played`` is entry history ``chips`` ({name, event}).
    """
    out: dict[str, int] = {}
    for chip in chips:
        start, stop = chip["start_event"], chip["stop_event"]
        if not start <= gameweek <= stop:
            continue
        used = sum(1 for p in played if p["name"] == chip["name"] and start <= p["event"] <= stop)
        if used < chip.get("number", 1):
            out[chip["name"]] = stop
    return out


def unlocks(chips: list[dict], gameweek: int) -> dict[str, int]:
    """First gameweek of each chip's next window after ``gameweek``."""
    out: dict[str, int] = {}
    for chip in sorted(chips, key=lambda c: c["start_event"]):
        if chip["start_event"] > gameweek:
            out.setdefault(chip["name"], chip["start_event"])
    return out


def advise(
    players: pd.DataFrame,
    squad: list[int],
    bank: float,
    plan: optimize.Plan,
    gameweeks: Sequence[int],
    columns: Sequence[str],
    weights: Sequence[float],
    usable: dict[str, int],
    selling_prices: dict[int, float] | None = None,
    next_windows: dict[str, int] | None = None,
) -> list[ChipAdvice]:
    """Advice for every chip; ``plan`` is the recommended transfer plan."""
    df = players.set_index("player_id")
    names = df["web_name"].to_dict() if "web_name" in df else {}
    values: dict[str, list[float]] = {}
    notes: dict[str, list[str]] = {}

    # Triple captain: one more multiple of the captain's points.
    values["3xc"] = [df.at[lu.captain, col] for lu, col in zip(plan.lineups, columns, strict=True)]
    notes["3xc"] = [names.get(lu.captain, str(lu.captain)) for lu in plan.lineups]
    # Bench boost: the bench's points count too.
    values["bboost"] = [
        sum(df.at[p, col] for p in lu.bench) for lu, col in zip(plan.lineups, columns, strict=True)
    ]
    notes["bboost"] = [""] * len(columns)
    # Free hit: best one-week squad from the same budget, minus the planned lineup.
    values["freehit"], notes["freehit"] = [], [""] * len(columns)
    if "freehit" in usable:  # the extra solves are skipped for chips already used
        for lu, col in zip(plan.lineups, columns, strict=True):
            best = optimize.solve(players, squad, bank, 0, None, selling_prices, [col])
            values["freehit"].append(best.lineups[0].points - lu.points if best else 0.0)
    # Wildcard: unlimited free transfers now, held over the horizon, vs. the plan.
    values["wildcard"], notes["wildcard"] = [], [""]
    if "wildcard" in usable:
        wild = optimize.solve(players, squad, bank, 0, None, selling_prices, columns, weights)
        values["wildcard"] = [wild.net_points - plan.net_points if wild else 0.0]

    advice = []
    for chip, name in NAMES.items():
        weeks = [
            {"gameweek": gw, "value": round(float(v), 2)}
            for gw, v in zip(gameweeks, values[chip], strict=False)
        ]
        advice.append(_verdict(chip, name, weeks, notes[chip], usable.get(chip), gameweeks[0]))
    # Only one chip can be played per gameweek: keep the one furthest over its threshold.
    playing = [a for a in advice if a.verdict == "play"]
    if len(playing) > 1:
        keep = max(playing, key=lambda a: a.values[0]["value"] / a.threshold)
        for a in playing:
            if a is not keep:
                a.verdict = "save"
                a.detail += f" Only one chip per gameweek: {keep.name} is worth more now."
    return advice


def _verdict(
    chip: str, name: str, weeks: list[dict], notes: list[str], until: int | None, now: int
) -> ChipAdvice:
    threshold = THRESHOLDS[chip]
    if until is None:
        return ChipAdvice(chip, name, "unavailable", None, [], threshold, "Used.")
    in_window = [w for w in weeks if w["gameweek"] <= until]
    best = max(in_window, key=lambda w: w["value"])
    i = weeks.index(best)
    who = f" ({notes[i]})" if notes[i] else ""
    value = f"+{best['value']:.1f} expected points in GW{best['gameweek']}{who}"
    if chip == "wildcard":
        value = f"+{best['value']:.1f} expected points over the horizon vs. your best transfers"
    expires = f" Must be used by GW{until}." if until - now <= 5 else ""
    if best["gameweek"] == now and best["value"] >= threshold:
        detail = f"Play it now: {value}, above the {threshold:.0f}-point rule of thumb."
        return ChipAdvice(chip, name, "play", until, weeks, threshold, detail + expires)
    if best["value"] >= threshold:
        detail = f"Save it: the best week in the horizon is GW{best['gameweek']} ({value})."
    else:
        detail = (
            f"Save it: best value is {value}, below the {threshold:.0f}-point rule of thumb. "
            "Chips pay off most in double or blank gameweeks."
        )
    return ChipAdvice(chip, name, "save", until, weeks, threshold, detail + expires)
