from datetime import datetime, timezone
from typing import Literal

from pydantic import AwareDatetime, BaseModel, ConfigDict, Field, field_validator, model_validator

from app.services.grading import canonical_matchup
from app.services.stadiums import venue_for


class WeatherGame(BaseModel):
    matchup: str = Field(..., examples=["MIA@BUF"], description="AWAY@HOME")
    kickoff_utc: AwareDatetime = Field(..., examples=["2026-10-04T17:00:00Z"])
    venue: str | None = Field(
        None,
        examples=["tottenham"],
        description="Neutral-site slug for international games; omit for the home stadium",
    )

    @field_validator("matchup")
    @classmethod
    def _matchup(cls, value: str) -> str:
        return canonical_matchup(value)

    @model_validator(mode="after")
    def _known_venue(self) -> "WeatherGame":
        venue_for(self.matchup, self.venue)  # 422 on an unknown team or venue
        return self


class WeatherRefresh(BaseModel):
    season: int = Field(..., ge=1900, le=2100, examples=[2026])
    week: int = Field(..., ge=1, le=25, examples=[5])
    # One NFL week is at most 16 games; the cap bounds outbound calls per request.
    games: list[WeatherGame] = Field(..., min_length=1, max_length=20)


class GameWeather(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    season: int
    week: int
    matchup: str
    kickoff_utc: datetime
    venue_name: str
    city: str
    roof: Literal["open", "retractable", "dome"]
    provider: Literal["nws", "open-meteo"] | None = None
    status: Literal["ok", "indoor", "out_of_range", "error"]
    temp_f: float | None = None
    wind_mph: float | None = None
    gust_mph: float | None = None
    precip_prob: float | None = Field(None, description="Chance of precipitation, percent")
    short_forecast: str | None = None
    error: str | None = Field(None, description="Error code when status is 'error'")
    attribution: str | None = Field(None, description="Credit to show with this data")
    fetched_at: datetime | None = None

    @field_validator("kickoff_utc", "fetched_at")
    @classmethod
    def _as_utc(cls, value: datetime | None) -> datetime | None:
        # SQLite returns naive UTC; Postgres returns the session timezone.
        if value is None:
            return None
        if value.tzinfo is None:
            return value.replace(tzinfo=timezone.utc)
        return value.astimezone(timezone.utc)
