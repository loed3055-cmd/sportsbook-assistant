"""Predictions (gold layer): record ATS picks per source and score them side by side."""

from collections import defaultdict

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy.orm import Session

from app.db import get_db
from app.models.game_result import GameResult as GameResultModel
from app.models.prediction import Prediction as PredictionModel
from app.schemas.prediction import (
    GameResult,
    GameResultBulkCreate,
    Prediction,
    PredictionBulkCreate,
    PredictionBulkResponse,
    SourceStanding,
    StandingsResponse,
    WeekRecord,
)
from app.services.grading import PickResult, ats_winner, grade_pick, parse_matchup

router = APIRouter()

GameKey = tuple[int, int, str]  # (season, week, matchup)


def _reject_duplicate_keys(keys: list[tuple]) -> None:
    seen: set[tuple] = set()
    for key in keys:
        if key in seen:
            raise HTTPException(status_code=422, detail=f"duplicate entry in request: {key}")
        seen.add(key)


def _results_by_game(db: Session, predictions: list[PredictionModel]) -> dict[GameKey, GameResultModel]:
    if not predictions:
        return {}
    seasons = {p.season for p in predictions}
    weeks = {p.week for p in predictions}
    rows = (
        db.query(GameResultModel)
        .filter(GameResultModel.season.in_(seasons), GameResultModel.week.in_(weeks))
        .all()
    )
    return {(r.season, r.week, r.matchup): r for r in rows}


def _grade(prediction: PredictionModel, result: GameResultModel | None) -> tuple[str | None, PickResult | None]:
    if result is None:
        return None, None
    winner = ats_winner(
        away_team=prediction.away_team,
        home_team=prediction.home_team,
        favorite=prediction.favorite,
        spread=prediction.spread,
        away_score=result.away_score,
        home_score=result.home_score,
    )
    return winner, grade_pick(prediction.pick, winner)


def _to_schema(prediction: PredictionModel, results: dict[GameKey, GameResultModel]) -> Prediction:
    winner, outcome = _grade(
        prediction, results.get((prediction.season, prediction.week, prediction.matchup))
    )
    out = Prediction.model_validate(prediction)
    out.ats_winner = winner
    out.result = outcome.value if outcome else None
    return out


@router.post("/", response_model=PredictionBulkResponse)
def upsert_predictions(payload: PredictionBulkCreate, db: Session = Depends(get_db)):
    """Record picks in bulk. Re-posting the same (season, week, matchup, source) replaces the pick."""

    _reject_duplicate_keys([(p.season, p.week, p.matchup, p.source) for p in payload.predictions])

    created = updated = 0
    rows: list[PredictionModel] = []
    for item in payload.predictions:
        away, home = parse_matchup(item.matchup)
        values = item.model_dump()
        values.update(away_team=away, home_team=home)
        row = (
            db.query(PredictionModel)
            .filter_by(season=item.season, week=item.week, matchup=item.matchup, source=item.source)
            .one_or_none()
        )
        if row is None:
            row = PredictionModel(**values)
            db.add(row)
            created += 1
        else:
            for field, value in values.items():
                setattr(row, field, value)
            updated += 1
        rows.append(row)

    db.commit()
    for row in rows:
        db.refresh(row)
    results = _results_by_game(db, rows)
    return PredictionBulkResponse(
        created=created,
        updated=updated,
        predictions=[_to_schema(row, results) for row in rows],
    )


@router.get("/", response_model=list[Prediction])
def list_predictions(
    season: int | None = None,
    week: int | None = None,
    source: str | None = None,
    db: Session = Depends(get_db),
):
    """List picks, graded against final scores when available."""

    query = db.query(PredictionModel)
    if season is not None:
        query = query.filter(PredictionModel.season == season)
    if week is not None:
        query = query.filter(PredictionModel.week == week)
    if source is not None:
        query = query.filter(PredictionModel.source == source.strip().lower())
    rows = query.order_by(
        PredictionModel.season, PredictionModel.week, PredictionModel.matchup, PredictionModel.source
    ).all()
    results = _results_by_game(db, rows)
    return [_to_schema(row, results) for row in rows]


@router.post("/results", response_model=list[GameResult])
def upsert_results(payload: GameResultBulkCreate, db: Session = Depends(get_db)):
    """Record final scores in bulk (upsert on season/week/matchup)."""

    _reject_duplicate_keys([(r.season, r.week, r.matchup) for r in payload.results])

    rows: list[GameResultModel] = []
    for item in payload.results:
        away, home = parse_matchup(item.matchup)
        row = (
            db.query(GameResultModel)
            .filter_by(season=item.season, week=item.week, matchup=item.matchup)
            .one_or_none()
        )
        if row is None:
            row = GameResultModel(away_team=away, home_team=home, **item.model_dump())
            db.add(row)
        else:
            row.away_score, row.home_score = item.away_score, item.home_score
        rows.append(row)

    db.commit()
    for row in rows:
        db.refresh(row)
    return rows


@router.get("/results", response_model=list[GameResult])
def list_results(season: int | None = None, week: int | None = None, db: Session = Depends(get_db)):
    query = db.query(GameResultModel)
    if season is not None:
        query = query.filter(GameResultModel.season == season)
    if week is not None:
        query = query.filter(GameResultModel.week == week)
    return query.order_by(GameResultModel.season, GameResultModel.week, GameResultModel.matchup).all()


_TALLY_FIELD = {PickResult.WIN: "wins", PickResult.LOSS: "losses", PickResult.PUSH: "pushes"}


@router.get("/standings", response_model=StandingsResponse)
def standings(
    season: int = Query(...),
    week: int | None = None,
    source: str | None = None,
    db: Session = Depends(get_db),
):
    """ATS W-L-P per source for a season (optionally one week), with a per-week breakdown.

    Picks on games without a final score count as ``pending``.
    """

    rows = list_predictions(season=season, week=week, source=source, db=db)

    per_week: dict[str, dict[int, WeekRecord]] = defaultdict(dict)
    for p in rows:
        record = per_week[p.source].setdefault(p.week, WeekRecord(week=p.week))
        field = _TALLY_FIELD[PickResult(p.result)] if p.result else "pending"
        setattr(record, field, getattr(record, field) + 1)

    table: list[SourceStanding] = []
    for src, weeks in per_week.items():
        ordered = [weeks[w] for w in sorted(weeks)]
        table.append(
            SourceStanding(
                source=src,
                wins=sum(w.wins for w in ordered),
                losses=sum(w.losses for w in ordered),
                pushes=sum(w.pushes for w in ordered),
                pending=sum(w.pending for w in ordered),
                weeks=ordered,
            )
        )
    table.sort(key=lambda s: (-s.wins, s.losses, s.source))
    return StandingsResponse(season=season, week=week, standings=table)
