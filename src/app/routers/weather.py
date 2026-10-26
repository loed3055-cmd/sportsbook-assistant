"""Weather at the game venue: fetch kickoff-hour forecasts on demand and store them."""

import urllib.request
from dataclasses import asdict
from datetime import datetime, timezone

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session

from app.db import get_db
from app.models.game_weather import GameWeather as GameWeatherModel
from app.schemas.weather import GameWeather, WeatherRefresh
from app.services.grading import canonical_matchup
from app.services.stadiums import venue_for
from app.services.weather import forecast_for

router = APIRouter()

# Module-level so tests can swap them out.
urlopen = urllib.request.urlopen


def utcnow() -> datetime:
    return datetime.now(timezone.utc)


@router.post("/refresh", response_model=list[GameWeather])
def refresh_weather(payload: WeatherRefresh, db: Session = Depends(get_db)):
    """Fetch the kickoff forecast for each game and upsert it (season/week/matchup)."""

    keys = [(payload.season, payload.week, g.matchup) for g in payload.games]
    if len(set(keys)) != len(keys):
        raise HTTPException(status_code=422, detail="duplicate matchup in request")

    now = utcnow()
    rows: list[GameWeatherModel] = []
    for game in payload.games:
        venue = venue_for(game.matchup, game.venue)
        forecast = forecast_for(venue, game.kickoff_utc, now=now, urlopen=urlopen)
        values = {
            "kickoff_utc": game.kickoff_utc.astimezone(timezone.utc),
            "venue_name": venue.name,
            "city": venue.city,
            "roof": venue.roof,
            "fetched_at": now,
            **asdict(forecast),
        }
        row = (
            db.query(GameWeatherModel)
            .filter_by(season=payload.season, week=payload.week, matchup=game.matchup)
            .one_or_none()
        )
        if row is None:
            row = GameWeatherModel(season=payload.season, week=payload.week, matchup=game.matchup)
            db.add(row)
        for field, value in values.items():
            setattr(row, field, value)
        rows.append(row)

    db.commit()
    for row in rows:
        db.refresh(row)
    return rows


@router.get("/", response_model=list[GameWeather])
def list_weather(
    season: int | None = None,
    week: int | None = None,
    matchup: str | None = None,
    db: Session = Depends(get_db),
):
    query = db.query(GameWeatherModel)
    if season is not None:
        query = query.filter(GameWeatherModel.season == season)
    if week is not None:
        query = query.filter(GameWeatherModel.week == week)
    if matchup is not None:
        try:
            matchup = canonical_matchup(matchup)
        except ValueError as exc:
            raise HTTPException(status_code=422, detail=str(exc))
        query = query.filter(GameWeatherModel.matchup == matchup)
    return query.order_by(
        GameWeatherModel.season, GameWeatherModel.week, GameWeatherModel.matchup
    ).all()
