import csv, io, os
from datetime import datetime, timezone
from fastapi import APIRouter, Request, Depends
from fastapi.responses import HTMLResponse, RedirectResponse, PlainTextResponse
from sqlalchemy.orm import Session as DBSession
from app.deps import templates, get_db, ctx, site_name, check_csrf
from app.models import (Submission, ModerationDecision, Poll, PollOption, Vote, GuestAccount,
                        User, RoleConfig, AuditEvent, WebhookEvent, DeliveryAttempt, AppSetting)
from app.auth_sessions import COOKIE, get_current_user
from app import permissions as P
from app.services_audit import audit
from app.auth_guest import create_guest, regenerate_code, revoke_guest
from app.auth_discord import validate_guild_roles
from app.config import settings
from app.services_polls import compute_results, close_poll
from app.integrations import deliver_due, fire_and_forget

router = APIRouter(prefix="/admin")

def guard(request, db, need: str):
    user, sess = get_current_user(db, request.cookies.get(COOKIE))
    if not user:
        return None, None, RedirectResponse("/login", status_code=303)
    ok = {"admin": P.is_admin, "moderate": P.can_moderate, "votes": P.can_manage_votes, "guests": P.can_manage_guests}.get(need, P.is_admin)(db, user)
    if not ok:
        # refresh discord roles live and re-check (fail closed)
        from app.auth_discord import fetch_member_roles
        if user.kind == "discord" and user.discord_id:
            roles = fetch_member_roles(user.discord_id)
            if roles is not None:
                user.roles_cache = ",".join(sorted(roles)); db.commit()
                ok = {"admin": P.is_admin, "moderate": P.can_moderate, "votes": P.can_manage_votes, "guests": P.can_manage_guests}.get(need, P.is_admin)(db, user)
    if not ok:
        return None, None, templates.TemplateResponse(request, "error.html", ctx(request, db, code=403, message="Forbidden", site_name=site_name(db)), status_code=403)
    return user, sess, None

@router.get("", response_class=HTMLResponse)
def dashboard(request: Request, db: DBSession = Depends(get_db)):
    user, sess, err = guard(request, db, "admin")
    if err is not None:
        return err
    stats = {
        "pending": db.query(Submission).filter_by(status="PENDING_REVIEW").count(),
        "approved": db.query(Submission).filter_by(status="APPROVED").count(),
        "open_polls": db.query(Poll).filter_by(status="OPEN").count(),
        "guests": db.query(GuestAccount).filter_by(active=True).count(),
        "failed": db.query(DeliveryAttempt).filter_by(status="failed").count(),
    }
    recent = db.query(AuditEvent).order_by(AuditEvent.id.desc()).limit(15).all()
    return templates.TemplateResponse(request, "admin/dashboard.html", ctx(request, db, stats=stats, recent=recent, site_name=site_name(db)))

# ---- moderation ----
@router.get("/submissions", response_class=HTMLResponse)
def mod_queue(request: Request, q: str = "", status: str = "", db: DBSession = Depends(get_db)):
    user, sess, err = guard(request, db, "moderate")
    if err is not None:
        return err
    qry = db.query(Submission)
    if status:
        qry = qry.filter(Submission.status == status)
    if q:
        like = f"%{q}%"
        qry = qry.filter((Submission.title.like(like)) | (Submission.summary.like(like)))
    subs = qry.order_by(Submission.created_at.desc()).limit(200).all()
    statuses = ["PENDING_REVIEW", "CHANGES_REQUESTED", "APPROVED", "REJECTED", "PUBLISHED", "ARCHIVED"]
    return templates.TemplateResponse(request, "admin/submissions.html", ctx(request, db, subs=subs, statuses=statuses, q=q, status=status, site_name=site_name(db)))

@router.get("/submissions/{sid}", response_class=HTMLResponse)
def mod_detail(sid: str, request: Request, db: DBSession = Depends(get_db)):
    user, sess, err = guard(request, db, "moderate")
    if err is not None:
        return err
    s = db.get(Submission, sid)
    if not s:
        return templates.TemplateResponse(request, "error.html", ctx(request, db, code=404, message="Not found", site_name=site_name(db)), status_code=404)
    hist = db.query(ModerationDecision).filter_by(submission_id=sid).order_by(ModerationDecision.id.desc()).all()
    return templates.TemplateResponse(request, "admin/submission_detail.html", ctx(request, db, s=s, history=hist, site_name=site_name(db)))

