"""Preserve Markdown privately and convert all retained post content atomically."""
from pathlib import Path
from alembic import op
revision='0007_post_blocks'
down_revision='0006_posts'
branch_labels=None
depends_on=None


def upgrade():
    connection=op.get_bind()
    ddl=(Path(__file__).resolve().parents[2]/'db/007_post_blocks.sql').read_text()
    connection.connection.driver_connection.execute(ddl,prepare=False)
    from research_alembic.post_blocks_migration import migrate_content
    migrate_content(connection)


def downgrade():
    raise RuntimeError('Block history and preserved Markdown must not be downgraded')
