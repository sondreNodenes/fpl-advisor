"""FastAPI app: ``POST /recommend``, ``GET /predictions``, ``GET /health`` and a static page."""

from __future__ import annotations

from dataclasses import asdict
from pathlib import Path

from fastapi import FastAPI, HTTPException, Query
from fastapi.responses import FileResponse
from pydantic import BaseModel, Field

from fpl_advisor import __version__, api
from fpl_advisor.advisor import Advisor, PlayerView

STATIC = Path(__file__).parent / "static"

app = FastAPI(title="FPL Advisor", version=__version__)
advisor = Advisor()


class RecommendRequest(BaseModel):
    team: str = Field(..., description="FPL team ID or any URL containing /entry/{id}")
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


def _errors(exc: Exception) -> HTTPException:
    if isinstance(exc, ValueError):
        return HTTPException(400, str(exc))
    if isinstance(exc, api.NotFoundError):
        return HTTPException(404, str(exc))
    if isinstance(exc, api.FPLAPIError):
        return HTTPException(503, f"FPL API unavailable, try again shortly ({exc})")
    if isinstance(exc, FileNotFoundError):
        return HTTPException(503, f"Model or data not ready: {exc}")
    raise exc


@app.get("/health")
def health() -> dict[str, str]:
    return {"status": "ok", "version": __version__}


@app.post("/recommend", response_model=RecommendResponse)
def recommend(req: RecommendRequest) -> RecommendResponse:
    try:
        advice = advisor.advise(req.team, req.gameweek)
    except (ValueError, api.FPLAPIError, FileNotFoundError) as exc:
        raise _errors(exc) from exc
    return RecommendResponse(**asdict(advice))


@app.get("/predictions", response_model=PredictionsResponse)
def predictions(
    position: str | None = Query(None, pattern="^(GK|DEF|MID|FWD)$"),
    limit: int = Query(50, ge=1, le=1000),
    sort: str = Query("next", pattern="^(next|horizon)$"),
) -> PredictionsResponse:
    try:
        gameweeks, preds = advisor.forecast()
    except (ValueError, api.FPLAPIError, FileNotFoundError) as exc:
        raise _errors(exc) from exc
    by = "horizon_points" if sort == "horizon" else "predicted_points"
    players = PlayerView(preds, gameweeks).ranked(position, limit, by)
    return PredictionsResponse(gameweek=gameweeks[0], gameweeks=gameweeks, players=players)


@app.get("/", include_in_schema=False)
def index() -> FileResponse:
    return FileResponse(STATIC / "index.html")