@router.post("/submissions/{sid}/decision")
async def mod_decision(sid: str, request: Request, db: DBSession = Depends(get_db)):
    user, sess, err = guard(request, db, "moderate")
    if err is not None:
        return err
    form = await request.form()
    if not check_csrf(sess, form.get("csrf_token")):
        return templates.TemplateResponse(request, "error.html", ctx(request, db, code=403, message="Invalid session token.", site_name=site_name(db)), status_code=403)
    s = db.get(Submission, sid)
    if not s:
        return templates.TemplateResponse(request, "error.html", ctx(request, db, code=404, message="Not found", site_name=site_name(db)), status_code=404)
    action = (form.get("action") or "").strip()
    reason = (form.get("reason") or "").strip()[:2000]
    note = (form.get("internal_note") or "").strip()[:2000]
    mapping = {"APPROVE": "APPROVED", "REJECT": "REJECTED", "CHANGES": "CHANGES_REQUESTED", "PUBLISH": "PUBLISHED", "ARCHIVE": "ARCHIVED"}
    if action not in mapping:
        return templates.TemplateResponse(request, "error.html", ctx(request, db, code=400, message="Invalid action.", site_name=site_name(db)), status_code=400)
    # publish requires APPROVED first
    if action == "PUBLISH" and s.status != "APPROVED":
        return templates.TemplateResponse(request, "error.html", ctx(request, db, code=400, message="Only APPROVED submissions can be published.", site_name=site_name(db)), status_code=400)
    s.status = mapping[action]
    s.updated_at = datetime.now(timezone.utc)
    db.add(ModerationDecision(submission_id=sid, moderator_id=user.id, action=action, reason=reason, internal_note=note))
    db.commit()
    audit(db, user.id, f"moderation_{action.lower()}", sid, reason[:300])
    evmap = {"APPROVE": "submission_approved", "REJECT": "submission_rejected", "CHANGES": "changes_requested", "PUBLISH": "submission_published", "ARCHIVE": "submission_archived"}
    fire_and_forget(evmap[action], f"sub-{sid}-{action}-{s.updated_at.isoformat()}",
                    {"event_type": evmap[action], "submission_id": sid, "title": s.title, "category": s.category,
                     "status": s.status, "admin_url": f"{settings.APP_URL}/admin/submissions/{sid}"})
    return RedirectResponse(f"/admin/submissions/{sid}", status_code=303)

@router.get("/submissions/{sid}/attachment")
def mod_attachment(sid: str, request: Request, db: DBSession = Depends(get_db)):
    user, sess, err = guard(request, db, "moderate")
    if err is not None:
        return err
    from fastapi.responses import FileResponse
    s = db.get(Submission, sid)
    if not s or not s.attachment_path or not os.path.exists(s.attachment_path):
        return templates.TemplateResponse(request, "error.html", ctx(request, db, code=404, message="Not found", site_name=site_name(db)), status_code=404)
    return FileResponse(s.attachment_path, filename=s.attachment_name or "attachment")

# ---- polls ----
@router.get("/polls", response_class=HTMLResponse)
def polls_admin(request: Request, db: DBSession = Depends(get_db)):
    user, sess, err = guard(request, db, "votes")
    if err is not None:
        return err
    polls = db.query(Poll).order_by(Poll.created_at.desc()).all()
    return templates.TemplateResponse(request, "admin/polls.html", ctx(request, db, polls=polls, site_name=site_name(db)))

@router.get("/polls/new", response_class=HTMLResponse)
def poll_new_form(request: Request, db: DBSession = Depends(get_db)):
    user, sess, err = guard(request, db, "votes")
    if err is not None:
        return err
    return templates.TemplateResponse(request, "admin/poll_form.html", ctx(request, db, poll=None, guest_restrict=None, site_name=site_name(db)))

