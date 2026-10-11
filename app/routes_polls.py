from fastapi import APIRouter, Request, Depends, Form
from fastapi.responses import HTMLResponse, RedirectResponse
from sqlalchemy.orm import Session as DBSession
from app.deps import templates, get_db, ctx, site_name, check_csrf
from app.models import Poll, PollOption, Vote, User, PollGuestAllow
from app.auth_sessions import COOKIE, get_current_user
from app.services_polls import eligible_to_vote, cast_vote, compute_results, poll_is_open
from app import permissions as P
from app.services_audit import audit

router = APIRouter()

def _visible_polls(db, user):
    polls = db.query(Poll).filter(Poll.status.in_(["OPEN", "CLOSED"])).order_by(Poll.created_at.desc()).all()
    return polls

@router.get("/polls", response_class=HTMLResponse)
def polls_list(request: Request, db: DBSession = Depends(get_db)):
    user, _ = get_current_user(db, request.cookies.get(COOKIE))
    if not user:
        return RedirectResponse("/login", status_code=303)
    polls = _visible_polls(db, user)
    # hide polls user is not eligible for? Show open ones they can access; keep closed per visibility
    shown = []
    for p in polls:
        if p.status == "OPEN":
            if eligible_to_vote(db, p, user):
                shown.append(p)
        else:
            shown.append(p)
    return templates.TemplateResponse(request, "polls.html", ctx(request, db, polls=shown, site_name=site_name(db)))

@router.get("/polls/{pid}", response_class=HTMLResponse)
def poll_detail(pid: str, request: Request, db: DBSession = Depends(get_db)):
    user, sess = get_current_user(db, request.cookies.get(COOKIE))
    if not user:
        return RedirectResponse("/login", status_code=303)
    p = db.get(Poll, pid)
    if not p or p.status not in ("OPEN", "CLOSED"):
        return templates.TemplateResponse(request, "error.html", ctx(request, db, code=404, message="Not found", site_name=site_name(db)), status_code=404)
    if not eligible_to_vote(db, p, user):
        return templates.TemplateResponse(request, "error.html", ctx(request, db, code=403, message="You are not eligible for this poll.", site_name=site_name(db)), status_code=403)
    opts = db.query(PollOption).filter_by(poll_id=p.id).order_by(PollOption.position).all()
    my_vote = db.query(Vote).filter_by(poll_id=p.id, voter_id=user.id).first()
    my_text = ""
    if my_vote:
        o = db.get(PollOption, my_vote.option_id)
        my_text = o.text if o else ""
    can_vote = poll_is_open(p)
    note = "" if can_vote else "This poll is closed."
    return templates.TemplateResponse(request, "poll_detail.html", ctx(request, db, poll=p, options=opts, can_vote=can_vote,
        vote_note=note, my_vote=my_vote, my_vote_text=my_text, site_name=site_name(db)))

@router.post("/polls/{pid}/vote", response_class=HTMLResponse)
async def vote(pid: str, request: Request, db: DBSession = Depends(get_db)):
    user, sess = get_current_user(db, request.cookies.get(COOKIE))
    if not user:
        return RedirectResponse("/login", status_code=303)
    form = await request.form()
    if not check_csrf(sess, form.get("csrf_token")):
        return templates.TemplateResponse(request, "error.html", ctx(request, db, code=403, message="Invalid session token.", site_name=site_name(db)), status_code=403)
    p = db.get(Poll, pid)
    if not p or p.status not in ("OPEN", "CLOSED"):
        return templates.TemplateResponse(request, "error.html", ctx(request, db, code=404, message="Not found", site_name=site_name(db)), status_code=404)
    if not eligible_to_vote(db, p, user):
        audit(db, user.id, "vote_denied", pid, "ineligible")
        return templates.TemplateResponse(request, "error.html", ctx(request, db, code=403, message="You are not eligible for this poll.", site_name=site_name(db)), status_code=403)
    ok, msg = cast_vote(db, p, user, (form.get("option_id") or "").strip())
    if ok:
        audit(db, user.id, "vote_cast", pid, msg)
    opts = db.query(PollOption).filter_by(poll_id=p.id).order_by(PollOption.position).all()
    my_vote = db.query(Vote).filter_by(poll_id=p.id, voter_id=user.id).first()
    my_text = ""
    if my_vote:
        o = db.get(PollOption, my_vote.option_id)
        my_text = o.text if o else ""
    status = 200 if ok else 400
    c = ctx(request, db, poll=p, options=opts, can_vote=poll_is_open(p),
            vote_note="" if ok else msg, my_vote=my_vote, my_vote_text=my_text,
            flash=msg if ok else None, error=None if ok else msg, site_name=site_name(db))
    return templates.TemplateResponse(request, "poll_detail.html", c, status_code=status)

@router.get("/results/{pid}", response_class=HTMLResponse)
def results(pid: str, request: Request, db: DBSession = Depends(get_db)):
    user, _ = get_current_user(db, request.cookies.get(COOKIE))
    if not user:
        return RedirectResponse("/login", status_code=303)
    p = db.get(Poll, pid)
    if not p or p.status not in ("OPEN", "CLOSED"):
        return templates.TemplateResponse(request, "error.html", ctx(request, db, code=404, message="Not found", site_name=site_name(db)), status_code=404)
    if not eligible_to_vote(db, p, user) and not P.can_manage_votes(db, user):
        # results viewers: discord role
        if not (user.kind == "discord" and P.has_perm(db, user, "results_viewer")) and not P.is_admin(db, user):
            return templates.TemplateResponse(request, "error.html", ctx(request, db, code=403, message="Not eligible.", site_name=site_name(db)), status_code=403)
    res = compute_results(db, p)
    visible = (p.status == "CLOSED") or not p.hide_until_close or P.can_manage_votes(db, user)
    return templates.TemplateResponse(request, "results.html", ctx(request, db, poll=p, results=res, visible=visible, site_name=site_name(db)))
