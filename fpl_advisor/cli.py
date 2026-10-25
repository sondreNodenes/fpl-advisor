"""Command line: ``python -m fpl_advisor.cli "<team url or id>" [--gameweek N] [--json]``."""

from __future__ import annotations

import argparse
import json
import sys
from dataclasses import asdict
from typing import Any

from fpl_advisor import api
from fpl_advisor.advisor import Advice, Advisor


def _players(rows: list[dict[str, Any]], captain: int | None = None, vice: int | None = None):
    lines = [f"  {'Player':<18}{'Club':<6}{'Pos':<5}{'Price':>7}{'Pred':>7}{'Fix':>5}{'Avail':>7}"]
    for p in rows:
        tag = " (C)" if p["id"] == captain else " (V)" if p["id"] == vice else ""
        avail = f"{p['availability']:.0%}" if p["availability"] < 1 else ""
        lines.append(
            f"  {p['name'] + tag:<18}{p['team']:<6}{p['position']:<5}"
            f"{p['price']:>7.1f}{p['predicted_points']:>7.2f}{p['fixtures']:>5}{avail:>7}"
        )
    return "\n".join(lines)


def format_advice(a: Advice) -> str:
    best = a.recommendations[0]
    out = [
        f"{a.team_name} ({a.manager}), squad after GW{a.picks_gameweek}, predicting GW{a.gameweek}",
        f"Bank £{a.bank:.1f}m, free transfers {a.free_transfers}, "
        f"form data through GW{a.data_through_gameweek}",
        "",
        "Recommendations:",
    ]
    for r in a.recommendations:
        moves = ", ".join(f"{t['out']['name']} -> {t['in']['name']}" for t in r["transfers"])
        out.append(f"  {r['rank']}. {moves or 'No transfers'}: {r['reason']}")
        if r["rank"] == 1:
            for t in r["transfers"]:
                out.append(f"     Why {t['out']['name']} -> {t['in']['name']}:")
                out += [f"       - {line}" for line in t["why"]]
    if a.chips:
        out += ["", "Chips:"]
        out += [f"  {c['name']:<15}{c['verdict']:<12}{c['detail']}" for c in a.chips]
    out += [
        "",
        f"Captain {best['captain']['name']}, vice {best['vice_captain']['name']}",
        "",
        "Starting XI (option 1):",
        _players(best["starting"], best["captain"]["id"], best["vice_captain"]["id"]),
        "Bench:",
        _players(best["bench"]),
        "",
        *[f"Note: {n}" for n in a.notes],
    ]
    return "\n".join(out)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="FPL transfer, captain and XI advice")
    parser.add_argument("team", help="FPL team ID or URL containing /entry/{id}")
    parser.add_argument("--gameweek", type=int, help="squad gameweek (default: current)")
    parser.add_argument("--json", action="store_true", help="print JSON instead of tables")
    args = parser.parse_args(argv)
    try:
        advice = Advisor().advise(args.team, args.gameweek)
    except (ValueError, api.FPLAPIError, FileNotFoundError) as exc:
        print(f"Error: {exc}", file=sys.stderr)
        return 1
    print(json.dumps(asdict(advice), indent=2) if args.json else format_advice(advice))
    return 0


if __name__ == "__main__":
    sys.exit(main())
