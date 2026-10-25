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


class Recommendation(BaseModel):
    rank: int
    n_transfers: int
    transfers_out: list[Player]
    transfers_in: list[Player]
    hit_cost: int
    expected_points: float
    net_points: float
    gain: float
    reason: str
    captain: Player
    vice_captain: Player
    starting: list[Player]
    bench: list[Player]


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


class PredictionsResponse(BaseModel):
    gameweek: int
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
) -> PredictionsResponse:
    try:
        gameweek, preds = advisor.predictions()
    except (ValueError, api.FPLAPIError, FileNotFoundError) as exc:
        raise _errors(exc) from exc
    players = PlayerView(preds).ranked(position, limit)
    return PredictionsResponse(gameweek=gameweek, players=players)


@app.get("/", include_in_schema=False)
def index() -> FileResponse:
    return FileResponse(STATIC / "index.html")
