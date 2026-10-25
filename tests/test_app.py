import pytest
from fastapi.testclient import TestClient

from fpl_advisor import api
from fpl_advisor import app as app_module


@pytest.fixture
def client(fake_advisor, monkeypatch) -> TestClient:
    monkeypatch.setattr(app_module, "advisor", fake_advisor)
    for limiter in app_module.RATE_LIMITS.values():
        limiter.reset()
    return TestClient(app_module.app, raise_server_exceptions=False)


def test_health(client) -> None:
    assert client.get("/health").json()["status"] == "ok"


def test_index_served(client) -> None:
    response = client.get("/")
    assert response.status_code == 200
    assert "Get advice" in response.text


def test_recommend(client, fake_advisor) -> None:
    response = client.post("/recommend", json={"team": "5767275", "gameweek": 8})
    assert response.status_code == 200
    body = response.json()
    assert body["gameweek"] == 9
    assert len(body["squad"]) == 15
    best = body["recommendations"][0]
    assert len(best["starting"]) == 11 and len(best["bench"]) == 4
    assert len(best["lineups"]) == len(body["gameweeks"]) == 3
    assert {c["chip"] for c in body["chips"]} == {"wildcard", "freehit", "bboost", "3xc"}
    for t in best["transfers"]:  # serialised with the "in" alias
        assert t["in"]["name"] and t["out"]["name"] and t["why"]
    assert best["starting"][0]["horizon"][0]["gameweek"] == 9
    assert fake_advisor.calls == [("5767275", 8)]


@pytest.mark.parametrize(
    "error, status",
    [
        (ValueError("bad team"), 400),
        (api.NotFoundError("Team 1 not found"), 404),
        (api.FPLAPIError("down"), 503),
        (FileNotFoundError("no model"), 503),
    ],
)
def test_recommend_errors(client, fake_advisor, error, status) -> None:
    fake_advisor.error = error
    response = client.post("/recommend", json={"team": "1"})
    assert response.status_code == status
    assert response.json()["detail"]


def test_recommend_validates_gameweek(client) -> None:
    assert client.post("/recommend", json={"team": "1", "gameweek": 99}).status_code == 422


def test_predictions(client) -> None:
    horizon = client.get("/predictions", params={"sort": "horizon", "limit": 3}).json()
    totals = [p["horizon_points"] for p in horizon["players"]]
    assert totals == sorted(totals, reverse=True)
    body = client.get("/predictions", params={"position": "MID", "limit": 5}).json()
    assert body["gameweek"] == 9
    assert len(body["players"]) == 5
    assert {p["position"] for p in body["players"]} == {"MID"}
    pts = [p["predicted_points"] for p in body["players"]]
    assert pts == sorted(pts, reverse=True)
