"""Add research after verified M2, keeping M2 rollback compatibility."""
from pathlib import Path

from alembic import op

revision = "0002_research"
down_revision = None
branch_labels = None
depends_on = None


def upgrade():
    # Schema must precede the Alembic version table. The runner creates only
    # the namespace; this frozen DDL creates the reviewed data tables.
    ddl = (Path(__file__).resolve().parents[2] / "db" / "002_research.sql").read_text()
    op.get_bind().connection.driver_connection.execute(ddl.replace("CREATE SCHEMA research;", ""), prepare=False)


def downgrade():
    raise RuntimeError("Research data is retained during application rollback")