@router.post("/polls/new")
async def poll_create(request: Request, db: DBSession = Depends(get_db)):
    user, sess, err = guard(request, db, "votes")
    if err is not None:
        return err
    form = await request.form()
    if not check_csrf(sess, form.get("csrf_token")):
        return templates.TemplateResponse(request, "error.html", ctx(request, db, code=403, message="Invalid session token.", site_name=site_name(db)), status_code=403)
    title = (form.get("title") or "").strip()
    if not title or len(title) > 200:
        return templates.TemplateResponse(request, "error.html", ctx(request, db, code=400, message="Title required.", site_name=site_name(db)), status_code=400)
    desc = (form.get("description") or "")[:5000]
    sub_id = (form.get("submission_id") or "").strip() or None
    if sub_id:
        sub = db.get(Submission, sub_id)
        if not sub or sub.status not in ("APPROVED", "PUBLISHED"):
            return templates.TemplateResponse(request, "error.html", ctx(request, db, code=400, message="Linked submission must be APPROVED or PUBLISHED.", site_name=site_name(db)), status_code=400)
    elig = (form.get("eligibility") or "both").strip()
    if elig not in ("discord", "guests", "both"):
        elig = "both"
    def parse_dt(v):
        v = (v or "").strip()
        if not v:
            return None
        try:
            d = datetime.fromisoformat(v)
            if d.tzinfo is None:
                d = d.replace(tzinfo=timezone.utc)
            return d
        except ValueError:
            return None
    p = Poll(title=title, description=desc, submission_id=sub_id, status="DRAFT", eligibility=elig,
             allow_change=bool(form.get("allow_change")), allow_abstain=form.get("allow_abstain") != "0" and bool(form.get("allow_abstain", "1")),
             hide_until_close=bool(form.get("hide_until_close", "1")),
             opens_at=parse_dt(form.get("opens_at")), closes_at=parse_dt(form.get("closes_at")), created_by=user.id)
    # allow_abstain checkbox: present means true
    p.allow_abstain = bool(form.get("allow_abstain"))
    p.hide_until_close = bool(form.get("hide_until_close"))
    db.add(p); db.flush()
    lines = [(form.get("options") or "").strip().splitlines()]
    opts = [l.strip()[:200] for l in lines[0] if l.strip()]
    pos = 0
    for t in opts[:20]:
        db.add(PollOption(poll_id=p.id, text=t, position=pos)); pos += 1
    if p.allow_abstain:
        db.add(PollOption(poll_id=p.id, text="Abstain", position=pos, is_abstain=True))
    db.commit()
    audit(db, user.id, "poll_created", p.id, title[:200])
    return RedirectResponse(f"/admin/polls/{p.id}", status_code=303)

@router.get("/polls/{pid}", response_class=HTMLResponse)
def poll_manage(pid: str, request: Request, db: DBSession = Depends(get_db)):
    user, sess, err = guard(request, db, "votes")
    if err is not None:
        return err
    p = db.get(Poll, pid)
    if not p:
        return templates.TemplateResponse(request, "error.html", ctx(request, db, code=404, message="Not found", site_name=site_name(db)), status_code=404)
    from app.models import PollGuestAllow as PGA
    opts = db.query(PollOption).filter_by(poll_id=pid).order_by(PollOption.position).all()
    otext = "\n".join(o.text for o in opts if not o.is_abstain)
    allowed = db.query(PGA).filter_by(poll_id=pid).all()
    restrict = ""
    if allowed:
        users = [db.get(User, r.user_id) for r in allowed]
        restrict = "\n".join((db.get(GuestAccount, u.guest_id).username if u and u.guest_id else "") for u in users if u)
    return templates.TemplateResponse(request, "admin/poll_form.html", ctx(request, db, poll=p, options_text=otext, guest_restrict=restrict, site_name=site_name(db)))

