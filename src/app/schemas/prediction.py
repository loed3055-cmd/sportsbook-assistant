from datetime import datetime
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from app.services.grading import canonical_matchup, parse_matchup, team_in_matchup

Confidence = Literal["low", "medium", "high"]


class PredictionCreate(BaseModel):
    """One source's ATS pick. Team codes are case-insensitive and alias-normalized."""

    season: int = Field(..., ge=1900, le=2100, examples=[2026])
    week: int = Field(..., ge=1, le=25, examples=[3])
    matchup: str = Field(..., examples=["ATL@GB"], description="AWAY@HOME")
    favorite: str = Field(..., examples=["GB"])
    spread: float = Field(..., le=0, examples=[-5.5], description="Favorite's line (<= 0)")
    spread_as_of: datetime
    source: str = Field(..., min_length=1, examples=["edwin", "claude-chat", "rag-v1"])
    pick: str = Field(..., examples=["ATL"])
    confidence: Confidence | None = None
    reasoning: str | None = None
    retrieved_doc_ids: list[str] | None = None

    @field_validator("matchup")
    @classmethod
    def _matchup(cls, value: str) -> str:
        return canonical_matchup(value)

    @field_validator("source")
    @classmethod
    def _source(cls, value: str) -> str:
        value = value.strip().lower()
        if not value:
            raise ValueError("source must not be blank")
        return value

    @field_validator("confidence", mode="before")
    @classmethod
    def _confidence(cls, value):
        return value.strip().lower() if isinstance(value, str) else value

    @model_validator(mode="after")
    def _teams_in_matchup(self) -> "PredictionCreate":
        away, home = parse_matchup(self.matchup)
        self.favorite = team_in_matchup(self.favorite, away, home)
        self.pick = team_in_matchup(self.pick, away, home)
        return self


class PredictionBulkCreate(BaseModel):
    predictions: list[PredictionCreate] = Field(..., min_length=1)


class Prediction(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    season: int
    week: int
    matchup: str
    away_team: str
    home_team: str
    favorite: str
    spread: float
    spread_as_of: datetime
    source: str
    pick: str
    confidence: Confidence | None = None
    reasoning: str | None = None
    retrieved_doc_ids: list[str] | None = None
    created_at: datetime | None = None
    # Filled in when a final score exists for the game.
    ats_winner: str | None = Field(None, description="Team that covered, or 'PUSH'")
    result: Literal["W", "L", "P"] | None = None


class PredictionBulkResponse(BaseModel):
    created: int
    updated: int
    predictions: list[Prediction]


class GameResultCreate(BaseModel):
    season: int = Field(..., ge=1900, le=2100)
    week: int = Field(..., ge=1, le=25)
    matchup: str = Field(..., examples=["DET@BUF"], description="AWAY@HOME")
    away_score: int = Field(..., ge=0)
    home_score: int = Field(..., ge=0)

    @field_validator("matchup")
    @classmethod
    def _matchup(cls, value: str) -> str:
        return canonical_matchup(value)


class GameResultBulkCreate(BaseModel):
    results: list[GameResultCreate] = Field(..., min_length=1)


class GameResult(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    season: int
    week: int
    matchup: str
    away_team: str
    home_team: str
    away_score: int
    home_score: int


class RecordLine(BaseModel):
    wins: int = 0
    losses: int = 0
    pushes: int = 0
    pending: int = 0


class WeekRecord(RecordLine):
    week: int


class SourceStanding(RecordLine):
    source: str
    weeks: list[WeekRecord]


class StandingsResponse(BaseModel):
    season: int
    week: int | None = None
    standings: list[SourceStanding]
