from sqlalchemy import Column, Integer, String
from app.db import Base

class Team(Base):
    __tablename__ = "teams"

    id = Column(Integer, primary_key=True, index=True)
    sport = Column(String, nullable=False)
    league = Column(String, nullable=False)
    name = Column(String, nullable=False, unique=True)
    short_name = Column(String)
    external_ref = Column(String, nullable=True)
