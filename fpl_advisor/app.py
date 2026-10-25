"""FastAPI app: ``POST /recommend``, ``GET /predictions``, ``GET /health`` and a static page."""

from __future__ import annotations

import os
import time
from dataclasses import asdict
from pathlib import Path

from fastapi import FastAPI, HTTPException, Query, Request
from fastapi.middleware.gzip import GZipMiddleware
from fastapi.responses import FileResponse, JSONResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field

from fpl_advisor import __version__, api, security
from fpl_advisor.advisor import Advisor, PlayerView

STATIC = Path(__file__).parent / "static"
MAX_BODY_BYTES = 4 * 1024  # a team URL and a gameweek fit easily
WORK_SLOTS = int(os.environ.get("FPL_WORK_SLOTS", "2"))  # concurrent optimizations per instance
WORK_QUEUE_SECONDS = 10.0
WORK_TIMEOUT_SECONDS = 60.0  # below Cloud Run's 120 s request timeout
# Behind Cloud Run, client IPs come from X-Forwarded-For (set by Google's front end).
TRUST_PROXY = os.environ.get("FPL_TRUST_PROXY_HEADERS") == "1"

RATE_LIMITS = {  # per client IP per minute
    ("*", "*"): security.RateLimiter(limit=120, window=60),  # any route, incl. page and static
    ("POST", "/recommend"): security.RateLimiter(limit=10, window=60),
    ("GET", "/predictions"): security.RateLimiter(limit=60, window=60),
}

log = security.json_logger("fpl_advisor.app")
app = FastAPI(title="FPL Advisor", version=__version__)
app.add_middleware(
    security.AbuseControlMiddleware,
    limits=RATE_LIMITS,
    max_body_bytes=MAX_BODY_BYTES,
    trust_proxy=TRUST_PROXY,
    logger=log,
)
# Compressing the page, script and JSON cuts egress per request (the main flood cost).
app.add_middleware(GZipMiddleware, minimum_size=1000)
app.add_middleware(security.SecurityHeadersMiddleware)  # outermost: also covers 413/429
app.mount("/static", StaticFiles(directory=STATIC), name="static")
advisor = Advisor()
work = security.WorkLimiter(WORK_SLOTS, WORK_QUEUE_SECONDS, WORK_TIMEOUT_SECONDS)


class RecommendRequest(BaseModel):
    team: str = Field(
        ...,
        max_length=api.MAX_INPUT_LENGTH,
        description="FPL team ID or a fantasy.premierleague.com/entry/{id} URL",
    )
    gameweek: int | None = Field(None, ge=1, le=38, description="Squad gameweek (default: current)")


class Fixture(BaseModel):
    opponent: str
    home: bool
    difficulty: int | None


class WeekPoints(BaseModel):
    gameweek: int
    points: float
    opponents: list[Fixture]


class Player(BaseModel):
    id: int
    name: str
    team: str
    position: str
    price: float
    predicted_points: float
    fixtures: int
    availability: float
    ep_next: float | None
    news: str
    form_points: float = 0.0
    form_minutes: float = 0.0
    opponents: list[Fixture] = []
    horizon: list[WeekPoints] = []
    horizon_points: float | None = None


class Transfer(BaseModel):
    out: Player
    in_: Player = Field(..., alias="in")
    points_gain: float
    why: list[str]

    model_config = {"populate_by_name": True}


class Lineup(BaseModel):
    gameweek: int | None
    points: float
    captain: str
    bench_points: float


class ChipValue(BaseModel):
    gameweek: int
    value: float


class Chip(BaseModel):
    chip: str
    name: str
    verdict: str
    available_until: int | None
    values: list[ChipValue]
    threshold: float
    detail: str


class Recommendation(BaseModel):
    rank: int
    n_transfers: int
    transfers_out: list[Player]
    transfers_in: list[Player]
    transfers: list[Transfer] = []
    hit_cost: int
    expected_points: float
    net_points: float
    gain: float
    first_gain: float = 0.0
    bank_after: float | None = None
    reason: str
    captain: Player
    vice_captain: Player
    starting: list[Player]
    bench: list[Player]
    lineups: list[Lineup] = []


