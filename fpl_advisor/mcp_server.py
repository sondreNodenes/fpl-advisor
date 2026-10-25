"""MCP server exposing the advisor as tools. Run: ``python -m fpl_advisor.mcp_server`` (stdio)."""

from __future__ import annotations

import unicodedata
from typing import Any

from mcp.server.mcpserver import MCPServer

from fpl_advisor import __version__
from fpl_advisor.advisor import Advice, Advisor, PlayerView

server = MCPServer(
    name="fpl-advisor",
    version=__version__,
    instructions=(
        "Fantasy Premier League advice. `team` accepts an FPL team ID or any URL containing "
        "/entry/{id}. Points are model predictions for the next gameweek, already scaled by "
        "the player's chance of playing."
    ),
)
advisor = Advisor()


def _fold(text: str) -> str:
    """Lowercase and strip accents so 'odegaard' matches 'Ødegaard'."""
    text = text.replace("Ø", "O").replace("ø", "o")
    return "".join(c for c in unicodedata.normalize("NFKD", text) if not unicodedata.combining(c))


def _header(a: Advice) -> dict[str, Any]:
    return {
        "team_id": a.team_id,
        "team_name": a.team_name,
        "manager": a.manager,
        "gameweek": a.gameweek,
        "gameweeks": a.gameweeks,
        "bank": a.bank,
        "free_transfers": a.free_transfers,
        "notes": a.notes,
    }


@server.tool()
def get_team(team: str) -> dict[str, Any]:
    """Squad, bank and free transfers for an FPL team, with predicted points per player."""
    advice = advisor.advise(team)
    return _header(advice) | {"squad": advice.squad}


@server.tool()
def predict_player(name: str, limit: int = 5) -> dict[str, Any]:
    """Predicted points (next gameweek and the planning horizon) for players matching ``name``."""
    gameweeks, preds = advisor.forecast()
    query = _fold(name).lower().strip()
    matches = preds[preds["web_name"].map(lambda n: query in _fold(n).lower())]
    ranked = matches.sort_values("predicted_points", ascending=False)["player_id"]
    players = PlayerView(preds, gameweeks).many(list(ranked[:limit]))
    return {"gameweek": gameweeks[0], "gameweeks": gameweeks, "query": name, "players": players}


@server.tool()
def recommend_transfers(team: str) -> dict[str, Any]:
    """Ranked transfer plans (0-2 transfers, net of 4-point hits, planned over the next
    gameweeks) with the reasons for each swap, plus chip timing advice."""
    advice = advisor.advise(team)
    plans = [
        {k: plan[k] for k in ("rank", "hit_cost", "gain", "first_gain", "reason", "lineups")}
        | {
            "transfers": [
                {"out": t["out"]["name"], "in": t["in"]["name"], "why": t["why"]}
                for t in plan["transfers"]
            ],
            "captain": plan["captain"]["name"],
            "vice_captain": plan["vice_captain"]["name"],
        }
        for plan in advice.recommendations
    ]
    return _header(advice) | {"recommendations": plans, "chips": advice.chips}


@server.tool()
def captain_options(team: str, limit: int = 5) -> dict[str, Any]:
    """Best captain choices among the recommended starting XI, by predicted points."""
    advice = advisor.advise(team)
    best = advice.recommendations[0]
    options = sorted(best["starting"], key=lambda p: -p["predicted_points"])[:limit]
    return _header(advice) | {
        "captain": best["captain"]["name"],
        "vice_captain": best["vice_captain"]["name"],
        "options": options,
    }


if __name__ == "__main__":
    server.run()