@router.post("/polls/{pid}/edit")
async def poll_edit(pid: str, request: Request, db: DBSession = Depends(get_db)):
    user, sess, err = guard(request, db, "votes")
    if err is not None:
        return err
    p = db.get(Poll, pid)
    if not p:
        return templates.TemplateResponse(request, "error.html", ctx(request, db, code=404, message="Not found", site_name=site_name(db)), status_code=404)
    if p.status not in ("DRAFT", "OPEN"):
        return templates.TemplateResponse(request, "error.html", ctx(request, db, code=400, message="Only DRAFT/OPEN polls can be edited.", site_name=site_name(db)), status_code=400)
    form = await request.form()
    if not check_csrf(sess, form.get("csrf_token")):
        return templates.TemplateResponse(request, "error.html", ctx(request, db, code=403, message="Invalid session token.", site_name=site_name(db)), status_code=403)
    p.title = (form.get("title") or p.title).strip()[:200]
    p.description = (form.get("description") or "")[:5000]
    elig = (form.get("eligibility") or p.eligibility).strip()
    if elig in ("discord", "guests", "both"):
        p.eligibility = elig
    p.allow_change = bool(form.get("allow_change"))
    # options replace only if no votes yet
    if db.query(Vote).filter_by(poll_id=pid).count() == 0:
        db.query(PollOption).filter_by(poll_id=pid).delete()
        opts = [l.strip()[:200] for l in (form.get("options") or "").splitlines() if l.strip()]
        pos = 0
        for t in opts[:20]:
            db.add(PollOption(poll_id=pid, text=t, position=pos)); pos += 1
        if bool(form.get("allow_abstain")):
            db.add(PollOption(poll_id=pid, text="Abstain", position=pos, is_abstain=True))
        p.allow_abstain = bool(form.get("allow_abstain"))
    p.hide_until_close = bool(form.get("hide_until_close"))
    db.commit()
    audit(db, user.id, "poll_edited", pid, p.title[:200])
    return RedirectResponse(f"/admin/polls/{pid}", status_code=303)

@router.post("/polls/{pid}/lifecycle")
async def poll_lifecycle(pid: str, request: Request, db: DBSession = Depends(get_db)):
    user, sess, err = guard(request, db, "votes")
    if err is not None:
        return err
    p = db.get(Poll, pid)
    if not p:
        return templates.TemplateResponse(request, "error.html", ctx(request, db, code=404, message="Not found", site_name=site_name(db)), status_code=404)
    form = await request.form()
    if not check_csrf(sess, form.get("csrf_token")):
        return templates.TemplateResponse(request, "error.html", ctx(request, db, code=403, message="Invalid session token.", site_name=site_name(db)), status_code=403)
    op = (form.get("op") or "").strip()
    if op == "open":
        if db.query(PollOption).filter_by(poll_id=pid).count() == 0:
            return templates.TemplateResponse(request, "error.html", ctx(request, db, code=400, message="Add options before opening.", site_name=site_name(db)), status_code=400)
        p.status = "OPEN"; db.commit()
        audit(db, user.id, "poll_opened", pid, "")
        fire_and_forget("poll_opened", f"poll-open-{pid}", {"event_type": "poll_opened", "poll_id": pid, "title": p.title, "status": "OPEN", "admin_url": f"{settings.APP_URL}/polls/{pid}"})
    elif op == "close":
        res = close_poll(db, p)
        audit(db, user.id, "poll_closed", pid, str(res["outcome"]))
        fire_and_forget("poll_closed", f"poll-close-{pid}-{p.closed_at.isoformat()}" if p.closed_at else f"poll-close-{pid}",
                        {"event_type": "poll_closed", "poll_id": pid, "title": p.title, "status": "CLOSED", "outcome": res["outcome"], "admin_url": f"{settings.APP_URL}/results/{pid}"})
    elif op == "reopen":
        if p.status != "CLOSED":
            return templates.TemplateResponse(request, "error.html", ctx(request, db, code=400, message="Only CLOSED polls can be reopened.", site_name=site_name(db)), status_code=400)
        p.status = "OPEN"; p.result = ""; db.commit()
        audit(db, user.id, "poll_reopened", pid, "explicit reopen with audit")
    elif op == "archive":
        p.status = "ARCHIVED"; db.commit()
        audit(db, user.id, "poll_archived", pid, "")
    return RedirectResponse(f"/admin/polls/{pid}", status_code=303)

