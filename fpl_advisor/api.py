"""Thin, polite client for the public (unofficial) FPL JSON API, plus typed parsers."""

from __future__ import annotations

import json
import re
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import pandas as pd
import requests
from requests.adapters import HTTPAdapter
from urllib3.util.retry import Retry

BASE_URL = "https://fantasy.premierleague.com/api/"
USER_AGENT = "fpl-advisor/0.1 (+https://github.com/sondreNodenes/fpl-advisor)"
DEFAULT_CACHE_DIR = Path("data/cache")
MAX_RESPONSE_BYTES = 20 * 1024 * 1024  # bootstrap-static is ~2 MB; anything far larger is wrong
_SAFE_PATH = re.compile(r"[a-z0-9_-]+(?:/[a-z0-9_-]+)*/")

POSITIONS = {1: "GK", 2: "DEF", 3: "MID", 4: "FWD"}

# Per-fixture player stats kept from element-summary history (and the community dataset).
HISTORY_COLUMNS = [
    "player_id",
    "fixture",
    "round",
    "kickoff_time",
    "opponent_team",
    "was_home",
    "team_h_score",
    "team_a_score",
    "total_points",
    "minutes",
    "starts",
    "goals_scored",
    "assists",
    "clean_sheets",
    "goals_conceded",
    "bonus",
    "bps",
    "expected_goals",
    "expected_assists",
    "expected_goal_involvements",
    "expected_goals_conceded",
    "influence",
    "creativity",
    "threat",
    "ict_index",
    "value",
]
_FLOAT_HISTORY = [
    "expected_goals",
    "expected_assists",
    "expected_goal_involvements",
    "expected_goals_conceded",
    "influence",
    "creativity",
    "threat",
    "ict_index",
]


class FPLAPIError(RuntimeError):
    """The FPL API could not be reached or returned an unexpected response."""


class NotFoundError(FPLAPIError):
    """The requested resource (usually a team) does not exist or is not public."""


LAST_GAMEWEEK = 38
MAX_TEAM_ID = 99_999_999  # real IDs are around 1e7; the bound keeps junk out of upstream calls
MAX_INPUT_LENGTH = 200
_TEAM_ID = re.compile(r"[0-9]{1,8}")
# The only URLs accepted: fantasy.premierleague.com[/xx]/entry/{id}[/event/{gw}|/history|...]
_TEAM_URL = re.compile(
    r"(?:https?://)?(?:www\.)?fantasy\.premierleague\.com(?:/[a-z]{2})?"
    r"/entry/(?P<id>[0-9]{1,8})(?:/event/(?P<gw>[0-9]{1,2})|/history|/transfers)?/?",
    re.IGNORECASE,
)
_INVALID_TEAM = "Enter an FPL team ID (digits only) or a fantasy.premierleague.com/entry/{id} URL"


def parse_team(team: str | int) -> tuple[int, int | None]:
    """Validate a team ID or FPL URL; return ``(team_id, gameweek or None)``.

    Only ASCII digits and the exact FPL URL pattern are accepted. The input is never
    fetched: the ID is extracted and requests always go to ``BASE_URL``.
    """
    if isinstance(team, bool) or not isinstance(team, int | str):
        raise ValueError(_INVALID_TEAM)
    gameweek = None
    if isinstance(team, int):
        team_id = team
    else:
        text = team.strip()
        if len(text) > MAX_INPUT_LENGTH:
            raise ValueError(_INVALID_TEAM)
        if _TEAM_ID.fullmatch(text):
            team_id = int(text)
        elif match := _TEAM_URL.fullmatch(text):
            team_id = int(match["id"])
            gameweek = int(match["gw"]) if match["gw"] else None
        else:
            raise ValueError(_INVALID_TEAM)
    if not 1 <= team_id <= MAX_TEAM_ID:
        raise ValueError(_INVALID_TEAM)
    if gameweek is not None and not 1 <= gameweek <= LAST_GAMEWEEK:
        raise ValueError(f"Gameweek must be between 1 and {LAST_GAMEWEEK}")
    return team_id, gameweek


def parse_team_id(team: str | int) -> int:
    """The team ID from a raw ID or an FPL URL (see ``parse_team``)."""
    return parse_team(team)[0]


def parse_gameweek_from_url(team: str | int) -> int | None:
    """The gameweek from an FPL ``/event/{gw}`` URL, if present."""
    return parse_team(team)[1]


