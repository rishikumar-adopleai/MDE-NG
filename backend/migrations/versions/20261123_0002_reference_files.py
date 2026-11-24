"""Store source reference files in the workspace database."""

from alembic import op

from app.models import ReferenceFile

revision = "20261123_0002"
down_revision = "20261119_0001"
branch_labels = None
depends_on = None


def upgrade() -> None:
    ReferenceFile.__table__.create(op.get_bind(), checkfirst=True)


def downgrade() -> None:
    ReferenceFile.__table__.drop(op.get_bind(), checkfirst=True)
