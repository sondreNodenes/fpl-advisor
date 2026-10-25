"""Hostile inputs, abuse controls, safe errors and security headers."""

import asyncio
import json
import time

import pytest
from fastapi.testclient import TestClient

from fpl_advisor import api, security
from fpl_advisor import app as app_module
from tests.test_api import FakeResponse, make_client


@pytest.fixture
def client(fake_advisor, monkeypatch) -> TestClient:
    monkeypatch.setattr(app_module, "advisor", fake_advisor)
    for limiter in app_module.RATE_LIMITS.values():
        limiter.reset()
    return TestClient(app_module.app, raise_server_exceptions=False)


# --- Input validation (SSRF, injection, bounds) ---------------------------------------

VALID = [
    ("5767275", (5767275, None)),
    ("https://fantasy.premierleague.com/entry/5767275/event/8", (5767275, 8)),
    ("https://fantasy.premierleague.com/en/entry/5767275/event/8/", (5767275, 8)),
    ("fantasy.premierleague.com/entry/123/history", (123, None)),
    ("HTTPS://Fantasy.PremierLeague.com/entry/42", (42, None)),
]


@pytest.mark.parametrize("text, expected", VALID)
def test_valid_team_inputs(text, expected) -> None:
    assert api.parse_team(text) == expected


HOSTILE = [
    "http://169.254.169.254/entry/5",  # cloud metadata server (SSRF)
    "http://localhost:8080/entry/5",
    "https://example.com/entry/5767275",
    "https://fantasy.premierleague.com.evil.com/entry/5",  # look-alike host
    "https://evil.com/fantasy.premierleague.com/entry/5",
    "https://user@fantasy.premierleague.com/entry/5",  # userinfo trick
    "https://fantasy.premierleague.com:8443/entry/5",
    "file:///etc/passwd",
    "file://fantasy.premierleague.com/entry/5",
    "javascript:alert(1)//fantasy.premierleague.com/entry/5",
    "https://fantasy.premierleague.com/entry/5/../../admin",  # path traversal
    "https://fantasy.premierleague.com/entry/../5",
    "https://fantasy.premierleague.com/entry/5?redirect=http://evil.com",
    "5767275/../../etc",
    "١٢٣",  # Arabic-Indic digits: str.isdigit() accepts them
    "１２３",  # fullwidth digits
    "5767275\x00",
    "5767275\n6",
    "-5",
    "0",
    "1e9",
    "123456789",  # above MAX_TEAM_ID
    "9" * 5000,  # huge input
    "https://fantasy.premierleague.com/entry/" + "9" * 300,
    "",
    "   ",
]


@pytest.mark.parametrize("text", HOSTILE)
def test_hostile_team_inputs_rejected(text) -> None:
    with pytest.raises(ValueError) as err:
        api.parse_team(text)
    assert text.strip() not in str(err.value) or not text.strip()  # input is not echoed


@pytest.mark.parametrize("value", [0, -1, api.MAX_TEAM_ID + 1, True])
def test_team_id_bounds(value) -> None:
    with pytest.raises(ValueError):
        api.parse_team(value)


@pytest.mark.parametrize("gw", [0, 39, 99])
def test_gameweek_in_url_bounded(gw) -> None:
    with pytest.raises(ValueError):
        api.parse_team(f"https://fantasy.premierleague.com/entry/5/event/{gw}")


@pytest.mark.parametrize("team", HOSTILE[:6] + ["x" * 201])
def test_recommend_rejects_hostile_input_without_work(client, fake_advisor, team) -> None:
    response = client.post("/recommend", json={"team": team})
    assert response.status_code in (400, 422)
    assert fake_advisor.calls == []  # rejected before the advisor (or the FPL API) is called


def test_season_over_is_a_clear_error() -> None:
    class Bootstrap:
        def bootstrap(self):
            return {
                "events": [
                    {
                        "id": 38,
                        "finished": True,
                        "data_checked": True,
                        "is_current": True,
                        "is_next": False,
                        "deadline_time": "2027-05-23T10:00:00Z",
                    }
                ]
            }

    advisor = app_module.Advisor(client=Bootstrap())
    with pytest.raises(ValueError, match="Season is over"):
        advisor.forecast(39)


# --- Outbound client --------------------------------------------------------------------


def test_client_never_follows_redirects(tmp_path) -> None:
    client, session = make_client(tmp_path, [FakeResponse(302)])
    with pytest.raises(api.FPLAPIError):
        client.bootstrap()
    assert session.kwargs[0]["allow_redirects"] is False


def test_client_rejects_oversized_responses(tmp_path, monkeypatch) -> None:
    monkeypatch.setattr(api, "MAX_RESPONSE_BYTES", 1000)
    big = FakeResponse(200, {"x": "y" * 5000})
    client, _ = make_client(tmp_path, [big])
    with pytest.raises(api.FPLAPIError, match="larger than"):
        client.bootstrap()
    declared = FakeResponse(200, {"x": 1}, headers={"Content-Length": "999999"})
    client, _ = make_client(tmp_path / "b", [declared])
    with pytest.raises(api.FPLAPIError, match="too large"):
        client.bootstrap()


@pytest.mark.parametrize(
    "path", ["../secret/", "http://evil.com/", "entry/1/?x=1", "entry/%2e%2e/"]
)
def test_client_only_requests_safe_paths(tmp_path, path) -> None:
    client, session = make_client(tmp_path, [])
    with pytest.raises(ValueError):
        client.get(path)
    assert session.calls == 0


