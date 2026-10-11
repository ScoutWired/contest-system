"""Application entry point."""
import os
from fastapi import FastAPI, Request
from fastapi.responses import HTMLResponse
from fastapi.staticfiles import StaticFiles
from app.database import engine, SessionLocal, Base
from app import models  # noqa: F401 — register tables
from app.permissions import seed_role_configs
from app.models import AppSetting

app = FastAPI(title="ScoutWired Portal")

os.makedirs("./data", exist_ok=True)
Base.metadata.create_all(bind=engine)
db = SessionLocal()
try:
    seed_role_configs(db)
    if not db.get(AppSetting, "site_name"):
        db.add(AppSetting(key="site_name", value="ScoutWired Portal"))
        db.commit()
finally:
    db.close()

app.mount("/static", StaticFiles(directory="app/static"), name="static")

from app.routes_public import router as public_router
from app.routes_auth import router as auth_router
from app.routes_submit import router as submit_router
from app.routes_polls import router as polls_router
from app.routes_admin import router as admin_router
from app.routes_setup import router as setup_router

app.include_router(public_router)
app.include_router(auth_router)
app.include_router(submit_router)
app.include_router(polls_router)
app.include_router(admin_router)
app.include_router(setup_router)

@app.middleware("http")
async def security_headers(request: Request, call_next):
    resp = await call_next(request)
    resp.headers["X-Content-Type-Options"] = "nosniff"
    resp.headers["X-Frame-Options"] = "DENY"
    resp.headers["Referrer-Policy"] = "no-referrer"
    return resp

@app.exception_handler(404)
async def not_found(request: Request, exc):
    from app.deps import templates, site_name
    db = SessionLocal()
    try:
        from app.deps import ctx
        return templates.TemplateResponse(request, "error.html", ctx(request, db, code=404, message="Not found", site_name=site_name(db)), status_code=404)
    finally:
        db.close()
