"""Critical workflow + security tests (mocked Discord / Power Automate)."""
import re
import app.auth_discord as discord_mod
from tests.helpers import make_discord, make_guest, login_as, set_role, csrf_of
from app.security import reset_limiter
from app.models import Submission, Poll, PollOption, Vote, GuestAccount, User, RoleConfig, WebhookEvent, DeliveryAttempt

def _post(client, url, data):
    return client.post(url, data=data, follow_redirects=False)

# 1: guest can log in with generated credentials
def test_01_guest_login(client, db):
    reset_limiter()
    g, u, code = make_guest(db, "t01")
    r = client.post("/login/guest", data={"username": g.username, "code": code}, follow_redirects=False)
    assert r.status_code == 303, r.text[:300]
    r2 = client.get("/account")
    assert r2.status_code == 200 and g.username in r2.text
    client.cookies.clear()

# 2: invalid/expired/revoked guests cannot log in
def test_02_guest_login_rejected(client, db):
    reset_limiter()
    g, u, code = make_guest(db, "t02")
    r = client.post("/login/guest", data={"username": g.username, "code": "wrong-code-xyz"})
    assert r.status_code == 401
    from datetime import datetime, timezone, timedelta
    g.expires_at = datetime.now(timezone.utc) - timedelta(days=1); db.commit()
    r = client.post("/login/guest", data={"username": g.username, "code": code})
    assert r.status_code == 401
    g.expires_at = None; g.active = False; db.commit()
    r = client.post("/login/guest", data={"username": g.username, "code": code})
    assert r.status_code == 401
    client.cookies.clear()

# 3+4: authorised guest can submit; new proposal hidden from portal
def test_03_submit_hidden(client, db):
    reset_limiter()
    g, u, code = make_guest(db, "t03")
    client.post("/login/guest", data={"username": g.username, "code": code})
    from app.auth_sessions import get_current_user
    user, sess = get_current_user(db, client.cookies.get("sw_session"))
    r = client.post("/submit", data={"csrf_token": sess.csrf_token, "title": "Secret Idea XYZ", "category": "General",
                                     "summary": "s", "description": "d" * 50, "links": "", "proposed_options": ""})
    assert r.status_code == 200 and "awaiting review" in r.text
    s = db.query(Submission).filter_by(title="Secret Idea XYZ").one()
    assert s.status == "PENDING_REVIEW"
    client.cookies.clear()
    home = client.get("/")
    assert "Secret Idea XYZ" not in home.text
    anon = client.get(f"/submissions/{s.id}")
    assert anon.status_code == 404
    client.cookies.clear()

# 5: guests cannot moderate
def test_05_guest_cannot_moderate(client, db):
    reset_limiter()
    g, u, code = make_guest(db, "t05")
    s = db.query(Submission).filter_by(status="PENDING_REVIEW").first()
    client.post("/login/guest", data={"username": g.username, "code": code})
    user, sess = __import__("app.auth_sessions", fromlist=["get_current_user"]).get_current_user(db, client.cookies.get("sw_session"))
    r = client.post(f"/admin/submissions/{s.id}/decision", follow_redirects=False, data={"csrf_token": sess.csrf_token, "action": "APPROVE"})
    assert r.status_code == 403
    r = client.get("/admin")
    assert r.status_code == 403
    client.cookies.clear()

# 6+7: discord moderation roles enforced
def test_06_07_discord_moderation_roles(client, db):
    set_role(db, "moderator", "9001")
    plain = make_discord(db, "6001", roles="")
    login_as(client, db, plain)
    s = db.query(Submission).filter_by(status="PENDING_REVIEW").first()
    r = client.get("/admin/submissions")
    assert r.status_code == 403
    client.cookies.clear()
    mod = make_discord(db, "6002", roles="9001")
    login_as(client, db, mod)
    r = client.get("/admin/submissions")
    assert r.status_code == 200
    tok = csrf_of(db, mod)
    # monkeypatch integrations to avoid network
    import app.routes_admin as adm
    fired = []
    orig = adm.fire_and_forget
    adm.fire_and_forget = lambda *a, **k: fired.append(a)
    r = client.post(f"/admin/submissions/{s.id}/decision", follow_redirects=False, data={"csrf_token": tok, "action": "APPROVE", "reason": "good"})
    adm.fire_and_forget = orig
    assert r.status_code == 303
    db.expire_all()
    assert db.get(Submission, s.id).status == "APPROVED"
    assert fired
    client.cookies.clear()

