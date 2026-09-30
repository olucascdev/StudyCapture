from __future__ import annotations

import json
import logging
import threading
from datetime import UTC, datetime, timedelta
from pathlib import Path

from .audio import SAMPLE_RATE, wav_to_pcm
from .config import Settings
from .db import Database
from .markdown import build_markdown, write_note
from .transcriber import RetryAfterError, Transcriber

log = logging.getLogger(__name__)


class WaitingForBlocks(RuntimeError):
    """A finish request arrived before every expected block was uploaded."""


def now() -> str:
    return datetime.now(UTC).isoformat()


class TaskQueue:
    def __init__(self, db: Database, settings: Settings):
        self.db = db
        self.settings = settings
        self.transcriber = Transcriber(settings)
        self._stop = threading.Event()
        self._thread: threading.Thread | None = None

    def start(self) -> None:
        self.recover()
        self._thread = threading.Thread(target=self._run, name="studycapture-queue", daemon=True)
        self._thread.start()

    def stop(self) -> None:
        self._stop.set()
        if self._thread:
            self._thread.join(timeout=2)

    def recover(self) -> None:
        with self.db.tx() as conn:
            conn.execute("UPDATE tasks SET status='aguardando' WHERE status='processando'")

    def enqueue(self, session_id: str) -> None:
        with self.db.tx() as conn:
            conn.execute(
                "INSERT INTO tasks(session_id, kind, next_run_at) VALUES(?, 'finalize', ?) "
                "ON CONFLICT(session_id, kind) DO UPDATE SET status='aguardando', next_run_at=excluded.next_run_at",
                (session_id, now()),
            )

    def _run(self) -> None:
        while not self._stop.wait(0.4):
            task = self._claim()
            if not task:
                continue
            try:
                self._process(task["session_id"])
                self._complete(task["id"])
            except WaitingForBlocks:
                with self.db.tx() as conn:
                    conn.execute(
                        "UPDATE tasks SET status='aguardando', next_run_at=? WHERE id=?",
                        ((datetime.now(UTC) + timedelta(seconds=2)).isoformat(), task["id"]),
                    )
            except RetryAfterError as exc:
                with self.db.tx() as conn:
                    conn.execute(
                        "UPDATE tasks SET status='aguardando', error=?, next_run_at=? WHERE id=?",
                        (str(exc), (datetime.now(UTC) + timedelta(seconds=exc.seconds)).isoformat(), task["id"]),
                    )
            except Exception as exc:  # keep the consumer alive after network/API errors
                log.warning("task %s failed: %s", task["id"], exc)
                self._fail_or_retry(task["id"], task["session_id"], str(exc), task["attempts"])

    def _claim(self):
        with self.db.tx() as conn:
            row = conn.execute(
                "SELECT * FROM tasks WHERE status='aguardando' AND next_run_at <= ? ORDER BY id LIMIT 1",
                (now(),),
            ).fetchone()
            if not row:
                return None
            conn.execute("UPDATE tasks SET status='processando', attempts=attempts+1 WHERE id=?", (row["id"],))
            return dict(row)

    def _complete(self, task_id: int) -> None:
        with self.db.tx() as conn:
            conn.execute("UPDATE tasks SET status='concluída' WHERE id=?", (task_id,))

    def _fail_or_retry(self, task_id: int, session_id: str, error: str, attempts: int) -> None:
        delay = min(300, 2 ** min(attempts, 8))
        with self.db.tx() as conn:
            conn.execute(
                "UPDATE tasks SET status='aguardando', error=?, next_run_at=? WHERE id=?",
                (error[:1000], (datetime.now(UTC) + timedelta(seconds=delay)).isoformat(), task_id),
            )
            conn.execute("UPDATE sessions SET transcription_status='falhou', error=? WHERE id=?", (error[:1000], session_id))

    def _process(self, session_id: str) -> None:
        with self.db.tx() as conn:
            session = dict(conn.execute("SELECT * FROM sessions WHERE id=?", (session_id,)).fetchone())
            blocks = [dict(row) for row in conn.execute("SELECT * FROM blocks WHERE session_id=? ORDER BY sequence", (session_id,))]
        expected = session["expected_last_sequence"]
        if expected is None or len(blocks) < expected + 1 or [b["sequence"] for b in blocks] != list(range(expected + 1)):
            raise WaitingForBlocks("aguardando blocos de áudio")

        with self.db.tx() as conn:
            conn.execute("UPDATE sessions SET transcription_status='processando' WHERE id=?", (session_id,))
        pcm = b"".join(wav_to_pcm(Path(b["file_path"]).read_bytes()) for b in blocks)
        total = session["total_samples"] or len(pcm) // 2
        segments: list[dict] = []
        window_size = 60 * SAMPLE_RATE
        overlap = 2 * SAMPLE_RATE
        for index, start in enumerate(range(0, total, window_size)):
            main_end = min(total, start + window_size)
            window_start = max(0, start - overlap)
            window_end = min(total, main_end + overlap)
            with self.db.tx() as conn:
                conn.execute(
                    "INSERT OR IGNORE INTO windows(session_id, window_index, start_sample, end_sample) VALUES(?,?,?,?)",
                    (session_id, index, start, main_end),
                )
                row = conn.execute("SELECT * FROM windows WHERE session_id=? AND window_index=?", (session_id, index)).fetchone()
            if row["status"] == "concluída":
                segments.extend(json.loads(row["segments_json"] or "[]"))
                continue
            transcription = self.transcriber.transcribe(pcm[window_start * 2:window_end * 2], session["language"])
            window_segments = segments_for_window(transcription.response, transcription.segments, window_start, start, main_end)
            with self.db.tx() as conn:
                conn.execute(
                    "UPDATE windows SET status='concluída', response_json=?, segments_json=? WHERE session_id=? AND window_index=?",
                    (json.dumps(transcription.response, ensure_ascii=False), json.dumps(window_segments, ensure_ascii=False), session_id, index),
            )
            segments.extend(window_segments)
        segments.sort(key=lambda item: item["start"])
        markdown = build_markdown({**session, "total_samples": total}, segments)
        note_path = session["note_path"] or write_note(self.settings.vault_path, session["folder"], session["title"], markdown)
        with self.db.tx() as conn:
            conn.execute(
                "UPDATE sessions SET status='concluída', transcription_status='concluída', note_path=?, finished_at=? WHERE id=?",
                (note_path, now(), session_id),
            )
        # A note is durable before temporary audio is removed; metadata and raw
        # Groq responses remain in SQLite for reconstruction and diagnostics.
        for block in blocks:
            try:
                Path(block["file_path"]).unlink(missing_ok=True)
            except OSError:
                log.warning("could not clean temporary audio for session %s", session_id)


