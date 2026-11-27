"""#119: PAT access scope column and the administrator membership audit read function (additive)."""
from pathlib import Path
from alembic import op

revision = '0011_ui_feedback'
down_revision = '0010_research_wiki'
branch_labels = None
depends_on = None


def upgrade():
    ddl = (Path(__file__).resolve().parents[2] / 'db/research_0011_ui_feedback.sql').read_text()
    op.get_bind().connection.driver_connection.execute(ddl, prepare=False)


def downgrade():
    # Rollback is a forward-fix with a 0011-compatible image that enforces credentials.access; an 0010 image
    # does not serve an 0011 database (exact research revision), so read-only tokens can never become writers.
    raise RuntimeError('Retain PAT access scopes and the audit function; use a compatible application image')
