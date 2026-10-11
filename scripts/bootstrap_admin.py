"""Secure first-admin bootstrap: python scripts/bootstrap_admin.py --discord-id 123 [--token $BOOTSTRAP_TOKEN]"""
import argparse, os, sys
sys.path.insert(0, ".")
p = argparse.ArgumentParser()
p.add_argument("--discord-id", required=True)
p.add_argument("--token", default=os.environ.get("BOOTSTRAP_TOKEN", ""))
a = p.parse_args()
expected = os.environ.get("BOOTSTRAP_TOKEN", "")
if expected and a.token != expected:
    print("invalid bootstrap token"); sys.exit(1)
from app.database import SessionLocal
from app.models import User
db = SessionLocal()
u = db.query(User).filter_by(discord_id=a.discord_id).first()
if not u:
    u = User(kind="discord", display_name=f"admin-{a.discord_id}", discord_id=a.discord_id, is_admin_bootstrap=True)
    db.add(u)
else:
    u.is_admin_bootstrap = True
db.commit()
print(f"bootstrap admin set for discord id {a.discord_id}; unset BOOTSTRAP_TOKEN / clear flag after use")
