"""Apply alembic migrations (used at container startup)."""
import os
from alembic.config import Config
from alembic import command
cfg = Config("alembic.ini")
command.upgrade(cfg, "head")
print("migrations applied")
