"""initial schema — all tables"""
revision = "0001"
down_revision = None
from app.database import Base
from app import models  # noqa

def upgrade():
    bind = __import__("alembic").op.get_bind()
    Base.metadata.create_all(bind=bind)

def downgrade():
    bind = __import__("alembic").op.get_bind()
    Base.metadata.drop_all(bind=bind)
