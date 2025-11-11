from fastapi import FastAPI
from app.routers import teams
from app.db import Base, engine

# Create tables automatically for now (later use Alembic)
Base.metadata.create_all(bind=engine)

app = FastAPI(title="Sportsbook Assistant MVP")

app.include_router(teams.router, prefix="/teams", tags=["teams"])

@app.get("/")
def root():
    return {"message": "Sportsbook Assistant API is live 🚀"}
