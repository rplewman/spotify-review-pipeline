"""Serving API + static dashboard. M0: hello-world that reads one row from Neon."""
import os
import re

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
        # Redact the password before echoing; also report shape problems common with pasted URLs.
        msg = re.sub(r"npg_\w+|://[^@\s]*@", "<redacted>", str(e).strip().splitlines()[0] if str(e).strip() else "")
        return {"ok": False, "error": type(e).__name__, "detail": msg,
                "url_starts_ok": url.startswith("postgresql://"), "url_has_quotes_or_spaces": bool(re.search(r"[\"'\s]", url)),
                "url_has_sslmode": "sslmode=require" in url}
    return {"ok": True, "db": row[0] if row else None}