def read_limited(response: requests.Response, limit: int) -> bytes:
    """The response body, or FPLAPIError once it grows past ``limit`` bytes."""
    declared = response.headers.get("Content-Length")
    if declared and declared.isdigit() and int(declared) > limit:
        raise FPLAPIError(f"FPL API response too large ({declared} bytes)")
    body = bytearray()
    try:
        for chunk in response.iter_content(chunk_size=64 * 1024):
            body += chunk
            if len(body) > limit:
                raise FPLAPIError(f"FPL API response larger than {limit} bytes")
    except requests.RequestException as exc:
        raise FPLAPIError(f"FPL API connection failed while reading: {exc}") from exc
    return bytes(body)


class FPLClient:
    """HTTP client with retries, a file cache and a minimum interval between network calls."""

    def __init__(
        self,
        cache_dir: Path | str = DEFAULT_CACHE_DIR,
        ttl_seconds: float = 3600,
        min_interval: float = 1.0,
        session: requests.Session | None = None,
        timeout: tuple[float, float] = (5, 20),  # connect, read
    ) -> None:
        self.cache_dir = Path(cache_dir)
        self.ttl_seconds = ttl_seconds
        self.min_interval = min_interval
        self.timeout = timeout
        self._last_request = 0.0
        self.session = session or self._default_session()

    @staticmethod
    def _default_session() -> requests.Session:
        session = requests.Session()
        retry = Retry(
            total=3,
            backoff_factor=2,
            status_forcelist=[429, 500, 502, 503, 504],
            allowed_methods=["GET"],
        )
        session.mount("https://", HTTPAdapter(max_retries=retry))
        session.headers["User-Agent"] = USER_AGENT
        return session

    def _cache_path(self, path: str) -> Path:
        return self.cache_dir / (path.strip("/").replace("/", "_") + ".json")

    def get(self, path: str, ttl_seconds: float | None = None) -> Any:
        """GET ``BASE_URL + path`` as JSON, served from cache when fresh.

        Only relative API paths are allowed, redirects are not followed (a redirect could
        point at another host) and bodies over ``MAX_RESPONSE_BYTES`` are rejected.
        """
        if not _SAFE_PATH.fullmatch(path):
            raise ValueError(f"Unexpected FPL API path: {path!r}")
        ttl = self.ttl_seconds if ttl_seconds is None else ttl_seconds
        cache_file = self._cache_path(path)
        if cache_file.exists() and time.time() - cache_file.stat().st_mtime < ttl:
            return json.loads(cache_file.read_text())

        wait = self.min_interval - (time.monotonic() - self._last_request)
        if wait > 0:
            time.sleep(wait)
        try:
            response = self.session.get(
                BASE_URL + path, timeout=self.timeout, allow_redirects=False, stream=True
            )
        except requests.RequestException as exc:
            raise FPLAPIError(f"FPL API unavailable ({path}): {exc}") from exc
        finally:
            self._last_request = time.monotonic()

        try:
            if response.status_code == 404:
                raise NotFoundError(f"Not found: {path}")
            if response.status_code != 200:
                raise FPLAPIError(f"FPL API returned HTTP {response.status_code} for {path}")
            body = read_limited(response, MAX_RESPONSE_BYTES)
        finally:
            response.close()
        try:
            data = json.loads(body)
        except ValueError as exc:
            # During gameweek updates the API serves an HTML maintenance page.
            raise FPLAPIError(f"FPL API returned non-JSON for {path} (being updated?)") from exc

        cache_file.parent.mkdir(parents=True, exist_ok=True)
        cache_file.write_text(json.dumps(data))
        return data

    def bootstrap(self) -> dict[str, Any]:
        return self.get("bootstrap-static/")

    def fixtures(self) -> list[dict[str, Any]]:
        return self.get("fixtures/")

    def element_summary(self, player_id: int) -> dict[str, Any]:
        return self.get(f"element-summary/{player_id}/")

    def entry(self, team_id: int) -> dict[str, Any]:
        return self.get(f"entry/{team_id}/")

    def entry_history(self, team_id: int) -> dict[str, Any]:
        return self.get(f"entry/{team_id}/history/")

    def picks(self, team_id: int, gameweek: int) -> dict[str, Any]:
        return self.get(f"entry/{team_id}/event/{gameweek}/picks/")

    def live(self, gameweek: int) -> dict[str, Any]:
        return self.get(f"event/{gameweek}/live/")


@dataclass(frozen=True)
class GameweekState:
    current: int | None
    next: int | None
    last_finished: int | None
    season: str


def gameweek_state(bootstrap: dict[str, Any]) -> GameweekState:
    events = bootstrap["events"]
    current = next((e["id"] for e in events if e["is_current"]), None)
    nxt = next((e["id"] for e in events if e["is_next"]), None)
    finished = [e["id"] for e in events if e["finished"] and e["data_checked"]]
    start_year = int(events[0]["deadline_time"][:4])
    season = f"{start_year}-{(start_year + 1) % 100:02d}"
    return GameweekState(current, nxt, max(finished) if finished else None, season)


