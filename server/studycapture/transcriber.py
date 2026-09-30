from __future__ import annotations

import json
from email.utils import parsedate_to_datetime
from datetime import UTC, datetime
from dataclasses import dataclass
from typing import Any

from .audio import SAMPLE_RATE, pcm_to_wav
from .config import Settings


@dataclass
class Transcription:
    response: dict[str, Any]
    segments: list[dict[str, Any]]


class RetryAfterError(RuntimeError):
    def __init__(self, seconds: int):
        super().__init__(f"Groq solicitou nova tentativa em {seconds}s")
        self.seconds = seconds


class Transcriber:
    def __init__(self, settings: Settings):
        self.settings = settings
        self.client = None
        if settings.groq_api_key and not settings.groq_mock:
            from groq import Groq

            self.client = Groq(api_key=settings.groq_api_key)

    def transcribe(self, pcm: bytes, language: str | None) -> Transcription:
        if self.settings.groq_mock:
            return Transcription(
                response={"text": "[transcrição simulada]", "segments": [], "words": []},
                segments=[{"start": 0.0, "end": len(pcm) / 2 / SAMPLE_RATE, "text": "[transcrição simulada]"}],
            )
        if not self.client:
            raise RuntimeError("GROQ_API_KEY não configurada")
        try:
            result = self.client.audio.transcriptions.create(
                file=("capture.wav", pcm_to_wav(pcm)),
                model=self.settings.groq_model,
                response_format="verbose_json",
                timestamp_granularities=["segment", "word"],
                **({"language": language} if language in {"pt", "en"} else {}),
            )
        except Exception as exc:
            if getattr(exc, "status_code", None) == 429:
                headers = getattr(getattr(exc, "response", None), "headers", {}) or {}
                raw_retry_after = headers.get("retry-after", "")
                try:
                    seconds = max(1, int(float(raw_retry_after)))
                except (TypeError, ValueError):
                    try:
                        seconds = max(1, int((parsedate_to_datetime(raw_retry_after).astimezone(UTC) - datetime.now(UTC)).total_seconds()))
                    except (TypeError, ValueError, OverflowError):
                        seconds = 30
                raise RetryAfterError(seconds) from exc
            raise
        if hasattr(result, "model_dump"):
            payload = result.model_dump()
        elif hasattr(result, "dict"):
            payload = result.dict()
        elif isinstance(result, dict):
            payload = result
        else:
            payload = json.loads(result.model_dump_json())
        return Transcription(response=payload, segments=list(payload.get("segments") or []))
