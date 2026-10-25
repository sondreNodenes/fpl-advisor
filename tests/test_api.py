import json
from pathlib import Path

import pytest
import requests

from fpl_advisor import api

DATA = Path(__file__).parent / "data"


def load(name: str):
    return json.loads((DATA / name).read_text())


@pytest.mark.parametrize(
    "team, expected",
    [
        (5767275, 5767275),
        ("5767275", 5767275),
        (" 5767275 ", 5767275),
        ("https://fantasy.premierleague.com/en/entry/5767275/event/8", 5767275),
        ("fantasy.premierleague.com/entry/123/history", 123),
    ],
)
def test_parse_team_id(team, expected) -> None:
    assert api.parse_team_id(team) == expected


def test_parse_team_id_rejects_garbage() -> None:
    with pytest.raises(ValueError):
        api.parse_team_id("https://example.com/team/abc")


def test_parse_gameweek_from_url() -> None:
    assert api.parse_gameweek_from_url("https://x/en/entry/5767275/event/8") == 8
    assert api.parse_gameweek_from_url("5767275") is None


def test_gameweek_state_uses_last_checked_gameweek() -> None:
    state = api.gameweek_state(load("bootstrap-static.json"))
    assert (state.current, state.next, state.last_finished) == (8, 9, 7)
    assert state.season == "2026-27"


def test_parse_players_types() -> None:
    players = api.parse_players(load("bootstrap-static.json"))
    raya = players.set_index("player_id").loc[1]
    assert raya["price"] == pytest.approx(6.2)
    assert raya["position"] == "GK"
    assert players["ep_next"].dtype == float


def test_parse_fixtures_and_teams() -> None:
    fixtures = api.parse_fixtures(load("fixtures.json"))
    assert {"fixture", "event", "team_h", "team_a", "finished"} <= set(fixtures.columns)
    teams = api.parse_teams(load("bootstrap-static.json"))
    assert teams.loc[0, "short_name"] == "ARS"


def test_parse_history() -> None:
    history = api.parse_history(load("element-summary.json"))
    assert list(history.columns) == api.HISTORY_COLUMNS
    assert history.loc[0, "value"] == pytest.approx(6.0)
    assert history["expected_goals"].dtype == float


def test_parse_history_empty() -> None:
    assert api.parse_history({"history": []}).empty


def test_parse_picks() -> None:
    picks = api.parse_picks(load("event_8_picks.json"))
    assert len(picks.picks) == 15
    assert sum(p.is_captain for p in picks.picks) == 1
    assert picks.bank == pytest.approx(1.2)
    assert picks.squad_value == pytest.approx(102.1)


def test_free_transfers_rolls_over_and_respects_wildcard() -> None:
    # 1 after GW1, 2, 3, then 3 used in GW4 -> 1, 2, wildcard keeps 2, -1+1=2, -2+1=1.
    assert api.free_transfers(load("history.json"), up_to_gameweek=8) == 1
    assert api.free_transfers(load("history.json"), up_to_gameweek=3) == 3


class FakeResponse:
    def __init__(self, status: int, payload=None, text: bool = False) -> None:
        self.status_code = status
        self._payload = payload
        self._text = text

    def json(self):
        if self._text:
            raise ValueError("not json")
        return self._payload


class FakeSession:
    def __init__(self, responses) -> None:
        self.responses = list(responses)
        self.calls = 0

    def get(self, url, timeout):
        self.calls += 1
        item = self.responses.pop(0)
        if isinstance(item, Exception):
            raise item
        return item


def make_client(tmp_path, responses) -> tuple[api.FPLClient, FakeSession]:
    session = FakeSession(responses)
    client = api.FPLClient(cache_dir=tmp_path, min_interval=0, session=session)
    return client, session


def test_client_caches_responses(tmp_path) -> None:
    client, session = make_client(tmp_path, [FakeResponse(200, {"a": 1})])
    assert client.get("bootstrap-static/") == {"a": 1}
    assert client.get("bootstrap-static/") == {"a": 1}
    assert session.calls == 1


def test_client_not_found(tmp_path) -> None:
    client, _ = make_client(tmp_path, [FakeResponse(404)])
    with pytest.raises(api.NotFoundError):
        client.entry(999999999)


@pytest.mark.parametrize(
    "response",
    [FakeResponse(503), FakeResponse(200, text=True), requests.ConnectionError("down")],
)
def test_client_unavailable(tmp_path, response) -> None:
    client, _ = make_client(tmp_path, [response])
    with pytest.raises(api.FPLAPIError):
        client.bootstrap()
