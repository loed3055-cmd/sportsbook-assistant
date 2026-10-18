from pydantic import BaseModel, ConfigDict

class TeamBase(BaseModel):
    sport: str
    league: str
    name: str
    short_name: str | None = None
    external_ref: str | None = None

class TeamCreate(TeamBase):
    pass

class Team(TeamBase):
    model_config = ConfigDict(from_attributes=True)

    id: int