def parse_players(bootstrap: dict[str, Any]) -> pd.DataFrame:
    df = pd.DataFrame(bootstrap["elements"])
    out = pd.DataFrame(
        {
            "player_id": df["id"].astype(int),
            "web_name": df["web_name"],
            "team": df["team"].astype(int),
            "element_type": df["element_type"].astype(int),
            "position": df["element_type"].map(POSITIONS),
            "price": df["now_cost"].astype(float) / 10,
            "status": df["status"],
            "chance_of_playing_next_round": pd.to_numeric(
                df["chance_of_playing_next_round"], errors="coerce"
            ),
            "news": df["news"],
            "form": pd.to_numeric(df["form"], errors="coerce"),
            "ep_next": pd.to_numeric(df["ep_next"], errors="coerce"),
            "total_points": df["total_points"].astype(int),
            "minutes": df["minutes"].astype(int),
            "expected_goals": pd.to_numeric(df["expected_goals"], errors="coerce"),
            "expected_assists": pd.to_numeric(df["expected_assists"], errors="coerce"),
            "ict_index": pd.to_numeric(df["ict_index"], errors="coerce"),
            "selected_by_percent": pd.to_numeric(df["selected_by_percent"], errors="coerce"),
        }
    )
    return out


def parse_teams(bootstrap: dict[str, Any]) -> pd.DataFrame:
    df = pd.DataFrame(bootstrap["teams"])
    cols = ["id", "name", "short_name", "strength_overall_home", "strength_overall_away"]
    return df[cols].rename(columns={"id": "team"})


def parse_fixtures(fixtures: list[dict[str, Any]]) -> pd.DataFrame:
    cols = [
        "id",
        "event",
        "kickoff_time",
        "team_h",
        "team_a",
        "team_h_score",
        "team_a_score",
        "team_h_difficulty",
        "team_a_difficulty",
        "finished",
    ]
    df = pd.DataFrame(fixtures, columns=cols).rename(columns={"id": "fixture"})
    df["event"] = df["event"].astype("Int64")  # None for unscheduled (postponed) fixtures
    df["team_h_score"] = df["team_h_score"].astype("Int64")
    df["team_a_score"] = df["team_a_score"].astype("Int64")
    return df


def parse_history(summary: dict[str, Any]) -> pd.DataFrame:
    """Per-fixture rows from ``element-summary/{id}/history`` (a double gameweek gives 2 rows)."""
    df = pd.DataFrame(summary["history"])
    if df.empty:
        return pd.DataFrame(columns=HISTORY_COLUMNS)
    df = df.rename(columns={"element": "player_id"})
    df[_FLOAT_HISTORY] = df[_FLOAT_HISTORY].apply(pd.to_numeric, errors="coerce")
    df["value"] = df["value"].astype(float) / 10
    return df[HISTORY_COLUMNS]


@dataclass(frozen=True)
class Pick:
    player_id: int
    position: int
    multiplier: int
    is_captain: bool
    is_vice_captain: bool
    element_type: int


@dataclass(frozen=True)
class Picks:
    gameweek: int
    picks: list[Pick]
    bank: float
    squad_value: float
    event_transfers: int
    event_transfers_cost: int
    active_chip: str | None


def parse_picks(data: dict[str, Any]) -> Picks:
    history = data["entry_history"]
    picks = [
        Pick(
            player_id=p["element"],
            position=p["position"],
            multiplier=p["multiplier"],
            is_captain=p["is_captain"],
            is_vice_captain=p["is_vice_captain"],
            element_type=p["element_type"],
        )
        for p in data["picks"]
    ]
    return Picks(
        gameweek=history["event"],
        picks=picks,
        bank=history["bank"] / 10,
        squad_value=history["value"] / 10,
        event_transfers=history["event_transfers"],
        event_transfers_cost=history["event_transfers_cost"],
        active_chip=data.get("active_chip"),
    )


def free_transfers(history: dict[str, Any], up_to_gameweek: int) -> int:
    """Estimate free transfers available for the gameweek after ``up_to_gameweek``.

    The API does not expose this directly. Current rules: 1 per gameweek, unused ones roll
    over up to 5, and wildcard / free hit weeks keep the count unchanged.
    """
    chips = {c["event"]: c["name"] for c in history.get("chips", [])}
    rows = sorted(history["current"], key=lambda r: r["event"])
    ft = 0
    for i, row in enumerate(rows):
        if row["event"] > up_to_gameweek:
            break
        if i == 0:
            ft = 1  # first gameweek is played with an unlimited initial squad
            continue
        if chips.get(row["event"]) in {"wildcard", "freehit"}:
            continue
        ft = min(5, max(0, ft - row["event_transfers"]) + 1)
    return ft
