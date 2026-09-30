from __future__ import annotations

import hashlib
import os
import secrets
import time
import uuid
import wave
from contextlib import asynccontextmanager
from datetime import UTC, datetime
from pathlib import Path

from fastapi import Depends, FastAPI, Header, HTTPException, Request
from fastapi.responses import JSONResponse
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel, Field, field_validator

from .config import Settings
from .audio import wav_to_pcm
from .db import Database
from .queue import TaskQueue
from .security import extension_origin, safe_vault_path, validate_relative_folder, validate_url

settings = Settings.from_env()
settings.ensure_directories()
db = Database(settings.data_dir / "studycapture.sqlite3")
queue = TaskQueue(db, settings)


@asynccontextmanager
async def lifespan(_app: FastAPI):
    queue.start()
    try:
        yield
    finally:
        queue.stop()


app = FastAPI(title="StudyCapture", version="0.1.0", lifespan=lifespan)
origin = extension_origin(settings.extension_id)
app.add_middleware(
    CORSMiddleware,
    allow_origins=[origin] if origin else [],
    allow_credentials=False,
    allow_methods=["GET", "POST", "PUT", "OPTIONS"],
    allow_headers=["Content-Type", "X-StudyCapture-Token", "X-Position-Samples", "X-Sample-Count", "X-Checksum"],
)
_rate_buckets: dict[str, tuple[float, int]] = {}


@app.middleware("http")
async def local_rate_limit(request: Request, call_next):
    if request.url.path.endswith("/health"):
        return await call_next(request)
    now_monotonic = time.monotonic()
    client = request.client.host if request.client else "unknown"
    started, count = _rate_buckets.get(client, (now_monotonic, 0))
    if now_monotonic - started >= 60:
        started, count = now_monotonic, 0
    count += 1
    _rate_buckets[client] = (started, count)
    if count > 180:
        return JSONResponse({"detail": "Muitas requisições; tente novamente em instantes"}, status_code=429, headers={"Retry-After": "60"})
    return await call_next(request)


class CreateSession(BaseModel):
    title: str = Field(min_length=1, max_length=300)
    url: str = Field(min_length=1, max_length=4096)
    folder: str = Field(default="", max_length=500)
    language: str | None = Field(default=None)

    @field_validator("title")
    @classmethod
    def title_clean(cls, value: str) -> str:
        value = " ".join(value.split())
        if not value:
            raise ValueError("Título vazio")
        return value

    @field_validator("url")
    @classmethod
    def url_clean(cls, value: str) -> str:
        return validate_url(value)

    @field_validator("folder")
    @classmethod
    def folder_clean(cls, value: str) -> str:
        return validate_relative_folder(value)

    @field_validator("language")
    @classmethod
    def language_clean(cls, value: str | None) -> str | None:
        if value not in {None, "", "pt", "en"}:
            raise ValueError("Idioma deve ser automático, pt ou en")
        return value or None


class FinishSession(BaseModel):
    last_sequence: int = Field(ge=0)
    total_samples: int = Field(gt=0)


async def require_token(x_studycapture_token: str | None = Header(default=None)) -> None:
    if not settings.local_token or not x_studycapture_token or not secrets.compare_digest(x_studycapture_token, settings.local_token):
        raise HTTPException(status_code=401, detail="Token local inválido")


def session_or_404(session_id: str) -> dict:
    with db.tx() as conn:
        row = conn.execute("SELECT * FROM sessions WHERE id=?", (session_id,)).fetchone()
    if not row:
        raise HTTPException(status_code=404, detail="Sessão não encontrada")
    return dict(row)


@app.get("/api/v1/health")
async def health() -> dict:
    return {"ok": True, "configured": bool(settings.local_token and settings.vault_path.exists()), "groq": bool(settings.groq_api_key or settings.groq_mock)}


@app.get("/api/v1/folders")
async def folders(parent: str = "", _: None = Depends(require_token)) -> dict:
    try:
        directory = safe_vault_path(settings.vault_path, parent)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    items = sorted(
        entry.name for entry in directory.iterdir() if entry.is_dir() and not entry.name.startswith(".") and not entry.is_symlink()
    )
    return {"parent": parent, "folders": items}


@app.post("/api/v1/sessions", status_code=201)
async def create_session(payload: CreateSession, _: None = Depends(require_token)) -> dict:
    try:
        safe_vault_path(settings.vault_path, payload.folder)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    with db.tx() as conn:
        active = conn.execute("SELECT id FROM sessions WHERE status IN ('capturando','pausada','finalizando') LIMIT 1").fetchone()
    if active:
        raise HTTPException(status_code=409, detail="Já existe uma captura ativa")
    session_id = str(uuid.uuid4())
    with db.tx() as conn:
        conn.execute(
            "INSERT INTO sessions(id,title,url,folder,language,status,created_at) VALUES(?,?,?,?,?,?,?)",
            (session_id, payload.title, payload.url, payload.folder, payload.language, "capturando", datetime.now(UTC).isoformat()),
        )
    return {"id": session_id, "status": "capturando"}


