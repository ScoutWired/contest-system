"""Setup wizard tests."""
import re
import pytest
from tests.helpers import make_discord, login_as
from app.models import AppSetting, RoleConfig, User

def wtoken_of(html):
    m = re.search(r'name="wtoken" value="([^"]+)"', html)
    assert m, "wizard token missing in form"
    return m.group(1)

@pytest.fixture()
def setup_open(client, db):
    """Ensure setup is incomplete during test, restore afterwards."""
    prev = db.get(AppSetting, "setup_complete")
    prev_v = prev.value if prev else None
    r = db.get(AppSetting, "setup_complete")
    if r:
        r.value = "0"
    else:
        db.add(AppSetting(key="setup_complete", value="0"))
    db.commit()
    yield
    r = db.get(AppSetting, "setup_complete")
    if prev_v is None:
        if r:
            db.delete(r)
    else:
        r.value = prev_v
    db.commit()
    client.cookies.clear()

def test_wizard_welcome_anonymous(client, db, setup_open):
    client.cookies.clear()
    r = client.get("/setup")
    assert r.status_code == 200
    assert "discord.com/developers/applications" in r.text
    assert "make.powerautomate.com" in r.text

def test_wizard_site_saves(client, db, setup_open):
    client.cookies.clear()
    t = wtoken_of(client.get("/setup?step=site").text)
    r = client.post("/setup/site", follow_redirects=False,
                    data={"wtoken": t, "site_name": "WizTest", "app_url": "https://wiz.example", "timezone": "UTC"})
    assert r.status_code == 303
    assert db.get(AppSetting, "site_name").value == "WizTest"
    # bad URL rejected
    t = wtoken_of(client.get("/setup?step=site").text)
    r = client.post("/setup/site", data={"wtoken": t, "site_name": "X", "app_url": "notaurl"})
    assert r.status_code == 400

def test_wizard_token_single_use(client, db, setup_open):
    client.cookies.clear()
    t = wtoken_of(client.get("/setup?step=site").text)
    client.post("/setup/site", follow_redirects=False,
                data={"wtoken": t, "site_name": "Once", "app_url": "https://x.example"})
    r = client.post("/setup/site", data={"wtoken": t, "site_name": "Twice", "app_url": "https://y.example"})
    assert r.status_code == 403

def test_wizard_roles_validation(client, db, setup_open):
    client.cookies.clear()
    t = wtoken_of(client.get("/setup?step=roles").text)
    r = client.post("/setup/roles", data={"wtoken": t, "roles_moderator": "not-a-role"})
    assert r.status_code == 400
    t = wtoken_of(client.get("/setup?step=roles").text)
    r = client.post("/setup/roles", follow_redirects=False, data={"wtoken": t, "roles_moderator": "123456789012345678"})
    assert r.status_code == 303
    assert db.get(RoleConfig, "moderator").role_ids == "123456789012345678"

def test_wizard_secrets_never_persisted(client, db, setup_open):
    client.cookies.clear()
    t = wtoken_of(client.get("/setup?step=discord_server").text)
    r = client.post("/setup/discord_server", data={"wtoken": t, "guild_id": "123456789012345678", "bot_token": "SUPERSECRET-bot-token-xyz"})
    assert r.status_code == 200
    assert "DISCORD_GUILD_ID=123456789012345678" in r.text  # snippet shown
    for row in db.query(AppSetting).all():
        assert "SUPERSECRET" not in (row.value or "")
    for rc in db.query(RoleConfig).all():
        assert "SUPERSECRET" not in (rc.role_ids or "")

def test_wizard_finish_locks_and_admin_rerun(client, db, setup_open):
    admin = make_discord(db, "999001", roles="", bootstrap=True)
    client.cookies.clear()
    t = wtoken_of(client.get("/setup?step=finish").text)
    r = client.post("/setup/finish", follow_redirects=False, data={"wtoken": t})
    assert r.status_code == 303
    assert db.get(AppSetting, "setup_complete").value == "1"
    # anonymous now locked out
    client.cookies.clear()
    r = client.get("/setup", follow_redirects=False)
    assert r.status_code == 303
    # admin can still re-run
    login_as(client, db, admin)
    r = client.get("/setup?step=roles")
    assert r.status_code == 200 and "re-run as admin" in r.text
    client.cookies.clear()

def test_wizard_first_admin_registers(client, db, setup_open):
    client.cookies.clear()
    t = wtoken_of(client.get("/setup?step=first_admin").text)
    r = client.post("/setup/first_admin", data={"wtoken": t, "discord_id": "424242424242424242"})
    assert r.status_code == 200 and "424242424242424242" in r.text
    u = db.query(User).filter_by(discord_id="424242424242424242").one()
    assert u.is_admin_bootstrap is True
    db.delete(u); db.commit()
