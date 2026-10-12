"""Serving API + static dashboard. M0: hello-world that reads one row from Neon."""
import os

import psycopg
from dotenv import load_dotenv
from fastapi import FastAPI

load_dotenv(".env")
app = FastAPI(title="Spotify Review Pipeline")


@app.get("/api/health")
def health():
    with psycopg.connect(os.environ["DATABASE_URL"]) as conn:
        row = conn.execute("select msg from hello where id = 1").fetchone()
    return {"ok": True, "db": row[0] if row else None}
