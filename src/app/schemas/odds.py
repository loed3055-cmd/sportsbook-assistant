from datetime import datetime

from pydantic import BaseModel


class OddsBase(BaseModel):
    sportsbook: str
    sport: str
    league: str
    market_type: str
    market_description: str
    outcome: str
    price_decimal: float
    event_timestamp: datetime
    team_home: str
    team_away: str
    external_event_id: str | None = None


class Odds(OddsBase):
    id: int
    created_at: datetime

    class Config:
        orm_mode = True
