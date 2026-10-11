"""Vercel serverless entrypoint: exposes the FastAPI app as `app` (and `handler`).

All routes are rewritten here via vercel.json. DB migrations run at build
time (see vercel.json buildCommand); runtime never performs DDL.
"""
from app.main import app

handler = app