# --- Abuse controls ---------------------------------------------------------------------


def test_rate_limit_returns_429_with_retry_after(client) -> None:
    codes = [client.post("/recommend", json={"team": "1"}).status_code for _ in range(12)]
    assert codes[:10] == [200] * 10
    assert codes[10:] == [429, 429]
    response = client.post("/recommend", json={"team": "1"})
    assert int(response.headers["retry-after"]) >= 1
    assert response.json() == {"detail": "Too many requests, slow down"}


def test_rate_limiter_window_and_memory_bound() -> None:
    limiter = security.RateLimiter(limit=2, window=10, max_keys=3)
    assert [limiter.check("a", now=t) for t in (0, 1)] == [0, 0]
    assert limiter.check("a", now=2) == pytest.approx(8)
    assert limiter.check("a", now=10.5) == 0  # first hit left the window
    for key in "bcde":
        limiter.check(key, now=11)
    assert len(limiter._hits) == 3


def test_forwarded_for_uses_last_hop_only_when_trusted() -> None:
    scope = {"headers": [(b"x-forwarded-for", b"6.6.6.6, 1.2.3.4")], "client": ("10.0.0.1", 1)}
    assert security.client_ip(scope, trust_proxy=True) == "1.2.3.4"  # spoofed first entry ignored
    assert security.client_ip(scope, trust_proxy=False) == "10.0.0.1"


def test_body_size_limit(client) -> None:
    padded = '{"team": "1", "pad": "' + "x" * 10_000 + '"}'
    response = client.post(
        "/recommend", content=padded, headers={"Content-Type": "application/json"}
    )
    assert response.status_code == 413

    def chunks():  # no Content-Length: counted while streaming
        yield b'{"team": "1", "pad": "'
        for _ in range(10):
            yield b"x" * 1000
        yield b'"}'

    response = client.post(
        "/recommend", content=chunks(), headers={"Content-Type": "application/json"}
    )
    assert response.status_code == 413


def test_work_limiter_busy_and_timeout() -> None:
    async def scenario():
        limiter = security.WorkLimiter(slots=1, queue_timeout=0.2, timeout=0.3)
        slow = asyncio.create_task(limiter.run(lambda: time.sleep(0.5)))
        await asyncio.sleep(0.05)
        with pytest.raises(security.Busy):
            await limiter.run(lambda: 1)
        with pytest.raises(TimeoutError):
            await slow
        await asyncio.sleep(0.3)  # the slow thread finishes and frees its slot
        assert await limiter.run(lambda: 42) == 42

    asyncio.run(scenario())


@pytest.mark.parametrize("error, status", [(security.Busy(), 503), (TimeoutError(), 504)])
def test_busy_and_timeout_responses(client, fake_advisor, error, status) -> None:
    fake_advisor.error = error
    assert client.post("/recommend", json={"team": "1"}).status_code == status


# --- Safe errors ------------------------------------------------------------------------


@pytest.mark.parametrize(
    "error, leak",
    [
        (api.FPLAPIError("HTTPSConnectionPool(host='fantasy...') internal detail"), "internal"),
        (FileNotFoundError("No model at /data/models/model.joblib"), "/data/"),
    ],
)
def test_upstream_errors_do_not_leak_details(client, fake_advisor, error, leak) -> None:
    fake_advisor.error = error
    response = client.post("/recommend", json={"team": "1"})
    assert response.status_code == 503
    assert leak not in response.text


def test_unexpected_error_hides_stack_trace(client, fake_advisor) -> None:
    fake_advisor.error = RuntimeError("secret internal state")
    response = client.post("/recommend", json={"team": "1"})
    assert response.status_code == 500
    assert response.json() == {"detail": "Internal error"}
    assert "secret" not in response.text and "Traceback" not in response.text


def test_logs_are_json_with_validated_id_only(client, caplog) -> None:
    client.post("/recommend", json={"team": "https://fantasy.premierleague.com/entry/77"})
    record = [r for r in caplog.records if r.getMessage() == "recommend"][-1]
    line = security.JsonFormatter().format(record)
    entry = json.loads(line)
    assert entry["message"] == "recommend" and entry["team_id"] == 77
    assert "premierleague" not in line  # raw input is not logged


# --- Headers ----------------------------------------------------------------------------


@pytest.mark.parametrize("path", ["/", "/health", "/static/app.js"])
def test_security_headers(client, path) -> None:
    response = client.get(path)
    assert response.status_code == 200
    h = response.headers
    assert h["x-content-type-options"] == "nosniff"
    assert h["x-frame-options"] == "DENY"
    assert h["referrer-policy"] == "no-referrer"
    assert "frame-ancestors 'none'" in h["content-security-policy"]
    assert "unsafe-inline" not in h["content-security-policy"]


def test_headers_also_on_rejections(client) -> None:
    response = client.post("/recommend", content=b"x" * 10_000)
    assert response.status_code == 413
    assert response.headers["x-content-type-options"] == "nosniff"


def test_page_has_no_inline_script_or_style(client) -> None:
    html = client.get("/").text
    assert "<script>" not in html and "<style>" not in html and "style=" not in html
    assert 'src="/static/app.js"' in html
    assert "style=" not in client.get("/static/app.js").text