@router.post("/polls/{pid}/guests")
async def poll_guests(pid: str, request: Request, db: DBSession = Depends(get_db)):
    from app.models import PollGuestAllow as PGA
    user, sess, err = guard(request, db, "votes")
    if err is not None:
        return err
    form = await request.form()
    if not check_csrf(sess, form.get("csrf_token")):
        return templates.TemplateResponse(request, "error.html", ctx(request, db, code=403, message="Invalid session token.", site_name=site_name(db)), status_code=403)
    db.query(PGA).filter_by(poll_id=pid).delete()
    for line in (form.get("usernames") or "").splitlines():
        name = line.strip()
        if not name:
            continue
        g = db.query(GuestAccount).filter_by(username=name).first()
        if not g:
            continue
        u = db.query(User).filter_by(guest_id=g.id).first()
        if u:
            db.add(PGA(poll_id=pid, user_id=u.id))
    db.commit()
    audit(db, user.id, "poll_guests_updated", pid, "")
    return RedirectResponse(f"/admin/polls/{pid}", status_code=303)

@router.get("/polls/{pid}/export")
def poll_export(pid: str, request: Request, db: DBSession = Depends(get_db)):
    user, sess, err = guard(request, db, "votes")
    if err is not None:
        return err
    p = db.get(Poll, pid)
    if not p:
        return PlainTextResponse("not found", status_code=404)
    res = compute_results(db, p)
    buf = io.StringIO()
    w = csv.writer(buf)
    w.writerow(["option", "votes", "abstain"])
    for o in res["options"]:
        w.writerow([o["text"], o["votes"], o["abstain"]])
    w.writerow(["OUTCOME", res["outcome"], ""])
    audit(db, user.id, "poll_exported", pid, "")
    return PlainTextResponse(buf.getvalue(), media_type="text/csv",
                             headers={"Content-Disposition": f"attachment; filename=poll-{pid}.csv"})

# ---- guests ----
@router.get("/guests", response_class=HTMLResponse)
def guests_page(request: Request, db: DBSession = Depends(get_db)):
    user, sess, err = guard(request, db, "guests")
    if err is not None:
        return err
    guests = db.query(GuestAccount).order_by(GuestAccount.created_at.desc()).limit(300).all()
    return templates.TemplateResponse(request, "admin/guests.html", ctx(request, db, guests=guests, new_cred=None, site_name=site_name(db)))

@router.post("/guests/new")
async def guest_new(request: Request, db: DBSession = Depends(get_db)):
    user, sess, err = guard(request, db, "guests")
    if err is not None:
        return err
    form = await request.form()
    if not check_csrf(sess, form.get("csrf_token")):
        return templates.TemplateResponse(request, "error.html", ctx(request, db, code=403, message="Invalid session token.", site_name=site_name(db)), status_code=403)
    note = ((form.get("note") or "").strip())[:200]
    g, u, code = create_guest(db, note=note)
    audit(db, user.id, "guest_created", g.id, g.username)
    fire_and_forget("guest_created", f"guest-created-{g.id}", {"event_type": "guest_created", "guest_username": g.username, "note": "redacted", "admin_url": f"{settings.APP_URL}/admin/guests"})
    guests = db.query(GuestAccount).order_by(GuestAccount.created_at.desc()).limit(300).all()
    return templates.TemplateResponse(request, "admin/guests.html", ctx(request, db, guests=guests, new_cred={"username": g.username, "code": code}, site_name=site_name(db)))

@router.post("/guests/{gid}/action")
async def guest_action(gid: str, request: Request, db: DBSession = Depends(get_db)):
    user, sess, err = guard(request, db, "guests")
    if err is not None:
        return err
    form = await request.form()
    if not check_csrf(sess, form.get("csrf_token")):
        return templates.TemplateResponse(request, "error.html", ctx(request, db, code=403, message="Invalid session token.", site_name=site_name(db)), status_code=403)
    g = db.get(GuestAccount, gid)
    if not g:
        return templates.TemplateResponse(request, "error.html", ctx(request, db, code=404, message="Not found", site_name=site_name(db)), status_code=404)
    op = (form.get("op") or "").strip()
    new_cred = None
    if op == "revoke":
        revoke_guest(db, g)
        audit(db, user.id, "guest_revoked", gid, g.username)
        fire_and_forget("guest_revoked", f"guest-revoked-{gid}", {"event_type": "guest_revoked", "guest_username": g.username, "admin_url": f"{settings.APP_URL}/admin/guests"})
    elif op == "regenerate":
        code = regenerate_code(db, g)
        audit(db, user.id, "guest_regenerated", gid, g.username)
        new_cred = {"username": g.username, "code": code}
    elif op == "activate":
        g.active = True; db.commit()
        audit(db, user.id, "guest_activated", gid, g.username)
    guests = db.query(GuestAccount).order_by(GuestAccount.created_at.desc()).limit(300).all()
    return templates.TemplateResponse(request, "admin/guests.html", ctx(request, db, guests=guests, new_cred=new_cred, site_name=site_name(db)))

