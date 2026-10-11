import os, re
from datetime import datetime, timezone
from fastapi import APIRouter, Request, Depends, UploadFile, File, Form
from fastapi.responses import HTMLResponse, RedirectResponse
from sqlalchemy.orm import Session as DBSession
from app.deps import templates, get_db, ctx, site_name, check_csrf
from app.models import Submission, SubmissionRevision
from app.config import settings
from app import permissions as P
from app.auth_sessions import COOKIE, get_current_user
from app.services_audit import audit
from app.integrations import fire_and_forget

router = APIRouter()
URL_RE = re.compile(r"^https?://[^\s]{3,2000}$")
ALLOWED = {e.strip().lower() for e in settings.ALLOWED_UPLOAD_EXTS.split(",") if e.strip()}

@router.get("/submit", response_class=HTMLResponse)
def submit_form(request: Request, db: DBSession = Depends(get_db)):
    user, sess = get_current_user(db, request.cookies.get(COOKIE))
    if not user:
        return RedirectResponse("/login", status_code=303)
    if not P.can_contribute(db, user):
        return templates.TemplateResponse(request, "error.html", ctx(request, db, code=403, message="You may not submit proposals.", site_name=site_name(db)), status_code=403)
    return templates.TemplateResponse(request, "submit.html", ctx(request, db, form={}, max_mb=settings.MAX_UPLOAD_MB, site_name=site_name(db)))

@router.post("/submit")
async def submit_create(request: Request, db: DBSession = Depends(get_db)):
    user, sess = get_current_user(db, request.cookies.get(COOKIE))
    if not user:
        return RedirectResponse("/login", status_code=303)
    if not P.can_contribute(db, user):
        return templates.TemplateResponse(request, "error.html", ctx(request, db, code=403, message="Forbidden", site_name=site_name(db)), status_code=403)
    form = await request.form()
    if not check_csrf(sess, form.get("csrf_token")):
        return templates.TemplateResponse(request, "error.html", ctx(request, db, code=403, message="Invalid session token.", site_name=site_name(db)), status_code=403)
    title = (form.get("title") or "").strip()
    category = (form.get("category") or "General").strip()[:80]
    summary = (form.get("summary") or "").strip()
    description = (form.get("description") or "").strip()
    links_raw = (form.get("links") or "").strip()
    proposed = (form.get("proposed_options") or "").strip()
    fdict = {"title": title, "category": category, "summary": summary, "description": description, "links": links_raw, "proposed_options": proposed}
    if not title or len(title) > 200 or not description or len(description) > 20000 or len(summary) > 600:
        return templates.TemplateResponse(request, "submit.html", ctx(request, db, error="Check field lengths: title required (<=200), description required (<=20000), summary <=600.", form=fdict, max_mb=settings.MAX_UPLOAD_MB, site_name=site_name(db)), status_code=400)
    links = []
    for line in links_raw.splitlines():
        line = line.strip()
        if not line:
            continue
        if not URL_RE.match(line):
            return templates.TemplateResponse(request, "submit.html", ctx(request, db, error=f"Invalid URL: {line[:80]}", form=fdict, max_mb=settings.MAX_UPLOAD_MB, site_name=site_name(db)), status_code=400)
        links.append(line)
    # attachment
    att_path, att_name = "", ""
    upload = form.get("attachment")
    if upload is not None and hasattr(upload, "filename") and upload.filename:
        ext = os.path.splitext(upload.filename)[1].lower()
        if ext not in ALLOWED:
            return templates.TemplateResponse(request, "submit.html", ctx(request, db, error=f"File type not allowed: {ext}", form=fdict, max_mb=settings.MAX_UPLOAD_MB, site_name=site_name(db)), status_code=400)
        data = await upload.read()
        if len(data) > settings.MAX_UPLOAD_MB * 1024 * 1024:
            return templates.TemplateResponse(request, "submit.html", ctx(request, db, error="File too large.", form=fdict, max_mb=settings.MAX_UPLOAD_MB, site_name=site_name(db)), status_code=400)
        # basic malicious content guard: reject executables / html with scripts
        head = data[:4096].lower()
        if ext in (".html", ".htm", ".svg") or b"<script" in head or data[:2] == b"MZ":
            return templates.TemplateResponse(request, "submit.html", ctx(request, db, error="File content not allowed.", form=fdict, max_mb=settings.MAX_UPLOAD_MB, site_name=site_name(db)), status_code=400)
        import uuid
        os.makedirs(settings.UPLOAD_DIR, exist_ok=True)
        safe = re.sub(r"[^A-Za-z0-9._-]", "_", os.path.basename(upload.filename))[:100]
        att_name = safe
        att_path = os.path.join(settings.UPLOAD_DIR, f"{uuid.uuid4().hex}_{safe}")
        with open(att_path, "wb") as f:
            f.write(data)
    s = Submission(title=title, category=category or "General", summary=summary, description=description,
                   links="\n".join(links), proposed_options=proposed, status="PENDING_REVIEW",
                   submitter_id=user.id, attachment_path=att_path, attachment_name=att_name)
    db.add(s); db.commit()
    audit(db, user.id, "submission_created", s.id, title[:200])
    fire_and_forget("submission_needs_review", f"sub-created-{s.id}",
                    {"event_id": "", "event_type": "submission_needs_review", "submission_id": s.id,
                     "title": title, "category": s.category, "status": "PENDING_REVIEW",
                     "submitted_at": datetime.now(timezone.utc).isoformat(),
                     "submitter_type": user.kind,
                     "admin_url": f"{settings.APP_URL}/admin/submissions/{s.id}"})
    return templates.TemplateResponse(request, "submit.html", ctx(request, db, form={}, max_mb=settings.MAX_UPLOAD_MB, flash=f"Proposal submitted and awaiting review (ID {s.id}).", site_name=site_name(db)))

