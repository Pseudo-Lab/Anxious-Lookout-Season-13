"""Add server-assigned version provenance; preserve all prior content and proposals."""
from pathlib import Path
from alembic import op

revision = '0008_version_authors'
down_revision = '0007_post_blocks'
branch_labels = None
depends_on = None


def upgrade():
    ddl = (Path(__file__).resolve().parents[2] / 'db/008_version_authors.sql').read_text()
    op.get_bind().connection.driver_connection.execute(ddl, prepare=False)


def downgrade():
    raise RuntimeError('Retain version provenance and content; use a schema-compatible application image')
