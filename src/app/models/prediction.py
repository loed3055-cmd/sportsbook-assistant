from sqlalchemy import (
    JSON,
    Column,
    DateTime,
    Float,
    Integer,
    String,
    Text,
    UniqueConstraint,
    func,
)

from app.db import Base


class Prediction(Base):
    """Gold-layer record of one source's against-the-spread pick for one game.

    A "source" is whoever made the pick: a human ("edwin"), a chat assistant
    ("claude-chat"), or a future pipeline ("rag-v1"). Each prediction carries
    the line it was made against, so sources are graded on their own spread.
    """

    __tablename__ = "predictions"
    __table_args__ = (
        UniqueConstraint(
            "season", "week", "matchup", "source", name="uq_predictions_game_source"
        ),
    )

    id = Column(Integer, primary_key=True, index=True)
    season = Column(Integer, nullable=False, index=True)
    week = Column(Integer, nullable=False, index=True)
    matchup = Column(String, nullable=False)  # canonical "AWAY@HOME", e.g. "ATL@GB"
    away_team = Column(String, nullable=False)
    home_team = Column(String, nullable=False)
    favorite = Column(String, nullable=False)
    spread = Column(Float, nullable=False)  # favorite's line, <= 0 (e.g. -5.5)
    spread_as_of = Column(DateTime(timezone=True), nullable=False)
    source = Column(String, nullable=False, index=True)
    pick = Column(String, nullable=False)
    confidence = Column(String, nullable=True)  # low / medium / high
    reasoning = Column(Text, nullable=True)
    retrieved_doc_ids = Column(JSON, nullable=True)  # future RAG citations
    created_at = Column(DateTime(timezone=True), server_default=func.now())
