"""Independent purpose-separated PAT principals and immutable version attribution."""
from pathlib import Path
from alembic import op

revision = '0009_research_pats'
down_revision = '0008_version_authors'
branch_labels = None
depends_on = None


def upgrade():
    ddl = (Path(__file__).resolve().parents[2] / 'db/009_research_pats.sql').read_text()
    op.get_bind().connection.driver_connection.execute(ddl, prepare=False)


def downgrade():
    raise RuntimeError('Retain credentials, audit and version history; use a compatible application image')
