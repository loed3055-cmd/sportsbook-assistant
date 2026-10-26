from sqlalchemy import Column, DateTime, Integer, String, func

from app.db import Base


class Job(Base):
    """Tracks every ingestion job for auditability and retries."""

    __tablename__ = "jobs"

    id = Column(Integer, primary_key=True, index=True)
    sport = Column(String, nullable=False)
    source = Column(String, nullable=False)
    status = Column(String, nullable=False, default="queued")
    started_at = Column(DateTime(timezone=True), nullable=True)
    completed_at = Column(DateTime(timezone=True), nullable=True)
    records_ingested = Column(Integer, nullable=False, default=0)
    message = Column(String, nullable=True)
    created_at = Column(DateTime(timezone=True), server_default=func.now())
