from pydantic import BaseModel

class TeamBase(BaseModel):
    sport: str
    league: str
    name: str
    short_name: str | None = None
    external_ref: str | None = None

class TeamCreate(TeamBase):
    pass

class Team(TeamBase):
    id: int

    class Config:
        orm_mode = True
