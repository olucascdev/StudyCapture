from __future__ import annotations

import os
import secrets
from dataclasses import dataclass
from pathlib import Path

DEFAULT_VAULT_PATH = "/home/olucasdev/Documentos/Obsidian Vault"


def _env_bool(name: str, default: bool = False) -> bool:
    return os.getenv(name, str(default)).lower() in {"1", "true", "yes", "on"}


@dataclass(frozen=True)
class Settings:
    data_dir: Path
    vault_path: Path
    groq_api_key: str | None
    extension_id: str | None
    local_token: str
    host: str = "127.0.0.1"
    port: int = 8765
    groq_model: str = "whisper-large-v3-turbo"
    groq_mock: bool = False
    max_upload_bytes: int = 4 * 1024 * 1024
    max_backlog_bytes: int = 512 * 1024 * 1024

    @classmethod
    def from_env(cls) -> "Settings":
        data_dir = Path(os.getenv("STUDYCAPTURE_DATA_DIR", "~/.local/share/studycapture")).expanduser()
        vault = Path(os.getenv("STUDYCAPTURE_VAULT", DEFAULT_VAULT_PATH)).expanduser()
        token = os.getenv("STUDYCAPTURE_TOKEN", "")
        return cls(
            data_dir=data_dir,
            vault_path=vault,
            groq_api_key=os.getenv("GROQ_API_KEY") or None,
            extension_id=os.getenv("STUDYCAPTURE_EXTENSION_ID") or None,
            local_token=token,
            host=os.getenv("STUDYCAPTURE_HOST", "127.0.0.1"),
            port=int(os.getenv("STUDYCAPTURE_PORT", "8765")),
            groq_model=os.getenv("GROQ_MODEL", "whisper-large-v3-turbo"),
            groq_mock=_env_bool("GROQ_MOCK"),
        )

    def ensure_directories(self) -> None:
        self.data_dir.mkdir(parents=True, exist_ok=True)
        (self.data_dir / "sessions").mkdir(exist_ok=True)
        self.vault_path.mkdir(parents=True, exist_ok=True)


def generate_token() -> str:
    return secrets.token_urlsafe(32)
