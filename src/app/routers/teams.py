from fastapi import APIRouter, Depends
from sqlalchemy.orm import Session
from app.db import get_db
from app.models.team import Team as TeamModel
from app.schemas.team import TeamCreate, Team

router = APIRouter()

@router.post("/", response_model=Team)
def create_team(team: TeamCreate, db: Session = Depends(get_db)):
    db_team = TeamModel(**team.dict())
    db.add(db_team)
    db.commit()
    db.refresh(db_team)
    return db_team

@router.get("/", response_model=list[Team])
def list_teams(db: Session = Depends(get_db)):
    return db.query(TeamModel).all()
