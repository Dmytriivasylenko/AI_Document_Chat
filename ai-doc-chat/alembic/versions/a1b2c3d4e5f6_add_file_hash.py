"""add file_hash to documents

Revision ID: a1b2c3d4e5f6
Revises: be10b2c97b8d
Create Date: 2026-01-01 00:00:00.000000

Adds SHA-256 hash column for PDF deduplication.
Existing rows get NULL (acceptable — they will be re-hashed on next upload).
"""
from alembic import op
import sqlalchemy as sa

revision = "a1b2c3d4e5f6"
down_revision = "be10b2c97b8d"
branch_labels = None
depends_on = None


def upgrade() -> None:
    # Nullable so existing rows are unaffected
    op.add_column(
        "documents",
        sa.Column("file_hash", sa.String(64), nullable=True),
    )
    op.create_index(
        "ix_documents_file_hash",
        "documents",
        ["file_hash"],
    )


def downgrade() -> None:
    op.drop_index("ix_documents_file_hash", table_name="documents")
    op.drop_column("documents", "file_hash")
