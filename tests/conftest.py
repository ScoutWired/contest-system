import os, sys
os.environ.setdefault("SECRET_KEY", "test-secret-0123456789abcdef-test")
os.environ["DATABASE_URL"] = "sqlite:///./data/test.db"
sys.path.insert(0, ".")
import pytest
from fastapi.testclient import TestClient

@pytest.fixture(scope="session")
def client():
    # fresh db
    if os.path.exists("./data/test.db"):
        os.remove("./data/test.db")
    os.environ["DATABASE_URL"] = "sqlite:///./data/test.db"
    # re-create engine-bound tables via models metadata on test engine
    import app.database as dbutil
    from app import models  # noqa
    dbutil.Base.metadata.create_all(bind=dbutil.engine)
    from app.main import app
    from app.permissions import seed_role_configs
    from app.database import SessionLocal
    db = SessionLocal()
    seed_role_configs(db)
    db.close()
    with TestClient(app) as c:
        yield c

@pytest.fixture()
def db():
    from app.database import SessionLocal
    s = SessionLocal()
    yield s
    s.close()