# ---- roles ----
@router.get("/roles", response_class=HTMLResponse)
def roles_page(request: Request, db: DBSession = Depends(get_db)):
    user, sess, err = guard(request, db, "admin")
    if err is not None:
        return err
    from app.models import AppSetting as AS
    configs = db.query(RoleConfig).all()
    g = db.get(AS, "guild_id")
    return templates.TemplateResponse(request, "admin/roles.html", ctx(request, db, configs=configs, guild=(g.value if g else settings.DISCORD_GUILD_ID), site_name=site_name(db)))

@router.post("/roles")
async def roles_save(request: Request, db: DBSession = Depends(get_db)):
    user, sess, err = guard(request, db, "admin")
    if err is not None:
        return err
    form = await request.form()
    if not check_csrf(sess, form.get("csrf_token")):
        return templates.TemplateResponse(request, "error.html", ctx(request, db, code=403, message="Invalid session token.", site_name=site_name(db)), status_code=403)
    from app.models import AppSetting as AS
    import re
    msg = "Saved."
    guild = (form.get("guild_id") or "").strip()
    if guild and not guild.isdigit():
        return templates.TemplateResponse(request, "admin/roles.html", ctx(request, db, configs=db.query(RoleConfig).all(), guild=guild, msg="Invalid guild ID format.", site_name=site_name(db)), status_code=400)
    if guild:
        r = db.get(AS, "guild_id")
        if r:
            r.value = guild
        else:
            db.add(AS(key="guild_id", value=guild))
    all_roles: list[str] = []
    for cat, _, _ in P.CATEGORIES:
        raw = (form.get(f"roles_{cat}") or "").strip()
        ids = [x.strip() for x in raw.split(",") if x.strip()]
        for x in ids:
            if not re.fullmatch(r"\d{5,25}", x):
                return templates.TemplateResponse(request, "admin/roles.html", ctx(request, db, configs=db.query(RoleConfig).all(), guild=guild, msg=f"Invalid role id {x!r} in {cat}.", site_name=site_name(db)), status_code=400)
        rc = db.get(RoleConfig, cat)
        if rc:
            rc.role_ids = ",".join(ids)
            rc.enabled = bool(form.get(f"enabled_{cat}"))
        all_roles.extend(ids)
    db.commit()
    ok, vmsg = validate_guild_roles(all_roles)
    audit(db, user.id, "roles_updated", "", vmsg[:300])
    configs = db.query(RoleConfig).all()
    return templates.TemplateResponse(request, "admin/roles.html", ctx(request, db, configs=configs, guild=guild, msg=vmsg, site_name=site_name(db)))

@router.post("/roles/test")
async def roles_test(request: Request, db: DBSession = Depends(get_db)):
    user, sess, err = guard(request, db, "admin")
    if err is not None:
        return err
    form = await request.form()
    if not check_csrf(sess, form.get("csrf_token")):
        return templates.TemplateResponse(request, "error.html", ctx(request, db, code=403, message="Invalid session token.", site_name=site_name(db)), status_code=403)
    from app.auth_discord import API
    import httpx
    try:
        r = httpx.get(f"{API}/guilds/{settings.DISCORD_GUILD_ID}/roles",
                      headers={"Authorization": f"Bot {settings.DISCORD_BOT_TOKEN}"}, timeout=8)
        res = f"Guild API HTTP {r.status_code}" if r.status_code != 200 else f"OK — {len(r.json())} roles found."
    except Exception as e:
        res = f"Connection failed: {e}"
    configs = db.query(RoleConfig).all()
    from app.models import AppSetting as AS
    g = db.get(AS, "guild_id")
    return templates.TemplateResponse(request, "admin/roles.html", ctx(request, db, configs=configs, guild=(g.value if g else ""), test_result=res, site_name=site_name(db)))