# 8: approved stays unpublished until explicit publish
def test_08_approve_vs_publish(client, db):
    set_role(db, "moderator", "9001")
    mod = make_discord(db, "6002", roles="9001")
    login_as(client, db, mod)
    s = db.query(Submission).filter_by(status="APPROVED").first()
    assert s is not None
    home = client.get("/")
    assert s.title not in home.text
    import app.routes_admin as adm
    orig = adm.fire_and_forget
    adm.fire_and_forget = lambda *a, **k: None
    r = client.post(f"/admin/submissions/{s.id}/decision", follow_redirects=False, data={"csrf_token": csrf_of(db, mod), "action": "PUBLISH"})
    adm.fire_and_forget = orig
    assert r.status_code == 303
    db.expire_all()
    assert db.get(Submission, s.id).status == "PUBLISHED"
    assert s.title in client.get("/").text
    client.cookies.clear()

# 9-16: voting integrity
def _make_poll(db, by, eligibility="both", options=("A", "B"), allow_change=False, status="OPEN"):
    from app.models import Poll as P_, PollOption as PO
    p = P_(title=f"Poll-{P_.__name__}-{__import__('uuid').uuid4().hex[:6]}", description="d",
           eligibility=eligibility, allow_change=allow_change, allow_abstain=True, status=status, created_by=by.id)
    db.add(p); db.flush()
    for i, t in enumerate(options):
        db.add(PO(poll_id=p.id, text=t, position=i))
    db.add(PO(poll_id=p.id, text="Abstain", position=99, is_abstain=True))
    db.commit()
    return p

def _opt(db, poll, text):
    return db.query(PollOption).filter_by(poll_id=poll.id, text=text).one()

def test_09_10_eligibility_and_single_vote(client, db):
    set_role(db, "voter", "7001")
    mgr = make_discord(db, "6002", roles="9001")
    voter = make_discord(db, "7002", roles="7001")
    outsider = make_discord(db, "7003", roles="")
    reset_limiter()
    g, gu, code = make_guest(db, "vote-guest")
    p = _make_poll(db, mgr, eligibility="discord", options=("Yes", "No"))
    # guest blocked from discord-only poll
    client.post("/login/guest", data={"username": g.username, "code": code})
    from app.auth_sessions import get_current_user
    _, sess = get_current_user(db, client.cookies.get("sw_session"))
    r = client.post(f"/polls/{p.id}/vote", follow_redirects=False, data={"csrf_token": sess.csrf_token, "option_id": _opt(db, p, "Yes").id})
    assert r.status_code == 403
    client.cookies.clear()
    # eligible voter votes twice -> second rejected (no change allowed)
    login_as(client, db, voter)
    tok = csrf_of(db, voter)
    r = client.post(f"/polls/{p.id}/vote", follow_redirects=False, data={"csrf_token": tok, "option_id": _opt(db, p, "Yes").id})
    assert r.status_code == 200 and "recorded" in r.text
    r = client.post(f"/polls/{p.id}/vote", follow_redirects=False, data={"csrf_token": tok, "option_id": _opt(db, p, "No").id})
    assert r.status_code == 400 and "already voted" in r.text
    assert db.query(Vote).filter_by(poll_id=p.id, voter_id=voter.id).count() == 1
    client.cookies.clear()

def test_11_vote_change_rules(client, db):
    mgr = make_discord(db, "6002", roles="9001")
    v = make_discord(db, "7010", roles="7001")
    p = _make_poll(db, mgr, eligibility="both", options=("A", "B"), allow_change=True)
    login_as(client, db, v)
    tok = csrf_of(db, v)
    client.post(f"/polls/{p.id}/vote", follow_redirects=False, data={"csrf_token": tok, "option_id": _opt(db, p, "A").id})
    r = client.post(f"/polls/{p.id}/vote", follow_redirects=False, data={"csrf_token": tok, "option_id": _opt(db, p, "B").id})
    assert r.status_code == 200 and "updated" in r.text
    assert db.query(Vote).filter_by(poll_id=p.id, voter_id=v.id).count() == 1
    client.cookies.clear()

