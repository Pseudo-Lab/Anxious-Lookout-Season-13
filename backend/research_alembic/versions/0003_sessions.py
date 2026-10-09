"""Personal conversation metadata/cache; original Codex records stay in runner home."""
from pathlib import Path
from alembic import op

revision = "0003_sessions"
down_revision = "0002_research"
branch_labels = None
depends_on = None


def upgrade():
    ddl = (Path(__file__).resolve().parents[2] / "db" / "003_sessions.sql").read_text()
    op.get_bind().connection.driver_connection.execute(ddl, prepare=False)


def downgrade():
    raise RuntimeError("Personal conversations are retained during application rollback")
