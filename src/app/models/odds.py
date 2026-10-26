from sqlalchemy import Column, DateTime, Float, Integer, String, func

from app.db import Base


class Odds(Base):
    """Canonical odds record for normalized sportsbook data."""

    __tablename__ = "odds"

    id = Column(Integer, primary_key=True, index=True)
    sportsbook = Column(String, nullable=False)
    sport = Column(String, nullable=False, index=True)
    league = Column(String, nullable=False)
    market_type = Column(String, nullable=False)
    market_description = Column(String, nullable=False)
    outcome = Column(String, nullable=False)
    price_decimal = Column(Float, nullable=False)
    event_timestamp = Column(DateTime(timezone=True), nullable=False)
    team_home = Column(String, nullable=False)
    team_away = Column(String, nullable=False)
    external_event_id = Column(String, nullable=True, index=True)
    created_at = Column(DateTime(timezone=True), server_default=func.now())
