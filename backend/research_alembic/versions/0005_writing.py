"""Separate drafts and user-accepted Codex proposals; retain all prior history."""
from pathlib import Path
from alembic import op

revision = '0005_writing'
down_revision = '0004_publication'
branch_labels = None
depends_on = None


def upgrade():
    ddl = (Path(__file__).resolve().parents[2] / 'db/005_writing.sql').read_text()
    op.get_bind().connection.driver_connection.execute(ddl, prepare=False)


def downgrade():
    raise RuntimeError('Drafts and proposal history must be retained during rollback')