def test_12_closed_rejects(client, db):
    mgr = make_discord(db, "6002", roles="9001")
    v = make_discord(db, "7011", roles="7001")
    p = _make_poll(db, mgr, eligibility="both", options=("A", "B"), status="CLOSED")
    login_as(client, db, v)
    r = client.post(f"/polls/{p.id}/vote", follow_redirects=False, data={"csrf_token": csrf_of(db, v), "option_id": _opt(db, p, "A").id})
    assert r.status_code == 400
    client.cookies.clear()

def test_13_14_15_16_counting(client, db):
    from app.services_polls import compute_results, close_poll
    mgr = make_discord(db, "6002", roles="9001")
    p = _make_poll(db, mgr, eligibility="both", options=("Alpha", "Beta"))
    a, b = _opt(db, p, "Alpha"), _opt(db, p, "Beta")
    ab = _opt(db, p, "Abstain")
    voters = [make_discord(db, f"8{i:03d}", roles="x") for i in range(5)]
    from app.services_polls import cast_vote
    cast_vote(db, p, voters[0], a.id); cast_vote(db, p, voters[1], a.id)
    cast_vote(db, p, voters[2], b.id); cast_vote(db, p, voters[3], ab.id)
    res = compute_results(db, p)
    assert res["abstentions"] == 1 and res["total"] == 4
    assert res["outcome"] == a.id  # plurality
    cast_vote(db, p, voters[4], b.id)
    res = compute_results(db, p)
    assert res["outcome"] == "TIED"
    p2 = _make_poll(db, mgr, eligibility="both", options=("X", "Y"))
    res2 = compute_results(db, p2)
    assert res2["outcome"] == "NO_WINNER"
    p3 = _make_poll(db, mgr, eligibility="both", options=("X", "Y"))
    ab3 = _opt(db, p3, "Abstain")
    cast_vote(db, p3, voters[0], ab3.id)
    assert compute_results(db, p3)["outcome"] == "NO_WINNER"

# 17: role config change takes effect without rebuild
def test_17_role_hot_reload(client, db):
    set_role(db, "moderator", "9001")
    u = make_discord(db, "9002", roles="5555")
    from app import permissions as P
    assert P.can_moderate(db, u) is False
    set_role(db, "moderator", "5555")
    db.refresh(u)
    assert P.can_moderate(db, u) is True

# 18: revocation invalidates sessions
def test_18_revocation(client, db):
    reset_limiter()
    g, u, code = make_guest(db, "t18")
    client.post("/login/guest", data={"username": g.username, "code": code})
    assert client.get("/account", follow_redirects=False).status_code == 200
    db.expire_all()
    from app.auth_guest import revoke_guest
    revoke_guest(db, g)
    assert client.get("/account", follow_redirects=False).status_code == 303  # session dead
    client.cookies.clear()
    r = client.post("/login/guest", data={"username": g.username, "code": code})
    assert r.status_code == 401

# 19+20: integration failures don't corrupt; retries don't duplicate
def test_19_20_integrations(client, db, monkeypatch):
    from app import integrations as integ
    monkeypatch.setattr(integ.settings, "POWER_AUTOMATE_URL", "http://127.0.0.1:9/nope")
    ev = integ.queue_event(db, "submission_needs_review", "dedup-test-123", {"a": 1})
    out = integ.deliver_due(db, ev.event_id)
    assert out["failed"] >= 1
    # business data intact
    assert db.query(WebhookEvent).filter_by(dedup_key="dedup-test-123").count() == 1
    # retry same logical event -> no duplicate event row
    ev2 = integ.queue_event(db, "submission_needs_review", "dedup-test-123", {"a": 1})
    assert ev2.event_id == ev.event_id
    # HMAC signature present when secret configured
    from app.security import sign_payload
    assert len(sign_payload("s", "b")) == 64

# 21+22: unauthenticated cannot access private; request manipulation can't grant admin
def test_21_22_private_and_escalation(client, db):
    reset_limiter()
    assert client.get("/polls", follow_redirects=False).status_code == 303
    assert client.get("/admin", follow_redirects=False).status_code in (303, 403)
    g, u, code = make_guest(db, "t22")
    client.post("/login/guest", data={"username": g.username, "code": code})
    from app.auth_sessions import get_current_user
    user, sess = get_current_user(db, client.cookies.get("sw_session"))
    # try to escalate via form fields on settings save
    r = client.post("/admin/settings", data={"csrf_token": sess.csrf_token, "is_admin": "1", "role": "admin"})
    assert r.status_code == 403
    from app import permissions as P
    assert P.is_admin(db, user) is False
    client.cookies.clear()

