import math
from fastapi import APIRouter, Request, Depends, Response
from fastapi.responses import HTMLResponse, RedirectResponse, FileResponse
from sqlalchemy.orm import Session as DBSession
from app.deps import templates, get_db, ctx, site_name
from app.models import Submission, Poll
from app import permissions as P
from app.auth_sessions import COOKIE, get_current_user

router = APIRouter()

@router.get("/health")
def health():
    return {"ok": True, "service": "scoutwired-portal"}

@router.get("/", response_class=HTMLResponse)
def index(request: Request, q: str = "", category: str = "", page: int = 1, db: DBSession = Depends(get_db)):
    qry = db.query(Submission).filter(Submission.status == "PUBLISHED")
    if category:
        qry = qry.filter(Submission.category == category)
    if q:
        like = f"%{q}%"
        qry = qry.filter((Submission.title.like(like)) | (Submission.summary.like(like)))
    cats = [r[0] for r in db.query(Submission.category).filter(Submission.status == "PUBLISHED").distinct().all()]
    total = qry.count()
    per = 10
    pages = max(1, math.ceil(total / per))
    subs = qry.order_by(Submission.created_at.desc()).offset((page - 1) * per).limit(per).all()
    c = ctx(request, db, submissions=subs, categories=cats, q=q, category=category, page=page, pages=pages, site_name=site_name(db))
    return templates.TemplateResponse(request, "index.html", c)

@router.get("/help", response_class=HTMLResponse)
def help_page(request: Request, db: DBSession = Depends(get_db)):
    return templates.TemplateResponse(request, "help.html", ctx(request, db, site_name=site_name(db)))

@router.get("/account", response_class=HTMLResponse)
def account(request: Request, db: DBSession = Depends(get_db)):
    user, sess = get_current_user(db, request.cookies.get(COOKIE))
    if not user:
        return RedirectResponse("/login", status_code=303)
    return templates.TemplateResponse(request, "account.html", ctx(request, db, site_name=site_name(db)))

@router.get("/submissions/{sid}", response_class=HTMLResponse)
def submission_detail(sid: str, request: Request, db: DBSession = Depends(get_db)):
    s = db.get(Submission, sid)
    user, _ = get_current_user(db, request.cookies.get(COOKIE))
    if not s:
        return templates.TemplateResponse(request, "error.html", ctx(request, db, code=404, message="Not found", site_name=site_name(db)), status_code=404)
    if s.status != "PUBLISHED":
        # only submitter or moderator/admin may view unpublished
        allowed = bool(user and (user.id == s.submitter_id or P.can_moderate(db, user)))
        if not allowed:
            return templates.TemplateResponse(request, "error.html", ctx(request, db, code=404, message="Not found", site_name=site_name(db)), status_code=404)
    links = [l.strip() for l in (s.links or "").splitlines() if l.strip().startswith(("http://", "https://"))]
    return templates.TemplateResponse(request, "submission_detail.html", ctx(request, db, s=s, links=links, site_name=site_name(db)))

@router.get("/submissions/{sid}/attachment")
def submission_attachment(sid: str, request: Request, db: DBSession = Depends(get_db)):
    import os
    s = db.get(Submission, sid)
    user, _ = get_current_user(db, request.cookies.get(COOKIE))
    if not s or not s.attachment_path or not os.path.exists(s.attachment_path):
        return templates.TemplateResponse(request, "error.html", ctx(request, db, code=404, message="Not found", site_name=site_name(db)), status_code=404)
    if s.status != "PUBLISHED":
        allowed = bool(user and (user.id == s.submitter_id or P.can_moderate(db, user)))
        if not allowed:
            return templates.TemplateResponse(request, "error.html", ctx(request, db, code=404, message="Not found", site_name=site_name(db)), status_code=404)
    if s.status == "PUBLISHED" and not user:
        return RedirectResponse("/login", status_code=303)
    return FileResponse(s.attachment_path, filename=s.attachment_name or "attachment")
