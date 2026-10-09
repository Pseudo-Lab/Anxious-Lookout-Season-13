"""Immutable current-only releases and narrow audited administrator membership."""
from pathlib import Path
from alembic import op

revision = "0004_publication"
down_revision = "0003_sessions"
branch_labels = None
depends_on = None


def upgrade():
    ddl = (Path(__file__).resolve().parents[2] / "db" / "004_publication.sql").read_text()
    op.get_bind().connection.driver_connection.execute(ddl, prepare=False)


def downgrade():
    raise RuntimeError("Publication/private history is retained during application rollback")
