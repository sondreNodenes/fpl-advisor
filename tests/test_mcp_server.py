import asyncio

import pytest

from fpl_advisor import mcp_server


@pytest.fixture(autouse=True)
def fake(fake_advisor, monkeypatch):
    monkeypatch.setattr(mcp_server, "advisor", fake_advisor)
    return fake_advisor


def test_tools_registered() -> None:
    names = {t.name for t in asyncio.run(mcp_server.server.list_tools())}
    assert names == {"get_team", "predict_player", "recommend_transfers", "captain_options"}


def test_get_team() -> None:
    out = mcp_server.get_team("5767275")
    assert out["team_name"] == "Test FC"
    assert len(out["squad"]) == 15
    assert {"name", "predicted_points", "position"} <= set(out["squad"][0])


def test_predict_player_matches_case_and_accents() -> None:
    out = mcp_server.predict_player("PLAYER1", limit=3)
    assert out["gameweek"] == 9
    assert 0 < len(out["players"]) <= 3
    assert all("1" in p["name"] for p in out["players"])
    assert mcp_server._fold("Ødegaard").lower() == "odegaard"


def test_recommend_transfers() -> None:
    out = mcp_server.recommend_transfers("5767275")
    plans = out["recommendations"]
    assert [p["rank"] for p in plans] == list(range(1, len(plans) + 1))
    assert all(isinstance(p["captain"], str) and p["reason"] for p in plans)


def test_captain_options() -> None:
    out = mcp_server.captain_options("5767275", limit=3)
    pts = [p["predicted_points"] for p in out["options"]]
    assert len(pts) == 3 and pts == sorted(pts, reverse=True)
    assert out["captain"] == out["options"][0]["name"]


def test_call_tool_through_server() -> None:
    result = asyncio.run(mcp_server.server.call_tool("predict_player", {"name": "player2"}))
    assert not result.is_error