# ---- integrations ----
@router.get("/integrations", response_class=HTMLResponse)
def integrations_page(request: Request, db: DBSession = Depends(get_db)):
    user, sess, err = guard(request, db, "admin")
    if err is not None:
        return err
    rows = db.query(DeliveryAttempt, WebhookEvent).join(WebhookEvent, WebhookEvent.event_id == DeliveryAttempt.event_id).order_by(DeliveryAttempt.id.desc()).limit(50).all()
    deliveries = [{"event_id": ev.event_id, "event_type": ev.event_type, "target": a.target, "status": a.status, "tries": a.tries, "last_error": a.last_error} for a, ev in rows]
    return templates.TemplateResponse(request, "admin/integrations.html", ctx(request, db, deliveries=deliveries,
        oauth_ok=bool(settings.DISCORD_CLIENT_ID), bot_ok=bool(settings.DISCORD_BOT_TOKEN),
        pa_ok=bool(settings.POWER_AUTOMATE_URL), site_name=site_name(db)))

@router.post("/integrations/retry")
async def integrations_retry(request: Request, db: DBSession = Depends(get_db)):
    user, sess, err = guard(request, db, "admin")
    if err is not None:
        return err
    form = await request.form()
    if not check_csrf(sess, form.get("csrf_token")):
        return templates.TemplateResponse(request, "error.html", ctx(request, db, code=403, message="Invalid session token.", site_name=site_name(db)), status_code=403)
    # reset failed-but-retryable to pending
    for a in db.query(DeliveryAttempt).filter_by(status="failed").all():
        if a.tries < 3:
            a.status = "pending"
    db.commit()
    out = deliver_due(db)
    audit(db, user.id, "deliveries_retried", "", str(out))
    return RedirectResponse("/admin/integrations", status_code=303)

# ---- settings ----
DEFAULT_SETTINGS = {"site_name": "ScoutWired Portal", "timezone": "UTC", "maintenance_mode": "off",
                    "submission_max_length": "20000", "results_default_visibility": "after_close"}

@router.get("/settings", response_class=HTMLResponse)
def settings_page(request: Request, db: DBSession = Depends(get_db)):
    user, sess, err = guard(request, db, "admin")
    if err is not None:
        return err
    items = []
    for k, d in DEFAULT_SETTINGS.items():
        r = db.get(AppSetting, k)
        items.append((k, r.value if r else d))
    return templates.TemplateResponse(request, "admin/settings.html", ctx(request, db, settings_items=items, site_name=site_name(db)))

@router.post("/settings")
async def settings_save(request: Request, db: DBSession = Depends(get_db)):
    user, sess, err = guard(request, db, "admin")
    if err is not None:
        return err
    form = await request.form()
    if not check_csrf(sess, form.get("csrf_token")):
        return templates.TemplateResponse(request, "error.html", ctx(request, db, code=403, message="Invalid session token.", site_name=site_name(db)), status_code=403)
    for k in DEFAULT_SETTINGS:
        v = (form.get(k) or "").strip()[:500]
        r = db.get(AppSetting, k)
        if r:
            r.value = v
        else:
            db.add(AppSetting(key=k, value=v))
    db.commit()
    audit(db, user.id, "settings_updated", "", "")
    return RedirectResponse("/admin/settings", status_code=303)

# ---- audit ----
@router.get("/audit", response_class=HTMLResponse)
def audit_page(request: Request, db: DBSession = Depends(get_db)):
    user, sess, err = guard(request, db, "admin")
    if err is not None:
        return err
    events = db.query(AuditEvent).order_by(AuditEvent.id.desc()).limit(300).all()
    return templates.TemplateResponse(request, "admin/audit.html", ctx(request, db, events=events, site_name=site_name(db)))