@app.put("/api/v1/sessions/{session_id}/blocks/{sequence}")
async def upload_block(
    session_id: str,
    sequence: int,
    request: Request,
    x_position_samples: int = Header(ge=0),
    x_sample_count: int = Header(gt=0),
    x_checksum: str = Header(min_length=64, max_length=64),
    _: None = Depends(require_token),
) -> dict:
    session = session_or_404(session_id)
    if session["status"] not in {"capturando", "pausada", "interrompida", "finalizando"} or sequence < 0:
        raise HTTPException(status_code=409, detail="Sessão não aceita novos blocos")
    content_length = request.headers.get("content-length")
    if content_length and (not content_length.isdigit() or int(content_length) > settings.max_upload_bytes):
        raise HTTPException(status_code=413, detail="Bloco excede o limite")
    data = await request.body()
    if len(data) > settings.max_upload_bytes or len(data) < 44 or not data.startswith(b"RIFF"):
        raise HTTPException(status_code=400, detail="Upload WAV inválido")
    try:
        pcm = wav_to_pcm(data)
    except (ValueError, EOFError, wave.Error) as exc:
        raise HTTPException(status_code=400, detail="WAV deve ser mono PCM 16-bit a 16 kHz") from exc
    if len(pcm) // 2 != x_sample_count:
        raise HTTPException(status_code=400, detail="Quantidade de amostras não corresponde ao WAV")
    digest = hashlib.sha256(data).hexdigest()
    if not secrets.compare_digest(digest, x_checksum):
        raise HTTPException(status_code=400, detail="Checksum inválido")
    session_dir = settings.data_dir / "sessions" / session_id
    session_dir.mkdir(parents=True, exist_ok=True)
    path = session_dir / f"{sequence:08d}.wav"
    with db.tx() as conn:
        existing = conn.execute("SELECT checksum, file_path FROM blocks WHERE session_id=? AND sequence=?", (session_id, sequence)).fetchone()
        if existing:
            if existing["checksum"] != digest:
                raise HTTPException(status_code=409, detail="Sequência já existe com conteúdo diferente")
            return {"sequence": sequence, "stored": True, "idempotent": True}
        temporary = path.with_suffix(".tmp")
        temporary.write_bytes(data)
        os.replace(temporary, path)
        conn.execute(
            "INSERT INTO blocks(session_id,sequence,position_samples,sample_count,checksum,file_path,created_at) VALUES(?,?,?,?,?,?,?)",
            (session_id, sequence, x_position_samples, x_sample_count, digest, str(path), datetime.now(UTC).isoformat()),
        )
    return {"sequence": sequence, "stored": True, "idempotent": False}


@app.get("/api/v1/sessions/{session_id}")
async def get_session(session_id: str, _: None = Depends(require_token)) -> dict:
    session = session_or_404(session_id)
    with db.tx() as conn:
        blocks = conn.execute("SELECT COUNT(*) AS count, COALESCE(SUM(sample_count), 0) AS samples FROM blocks WHERE session_id=?", (session_id,)).fetchone()
        last_block = conn.execute("SELECT MAX(sequence) AS sequence FROM blocks WHERE session_id=?", (session_id,)).fetchone()
        windows = conn.execute("SELECT COUNT(*) AS count FROM windows WHERE session_id=? AND status='concluída'", (session_id,)).fetchone()
    return {**session, "block_count": blocks["count"], "last_sequence": last_block["sequence"], "received_samples": blocks["samples"], "completed_windows": windows["count"]}


@app.post("/api/v1/sessions/{session_id}/finish")
async def finish_session(session_id: str, payload: FinishSession, _: None = Depends(require_token)) -> dict:
    session_or_404(session_id)
    with db.tx() as conn:
        conn.execute(
            "UPDATE sessions SET status='finalizando', finished_at=?, expected_last_sequence=?, total_samples=? WHERE id=? AND status IN ('capturando','pausada','interrompida','finalizando')",
            (datetime.now(UTC).isoformat(), payload.last_sequence, payload.total_samples, session_id),
        )
    queue.enqueue(session_id)
    return {"id": session_id, "status": "finalizando"}


@app.post("/api/v1/sessions/{session_id}/pause")
async def pause_session(session_id: str, _: None = Depends(require_token)) -> dict:
    session_or_404(session_id)
    with db.tx() as conn:
        conn.execute("UPDATE sessions SET status='pausada' WHERE id=? AND status='capturando'", (session_id,))
    return {"id": session_id, "status": "pausada"}


@app.post("/api/v1/sessions/{session_id}/resume")
async def resume_session(session_id: str, _: None = Depends(require_token)) -> dict:
    session_or_404(session_id)
    with db.tx() as conn:
        conn.execute("UPDATE sessions SET status='capturando' WHERE id=? AND status='pausada'", (session_id,))
    return {"id": session_id, "status": "capturando"}


@app.post("/api/v1/sessions/{session_id}/retry")
async def retry_session(session_id: str, _: None = Depends(require_token)) -> dict:
    session_or_404(session_id)
    with db.tx() as conn:
        conn.execute("UPDATE sessions SET status='finalizando', transcription_status='aguardando', error=NULL WHERE id=?", (session_id,))
    queue.enqueue(session_id)
    return {"id": session_id, "status": "finalizando"}


@app.post("/api/v1/sessions/{session_id}/interrupt")
async def interrupt_session(session_id: str, _: None = Depends(require_token)) -> dict:
    session_or_404(session_id)
    with db.tx() as conn:
        conn.execute(
            "UPDATE sessions SET status='interrompida', transcription_status='aguardando', error=? WHERE id=? AND status IN ('capturando','pausada','finalizando')",
            ("Captura interrompida manualmente durante a recuperação.", session_id),
        )
    return {"id": session_id, "status": "interrompida"}


@app.get("/api/v1/sessions")
async def list_sessions(_: None = Depends(require_token)) -> dict:
    with db.tx() as conn:
        rows = conn.execute("SELECT * FROM sessions ORDER BY created_at DESC LIMIT 20").fetchall()
    return {"sessions": [dict(row) for row in rows]}
