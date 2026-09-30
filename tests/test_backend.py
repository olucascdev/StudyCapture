from __future__ import annotations

import hashlib
from dataclasses import replace

import httpx
import pytest

from server.studycapture.audio import pcm_to_wav
from server.studycapture.db import Database
from server.studycapture.markdown import build_markdown, format_timestamp, write_note
from server.studycapture.security import safe_vault_path, sanitize_filename, validate_relative_folder
from server.studycapture.queue import TaskQueue
from server.studycapture.queue import segments_for_window


def test_audio_timestamp_and_filename_rules(tmp_path):
    assert format_timestamp(0) == "00:00"
    assert format_timestamp(61) == "01:01"
    assert format_timestamp(3601) == "1:00:01"
    assert sanitize_filename(" Aula: introdução / 2026 ") == "Aula- introdução - 2026"
    assert validate_relative_folder("Aulas/2026") == "Aulas/2026"
    with pytest.raises(ValueError):
        validate_relative_folder("../fora")
    with pytest.raises(ValueError):
        validate_relative_folder(".segredo")


def test_markdown_and_collision_are_atomic(tmp_path):
    vault = tmp_path / "vault"
    (vault / "Aulas").mkdir(parents=True)
    session = {"title": "Aula de teste", "url": "https://example.com/aula", "created_at": "2026-09-30T12:00:00+00:00", "total_samples": 16_000}
    markdown = build_markdown(session, [{"start": 61, "text": "Olá mundo"}])
    assert "Timestamps relativos ao início da captura" in markdown
    assert "**01:01** Olá mundo" in markdown
    assert write_note(vault, "Aulas", session["title"], markdown) == "Aulas/Aula de teste.md"
    assert write_note(vault, "Aulas", session["title"], markdown) == "Aulas/Aula de teste (2).md"
    assert not list((vault / "Aulas").glob(".studycapture-*"))


@pytest.mark.asyncio
async def test_api_auth_upload_idempotency_and_conflict(tmp_path, monkeypatch):
    monkeypatch.setenv("STUDYCAPTURE_DATA_DIR", str(tmp_path / "initial-data"))
    monkeypatch.setenv("STUDYCAPTURE_VAULT", str(tmp_path / "initial-vault"))
    monkeypatch.setenv("STUDYCAPTURE_TOKEN", "initial-token")
    monkeypatch.setenv("GROQ_MOCK", "true")
    import server.studycapture.main as main

    vault = tmp_path / "vault"
    vault.mkdir()
    settings = replace(main.settings, data_dir=tmp_path / "data", vault_path=vault, local_token="test-token", groq_api_key=None, groq_mock=True)
    settings.ensure_directories()
    database = Database(settings.data_dir / "studycapture.sqlite3")
    queue = TaskQueue(database, settings)
    monkeypatch.setattr(main, "settings", settings)
    monkeypatch.setattr(main, "db", database)
    monkeypatch.setattr(main, "queue", queue)
    wav = pcm_to_wav(b"\0\0" * 160)
    headers = {"X-StudyCapture-Token": "test-token"}
    checksum = hashlib.sha256(wav).hexdigest()
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=main.app), base_url="http://test") as client:
        assert (await client.get("/api/v1/folders", headers=headers)).status_code == 200
        assert (await client.get("/api/v1/sessions")).status_code == 401
        created = await client.post("/api/v1/sessions", headers=headers, json={"title": "Teste", "url": "https://example.com", "folder": "", "language": "pt"})
        assert created.status_code == 201
        session_id = created.json()["id"]
        upload_headers = {**headers, "X-Position-Samples": "0", "X-Sample-Count": "160", "X-Checksum": checksum}
        first = await client.put(f"/api/v1/sessions/{session_id}/blocks/0", headers=upload_headers, content=wav)
        second = await client.put(f"/api/v1/sessions/{session_id}/blocks/0", headers=upload_headers, content=wav)
        assert first.status_code == 200 and not first.json()["idempotent"]
        assert second.status_code == 200 and second.json()["idempotent"]
        other = pcm_to_wav(b"\x01\0" * 160)
        conflict = await client.put(f"/api/v1/sessions/{session_id}/blocks/0", headers={**upload_headers, "X-Checksum": hashlib.sha256(other).hexdigest()}, content=other)
        assert conflict.status_code == 409


def test_symlink_vault_path_is_rejected(tmp_path):
    vault = tmp_path / "vault"
    vault.mkdir()
    target = tmp_path / "elsewhere"
    target.mkdir()
    (vault / "link").symlink_to(target, target_is_directory=True)
    with pytest.raises(ValueError):
        safe_vault_path(vault, "link")


def test_words_are_assigned_to_one_transcription_window():
    response = {"words": [{"start": 59.9, "end": 60.2, "word": "fronteira"}]}
    first = segments_for_window(response, [], 0, 0, 60 * 16_000)
    second_response = {"words": [{"start": 1.9, "end": 2.2, "word": "fronteira"}, {"start": 2.5, "end": 2.8, "word": "seguinte"}]}
    second = segments_for_window(second_response, [], 58 * 16_000, 60 * 16_000, 120 * 16_000)
    assert "fronteira" in first[0]["text"]
    assert second[0]["text"] == "seguinte"
