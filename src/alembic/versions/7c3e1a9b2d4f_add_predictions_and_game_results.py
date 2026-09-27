"""add predictions and game_results

Revision ID: 7c3e1a9b2d4f
Revises:
Create Date: 2026-09-24

"""
from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

# revision identifiers, used by Alembic.
revision: str = "7c3e1a9b2d4f"
down_revision: str | Sequence[str] | None = None
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "predictions",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("season", sa.Integer(), nullable=False),
        sa.Column("week", sa.Integer(), nullable=False),
        sa.Column("matchup", sa.String(), nullable=False),
        sa.Column("away_team", sa.String(), nullable=False),
        sa.Column("home_team", sa.String(), nullable=False),
        sa.Column("favorite", sa.String(), nullable=False),
        sa.Column("spread", sa.Float(), nullable=False),
        sa.Column("spread_as_of", sa.DateTime(timezone=True), nullable=False),
        sa.Column("source", sa.String(), nullable=False),
        sa.Column("pick", sa.String(), nullable=False),
        sa.Column("confidence", sa.String(), nullable=True),
        sa.Column("reasoning", sa.Text(), nullable=True),
        sa.Column("retrieved_doc_ids", sa.JSON(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=True),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("season", "week", "matchup", "source", name="uq_predictions_game_source"),
    )
    op.create_index(op.f("ix_predictions_id"), "predictions", ["id"], unique=False)
    op.create_index(op.f("ix_predictions_season"), "predictions", ["season"], unique=False)
    op.create_index(op.f("ix_predictions_week"), "predictions", ["week"], unique=False)
    op.create_index(op.f("ix_predictions_source"), "predictions", ["source"], unique=False)

    op.create_table(
        "game_results",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("season", sa.Integer(), nullable=False),
        sa.Column("week", sa.Integer(), nullable=False),
        sa.Column("matchup", sa.String(), nullable=False),
        sa.Column("away_team", sa.String(), nullable=False),
        sa.Column("home_team", sa.String(), nullable=False),
        sa.Column("away_score", sa.Integer(), nullable=False),
        sa.Column("home_score", sa.Integer(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=True),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=True),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("season", "week", "matchup", name="uq_game_results_game"),
    )
    op.create_index(op.f("ix_game_results_id"), "game_results", ["id"], unique=False)
    op.create_index(op.f("ix_game_results_season"), "game_results", ["season"], unique=False)
    op.create_index(op.f("ix_game_results_week"), "game_results", ["week"], unique=False)


def downgrade() -> None:
    op.drop_index(op.f("ix_game_results_week"), table_name="game_results")
    op.drop_index(op.f("ix_game_results_season"), table_name="game_results")
    op.drop_index(op.f("ix_game_results_id"), table_name="game_results")
    op.drop_table("game_results")
    op.drop_index(op.f("ix_predictions_source"), table_name="predictions")
    op.drop_index(op.f("ix_predictions_week"), table_name="predictions")
    op.drop_index(op.f("ix_predictions_season"), table_name="predictions")
    op.drop_index(op.f("ix_predictions_id"), table_name="predictions")
    op.drop_table("predictions")
