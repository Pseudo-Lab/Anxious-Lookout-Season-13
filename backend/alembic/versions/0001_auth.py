"""Adopt verified legacy v1 or create empty auth schema without rewriting data."""
from pathlib import Path

from alembic import op

revision = "0001_auth"
down_revision = None
branch_labels = None
depends_on = None


def upgrade():
    # Frozen original DDL: existing validated tables retain rows/defaults/passwords.
    ddl = (Path(__file__).resolve().parents[2] / "db" / "001_auth.sql").read_text()
    op.get_bind().connection.driver_connection.execute(ddl, prepare=False)


def downgrade():
    raise RuntimeError("Destructive auth downgrade is not an application rollback")
