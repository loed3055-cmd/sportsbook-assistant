from sqlalchemy import Column, DateTime, Float, Integer, String, UniqueConstraint, func

from app.db import Base


class GameWeather(Base):
    """Latest kickoff-hour forecast at a game's venue.

    Keyed like GameResult by (season, week, matchup), so it joins to picks and
    results without team foreign keys. Each refresh overwrites the row.
    ``status`` is ok | indoor | out_of_range | error; weather fields are null
    unless status is ok.
    """

    __tablename__ = "game_weather"
    __table_args__ = (
        UniqueConstraint("season", "week", "matchup", name="uq_game_weather_game"),
    )

    id = Column(Integer, primary_key=True, index=True)
    season = Column(Integer, nullable=False, index=True)
    week = Column(Integer, nullable=False, index=True)
    matchup = Column(String, nullable=False)  # canonical "AWAY@HOME"
    kickoff_utc = Column(DateTime(timezone=True), nullable=False)
    venue_name = Column(String, nullable=False)
    city = Column(String, nullable=False)
    roof = Column(String, nullable=False)  # open | retractable | dome
    provider = Column(String, nullable=True)  # nws | open-meteo; null for domes
    status = Column(String, nullable=False)
    temp_f = Column(Float, nullable=True)
    wind_mph = Column(Float, nullable=True)
    gust_mph = Column(Float, nullable=True)
    precip_prob = Column(Float, nullable=True)
    short_forecast = Column(String, nullable=True)
    error = Column(String, nullable=True)  # stable code, not raw exception text
    attribution = Column(String, nullable=True)  # required credit (Open-Meteo, CC BY 4.0)
    fetched_at = Column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now()
    )
