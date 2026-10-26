from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session
from app.db import get_db
from app.models.team import Team as TeamModel
from app.schemas.team import TeamCreate, Team

router = APIRouter()

@router.post("/", response_model=Team)
def create_team(team: TeamCreate, db: Session = Depends(get_db)):
    """Create a normalized team entry that feeds the Postgres data layer."""

    db_team = TeamModel(**team.model_dump())
    db.add(db_team)
    try:
        db.commit()
    except IntegrityError:
        db.rollback()
        raise HTTPException(status_code=409, detail=f"team {team.name!r} already exists")
    db.refresh(db_team)
    return db_team

@router.get("/", response_model=list[Team])
def list_teams(db: Session = Depends(get_db)):
    """Return all teams from the canonical table so ingestion and agents can reference them."""

    return db.query(TeamModel).all()