def segments_for_window(response: dict, segments: list[dict], window_start: int, main_start: int, main_end: int) -> list[dict]:
    """Assign words to exactly one main window, preserving segment-shaped Markdown."""
    words = response.get("words") or []
    if not words:
        words = response.get("word_timestamps") or []
    if not words:
        result = []
        for segment in segments:
            absolute_start = window_start + int(float(segment.get("start", 0)) * SAMPLE_RATE)
            if main_start <= absolute_start < main_end or (main_start == 0 and absolute_start == 0):
                result.append({
                    "start": absolute_start / SAMPLE_RATE,
                    "end": (window_start + float(segment.get("end", segment.get("start", 0))) * SAMPLE_RATE) / SAMPLE_RATE,
                    "text": segment.get("text", ""),
                })
        return result

    assigned = []
    for word in words:
        start = window_start + float(word.get("start", 0)) * SAMPLE_RATE
        if main_start <= start < main_end or (main_start == 0 and start == 0):
            assigned.append((start / SAMPLE_RATE, (window_start + float(word.get("end", word.get("start", 0))) * SAMPLE_RATE) / SAMPLE_RATE, str(word.get("word", "")).strip()))
    if not assigned:
        return []
    # Keep the 60-second segment cadence while using word timestamps for ownership.
    output = []
    current: dict | None = None
    for start, end, word in assigned:
        if not word:
            continue
        if current is None or start - current["end"] > 2.0:
            if current:
                output.append(current)
            current = {"start": start, "end": end, "text": word}
        else:
            current["end"] = end
            current["text"] += f" {word}"
    if current:
        output.append(current)
    return output
