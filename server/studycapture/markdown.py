from __future__ import annotations

import os
import tempfile
from datetime import datetime
from pathlib import Path
from urllib.parse import urlparse

from .security import safe_vault_path, sanitize_filename


def format_timestamp(seconds: float) -> str:
    total = max(0, int(seconds))
    hours, remainder = divmod(total, 3600)
    minutes, secs = divmod(remainder, 60)
    return f"{hours}:{minutes:02d}:{secs:02d}" if hours else f"{minutes:02d}:{secs:02d}"


def build_markdown(session: dict, segments: list[dict]) -> str:
    captured = datetime.fromisoformat(session["created_at"]).astimezone().isoformat(timespec="seconds")
    domain = urlparse(session["url"]).netloc
    duration = session["total_samples"] / 16_000
    lines = [
        "---",
        f"title: {session['title'].replace(chr(10), ' ')}",
        f"source: {domain}",
        f"url: {session['url']}",
        f"captured: {captured}",
        f"duration_seconds: {duration:.2f}",
        "---",
        "",
        f"# {session['title']}",
        "",
        "> Timestamps relativos ao início da captura.",
        "",
        "## Transcrição",
        "",
    ]
    for segment in segments:
        text = " ".join(str(segment.get("text", "")).split())
        if text:
            lines.append(f"**{format_timestamp(float(segment.get('start', 0)))}** {text}")
            lines.append("")
    if len(lines) == 14:
        lines.extend(["_Nenhuma fala detectada._", ""])
    return "\n".join(lines)


def write_note(vault: Path, folder: str, title: str, content: str) -> str:
    directory = safe_vault_path(vault, folder)
    stem = sanitize_filename(title)
    destination = directory / f"{stem}.md"
    counter = 2
    while destination.exists():
        destination = directory / f"{stem} ({counter}).md"
        counter += 1
    fd, temporary = tempfile.mkstemp(prefix=".studycapture-", suffix=".md", dir=directory)
    try:
        with os.fdopen(fd, "w", encoding="utf-8", newline="\n") as handle:
            handle.write(content)
            handle.flush()
            os.fsync(handle.fileno())
        if destination.exists():
            return write_note(vault, folder, title, content)
        os.replace(temporary, destination)
    finally:
        if os.path.exists(temporary):
            os.unlink(temporary)
    return str(destination.relative_to(vault.resolve()))
