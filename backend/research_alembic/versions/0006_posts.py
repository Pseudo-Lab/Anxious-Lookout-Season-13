"""Post storage and retained writing history, separate from research items."""
from pathlib import Path
from alembic import op

revision = '0006_posts'
down_revision = '0005_writing'
branch_labels = None
depends_on = None


def upgrade():
    ddl = (Path(__file__).resolve().parents[2] / 'db/006_posts.sql').read_text()
    op.get_bind().connection.driver_connection.execute(ddl, prepare=False)


def downgrade():
    raise RuntimeError('Post writing history must be retained during rollback')