@router.get("/my/submissions", response_class=HTMLResponse)
def my_subs(request: Request, db: DBSession = Depends(get_db)):
    from app.auth_sessions import COOKIE, get_current_user
    user, _ = get_current_user(db, request.cookies.get(COOKIE))
    if not user:
        return RedirectResponse("/login", status_code=303)
    subs = db.query(Submission).filter_by(submitter_id=user.id).order_by(Submission.created_at.desc()).all()
    return templates.TemplateResponse(request, "my_submissions.html", ctx(request, db, subs=subs, site_name=site_name(db)))

@router.post("/submissions/{sid}/edit")
async def submit_edit(sid: str, request: Request, db: DBSession = Depends(get_db)):
    user, sess = get_current_user(db, request.cookies.get(COOKIE))
    if not user:
        return RedirectResponse("/login", status_code=303)
    form = await request.form()
    if not check_csrf(sess, form.get("csrf_token")):
        return templates.TemplateResponse(request, "error.html", ctx(request, db, code=403, message="Invalid session token.", site_name=site_name(db)), status_code=403)
    s = db.get(Submission, sid)
    if not s or s.submitter_id != user.id:
        return templates.TemplateResponse(request, "error.html", ctx(request, db, code=404, message="Not found", site_name=site_name(db)), status_code=404)
    if s.status not in ("PENDING_REVIEW", "CHANGES_REQUESTED", "APPROVED", "PUBLISHED"):
        return templates.TemplateResponse(request, "error.html", ctx(request, db, code=400, message="This submission can no longer be edited.", site_name=site_name(db)), status_code=400)
    title = (form.get("title") or "").strip()
    description = (form.get("description") or "").strip()
    if not title or len(title) > 200 or not description or len(description) > 20000:
        return templates.TemplateResponse(request, "error.html", ctx(request, db, code=400, message="Invalid fields.", site_name=site_name(db)), status_code=400)
    db.add(SubmissionRevision(submission_id=sid, title=s.title, summary=s.summary, description=s.description, edited_by=user.id))
    s.title = title
    s.summary = (form.get("summary") or "").strip()[:600]
    s.description = description
    if s.status in ("APPROVED", "PUBLISHED"):
        s.status = "PENDING_REVIEW"  # material edits after approval return to review
    s.updated_at = datetime.now(timezone.utc)
    db.commit()
    audit(db, user.id, "submission_edited", sid, title[:200])
    return RedirectResponse("/my/submissions", status_code=303)
