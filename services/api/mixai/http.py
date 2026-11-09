"""Private single-user HTTP pilot. See README for the SaaS deployment boundary."""
import asyncio
import json
import os
import sqlite3
import tempfile
from contextlib import closing
from pathlib import Path
from uuid import UUID, uuid4

from fastapi import FastAPI, HTTPException, Request
from starlette.concurrency import run_in_threadpool
from starlette.middleware.trustedhost import TrustedHostMiddleware

from .dsp import AudioError, MAX_BYTES, analyze

app = FastAPI(title="MixAI Pilot", version="0.1.0")
app.add_middleware(TrustedHostMiddleware, allowed_hosts=["127.0.0.1", "localhost"])
slots = asyncio.Semaphore(2)


def db():
    location = Path(os.getenv("MIXAI_STATE", "./state"))
    location.mkdir(mode=0o700, parents=True, exist_ok=True)
    conn = sqlite3.connect(location / "pilot.sqlite3", timeout=10)
    conn.row_factory = sqlite3.Row
    conn.execute("""
        CREATE TABLE IF NOT EXISTS reports (
          id TEXT PRIMARY KEY,
          created_at TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%fZ','now')),
          report TEXT NOT NULL
        )
    """)
    return conn


@app.get("/healthz")
def health():
    return {"status": "ok"}


def persist(report):
    report_id = str(uuid4())
    with closing(db()) as conn, conn:
        conn.execute("INSERT INTO reports (id, report) VALUES (?, ?)",
                     (report_id, json.dumps(report, allow_nan=False)))
    return {"id": report_id, "report": report}


@app.post("/v1/local/reports", status_code=201)
async def create_report(request: Request):
    if request.headers.get("content-type") != "application/octet-stream":
        raise HTTPException(415, "Envía el archivo como application/octet-stream.")
    declared = request.headers.get("content-length")
    if declared is not None and (not declared.isdecimal() or len(declared) > 10):
        raise HTTPException(400, "Content-Length inválido.")
    if declared is not None and int(declared) > MAX_BYTES:
        raise HTTPException(413, "El archivo excede 80 MiB.")
    if slots.locked():
        raise HTTPException(503, "Procesamiento ocupado.", headers={"Retry-After": "5"})
    async with slots:
        with tempfile.TemporaryDirectory(prefix="mixai-upload-") as directory:
            path = Path(directory) / "audio"
            size = 0
            try:
                async with asyncio.timeout(120):
                    with path.open("wb") as output:
                        async for chunk in request.stream():
                            size += len(chunk)
                            if size > MAX_BYTES:
                                raise HTTPException(413, "El archivo excede 80 MiB.")
                            if chunk:
                                await run_in_threadpool(output.write, chunk)
            except TimeoutError as exc:
                raise HTTPException(408, "La subida excedió el tiempo permitido.") from exc
            try:
                report = await run_in_threadpool(analyze, path)
            except AudioError as exc:
                raise HTTPException(422, str(exc)) from exc
        return await run_in_threadpool(persist, report)


@app.get("/v1/local/reports")
def list_reports():
    with closing(db()) as conn:
        rows = conn.execute("""
            SELECT id, created_at, json_extract(report, '$.format') AS format,
              json_extract(report, '$.duration_s') AS duration_s
            FROM reports ORDER BY created_at DESC, id DESC LIMIT 50
        """).fetchall()
    return {"items": [dict(row) for row in rows]}


@app.get("/v1/local/reports/{report_id}")
def get_report(report_id: UUID):
    with closing(db()) as conn:
        row = conn.execute("SELECT report FROM reports WHERE id = ?", (str(report_id),)).fetchone()
    if row is None:
        raise HTTPException(404, "Reporte inexistente.")
    return {"id": str(report_id), "report": json.loads(row["report"])}


@app.delete("/v1/local/reports/{report_id}", status_code=204)
def delete_report(report_id: UUID):
    with closing(db()) as conn, conn:
        conn.execute("DELETE FROM reports WHERE id = ?", (str(report_id),))
