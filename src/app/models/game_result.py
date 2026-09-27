from sqlalchemy import Column, DateTime, Integer, String, UniqueConstraint, func

from app.db import Base


class GameResult(Base):
    """Final score for one NFL game, keyed the same way as predictions.

    Minimal on purpose: just enough to grade picks against the spread. It is
    keyed by (season, week, matchup) rather than by team foreign keys so pick'em
    sheets can be graded without first populating the teams table.
    """

    __tablename__ = "game_results"
    __table_args__ = (
        UniqueConstraint("season", "week", "matchup", name="uq_game_results_game"),
    )

    id = Column(Integer, primary_key=True, index=True)
    season = Column(Integer, nullable=False, index=True)
    week = Column(Integer, nullable=False, index=True)
    matchup = Column(String, nullable=False)  # canonical "AWAY@HOME"
    away_team = Column(String, nullable=False)
    home_team = Column(String, nullable=False)
    away_score = Column(Integer, nullable=False)
    home_score = Column(Integer, nullable=False)
    created_at = Column(DateTime(timezone=True), server_default=func.now())
    updated_at = Column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now()
    )
