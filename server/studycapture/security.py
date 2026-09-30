from __future__ import annotations

import re
from pathlib import Path
from urllib.parse import urlparse


EXTENSION_ID = re.compile(r"^[a-p]{32}$")


def validate_relative_folder(value: str) -> str:
    value = value.strip().replace("\\", "/")
    if value in {"", "."}:
        return ""
    path = Path(value)
    if path.is_absolute() or any(part in {"", ".", ".."} for part in path.parts):
        raise ValueError("A pasta deve ser um caminho relativo sem travessia")
    if any(part.startswith(".") for part in path.parts):
        raise ValueError("Pastas ocultas não são permitidas")
    return "/".join(path.parts)


def validate_url(value: str) -> str:
    parsed = urlparse(value)
    if any(ord(char) < 32 for char in value) or parsed.scheme not in {"http", "https"} or not parsed.netloc or len(value) > 4096:
        raise ValueError("URL inválida")
    return value


def safe_vault_path(vault: Path, relative: str) -> Path:
    relative = validate_relative_folder(relative)
    vault = vault.expanduser().resolve()
    candidate = (vault / relative).resolve()
    if candidate != vault and vault not in candidate.parents:
        raise ValueError("Caminho fora do vault")
    current = vault
    for part in Path(relative).parts:
        current = current / part
        if current.is_symlink():
            raise ValueError("Links simbólicos não são permitidos no caminho do vault")
    if not candidate.exists() or not candidate.is_dir():
        raise ValueError("Pasta do vault não encontrada")
    return candidate


def sanitize_filename(title: str) -> str:
    title = " ".join(title.strip().split())
    title = re.sub(r"[\x00-\x1f<>:\"/\\|?*]", "-", title)
    title = title.strip(" .")[:120]
    return title or "Captura sem título"


def extension_origin(extension_id: str | None) -> str | None:
    return f"chrome-extension://{extension_id}" if extension_id and EXTENSION_ID.fullmatch(extension_id) else None