class RecommendResponse(BaseModel):
    team_id: int
    team_name: str
    manager: str
    picks_gameweek: int
    gameweek: int
    data_through_gameweek: int
    bank: float
    free_transfers: int
    squad: list[Player]
    recommendations: list[Recommendation]
    notes: list[str]
    gameweeks: list[int] = []
    weights: list[float] = []
    chips: list[Chip] = []


class PredictionsResponse(BaseModel):
    gameweek: int
    gameweeks: list[int]
    players: list[Player]


EXPECTED_ERRORS = (ValueError, api.FPLAPIError, FileNotFoundError, security.Busy, TimeoutError)


def _errors(exc: Exception) -> HTTPException:
    """Map expected failures to clear messages. Texts shown to clients are written here or
    in our own validation code; upstream and file system details stay in the logs."""
    if isinstance(exc, ValueError):  # our validation messages, no raw input echoed
        return HTTPException(400, str(exc))
    if isinstance(exc, api.NotFoundError):
        return HTTPException(404, str(exc))
    if isinstance(exc, api.FPLAPIError):
        return HTTPException(503, "The FPL API is unavailable right now; try again shortly")
    if isinstance(exc, FileNotFoundError):
        return HTTPException(503, "Predictions are not ready yet; try again later")
    if isinstance(exc, security.Busy):
        return HTTPException(503, "Busy, try again in a few seconds", {"Retry-After": "5"})
    if isinstance(exc, TimeoutError):
        return HTTPException(504, "The request took too long; try again shortly")
    raise exc


async def _run(event: str, fn, **fields):
    """Run CPU-heavy work in the limited pool; log one structured line per request."""
    started = time.monotonic()
    try:
        result = await work.run(fn)
    except EXPECTED_ERRORS as exc:
        error = _errors(exc)
        log.warning(
            event,
            extra={
                "fields": fields
                | {"status": error.status_code, "error": type(exc).__name__}
                | _elapsed(started)
            },
        )
        raise error from exc
    log.info(event, extra={"fields": fields | {"status": 200} | _elapsed(started)})
    return result


def _elapsed(started: float) -> dict[str, int]:
    return {"duration_ms": round((time.monotonic() - started) * 1000)}


@app.exception_handler(Exception)
async def unexpected_error(request: Request, exc: Exception) -> JSONResponse:
    """Never send a stack trace to the client; log it for us instead."""
    log.error("unhandled error", exc_info=exc, extra={"fields": {"path": request.url.path}})
    return JSONResponse({"detail": "Internal error"}, status_code=500)


@app.get("/health")
def health() -> dict[str, str]:
    return {"status": "ok", "version": __version__}


@app.post("/recommend", response_model=RecommendResponse)
async def recommend(req: RecommendRequest) -> RecommendResponse:
    try:
        team_id, _ = api.parse_team(req.team)  # validate before any work or logging
    except ValueError as exc:
        raise _errors(exc) from exc
    advice = await _run(
        "recommend",
        lambda: advisor.advise(req.team, req.gameweek),
        team_id=team_id,
        gameweek=req.gameweek,
    )
    return RecommendResponse(**asdict(advice))


@app.get("/predictions", response_model=PredictionsResponse)
async def predictions(
    position: str | None = Query(None, pattern="^(GK|DEF|MID|FWD)$"),
    limit: int = Query(50, ge=1, le=1000),
    sort: str = Query("next", pattern="^(next|horizon)$"),
) -> PredictionsResponse:
    gameweeks, preds = await _run("predictions", advisor.forecast, position=position)
    by = "horizon_points" if sort == "horizon" else "predicted_points"
    players = PlayerView(preds, gameweeks).ranked(position, limit, by)
    return PredictionsResponse(gameweek=gameweeks[0], gameweeks=gameweeks, players=players)


@app.get("/", include_in_schema=False)
def index() -> FileResponse:
    return FileResponse(STATIC / "index.html")