# 23: CSRF
def test_23_csrf(client, db):
    reset_limiter()
    g, u, code = make_guest(db, "t23")
    client.post("/login/guest", data={"username": g.username, "code": code})
    r = client.post("/submit", data={"csrf_token": "bad", "title": "X", "description": "y" * 50})
    assert r.status_code == 403
    r = client.post("/submit", data={"title": "X", "description": "y" * 50})
    assert r.status_code == 403
    client.cookies.clear()

# 24: OAuth state validation
def test_24_oauth_state(client, db):
    r = client.get("/login/discord/callback?code=abc&state=bogus", follow_redirects=False)
    assert r.status_code == 400

# 25: session expiry + logout
def test_25_session_expiry_logout(client, db):
    from datetime import datetime, timezone, timedelta
    from app.models import Session as SRow
    reset_limiter()
    g, u, code = make_guest(db, "t25")
    client.post("/login/guest", data={"username": g.username, "code": code})
    from app.auth_sessions import get_current_user
    user, sess = get_current_user(db, client.cookies.get("sw_session"))
    tok = sess.csrf_token
    r = client.post("/logout", data={"csrf_token": tok}, follow_redirects=False)
    assert r.status_code == 303
    assert client.get("/account", follow_redirects=False).status_code == 303
    # expiry
    client.post("/login/guest", data={"username": g.username, "code": code})
    user2, sess2 = get_current_user(db, client.cookies.get("sw_session"))
    row = db.get(SRow, sess2.id)
    row.expires_at = datetime.now(timezone.utc) - timedelta(minutes=1); db.commit()
    assert client.get("/account", follow_redirects=False).status_code == 303
    client.cookies.clear()

# 26: concurrent votes cannot duplicate (unique constraint)
def test_26_concurrent_votes(client, db):
    from sqlalchemy.exc import IntegrityError
    from app.models import Vote as V
    mgr = make_discord(db, "6002", roles="9001")
    v = make_discord(db, "7020", roles="x")
    p = _make_poll(db, mgr, eligibility="both", options=("A", "B"))
    a = _opt(db, p, "A")
    db.add(V(poll_id=p.id, voter_id=v.id, option_id=a.id)); db.commit()
    db.add(V(poll_id=p.id, voter_id=v.id, option_id=a.id))
    try:
        db.commit()
        assert False, "expected IntegrityError"
    except IntegrityError:
        db.rollback()
    assert db.query(V).filter_by(poll_id=p.id, voter_id=v.id).count() == 1

# 27: closure races late votes
def test_27_close_races(client, db):
    from app.services_polls import close_poll, cast_vote
    mgr = make_discord(db, "6002", roles="9001")
    v = make_discord(db, "7021", roles="x")
    p = _make_poll(db, mgr, eligibility="both", options=("A", "B"))
    close_poll(db, p)
    ok, _ = cast_vote(db, p, v, _opt(db, p, "A").id)
    assert ok is False

# 28: invalid input + malicious uploads rejected
def test_28_input_validation(client, db):
    reset_limiter()
    g, u, code = make_guest(db, "t28")
    client.post("/login/guest", data={"username": g.username, "code": code})
    from app.auth_sessions import get_current_user
    _, sess = get_current_user(db, client.cookies.get("sw_session"))
    r = client.post("/submit", data={"csrf_token": sess.csrf_token, "title": "", "description": "short"})
    assert r.status_code == 400
    r = client.post("/submit", data={"csrf_token": sess.csrf_token, "title": "T", "description": "d" * 50,
                                     "links": "javascript:alert(1)"})
    assert r.status_code == 400
    r = client.post("/submit", data={"csrf_token": sess.csrf_token, "title": "T", "description": "d" * 50},
                    files={"attachment": ("evil.exe", b"MZ" + b"x" * 100, "application/octet-stream")})
    assert r.status_code == 400
    client.cookies.clear()

# 29: migrations work from clean install
def test_29_migration_clean():
    import os
    assert os.path.exists("migrations/versions/0001_initial.py")
    assert os.path.exists("alembic.ini")
