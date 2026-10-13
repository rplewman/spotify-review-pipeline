"""Serving API + static dashboard. M0: hello-world that reads one row from Neon."""
import os

import psycopg
from dotenv import load_dotenv
from fastapi import FastAPI

load_dotenv(".env")
app = FastAPI(title="Spotify Review Pipeline")


@app.get("/api/health")
def health():
    url = os.environ.get("DATABASE_URL")
    if not url:
        return {"ok": False, "error": "DATABASE_URL is not set"}
    try:
        with psycopg.connect(url, connect_timeout=10) as conn:
            row = conn.execute("select msg from hello where id = 1").fetchone()
    except psycopg.Error as e:
        return {"ok": False, "error": type(e).__name__}  # class name only; never echo the URL
    return {"ok": True, "db": row[0] if row else None}
