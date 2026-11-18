"""#105 research wiki: version summary/bibliography and relation evidence."""
from pathlib import Path
from alembic import op

revision = '0010_research_wiki'
down_revision = '0009_research_pats'
branch_labels = None
depends_on = None


def upgrade():
    ddl = (Path(__file__).resolve().parents[2] / 'db/research_0010_wiki.sql').read_text()
    op.get_bind().connection.driver_connection.execute(ddl, prepare=False)


def downgrade():
    raise RuntimeError('Retain summaries, bibliography and relation evidence; use a compatible application image')
