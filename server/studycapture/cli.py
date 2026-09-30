from __future__ import annotations

import argparse
import getpass
import os
import shlex
import shutil
import subprocess
from pathlib import Path

from .config import DEFAULT_VAULT_PATH, generate_token


SERVICE = """[Unit]
Description=StudyCapture local transcription server
After=network-online.target

[Service]
Type=simple
WorkingDirectory={project}
EnvironmentFile={config}
ExecStart={python} -m uvicorn server.studycapture.main:app --host 127.0.0.1 --port 8765
Restart=on-failure
RestartSec=3

[Install]
WantedBy=default.target
"""

BROWSER_NAMES = ("brave-browser", "brave", "chromium", "chromium-browser")


def install_browser_autostart(project: Path) -> None:
    extension_dir = project / "extension" / "dist"
    if not (extension_dir / "manifest.json").exists():
        print(f"Extensão ainda não compilada; autostart do Brave não instalado: {extension_dir}")
        return
    browser = next((shutil.which(name) for name in BROWSER_NAMES if shutil.which(name)), None)
    if not browser:
        print("Brave não encontrado no PATH; servidor foi configurado, mas o autostart do navegador não foi instalado.")
        return
    autostart_dir = Path(os.getenv("XDG_CONFIG_HOME", Path.home() / ".config")) / "autostart"
    autostart_dir.mkdir(parents=True, exist_ok=True)
    desktop_file = autostart_dir / "studycapture-brave.desktop"
    desktop_file.write_text(
        "[Desktop Entry]\n"
        "Type=Application\n"
        "Name=StudyCapture — Brave\n"
        f"Exec={browser} --no-startup-window --load-extension={extension_dir}\n"
        "X-GNOME-Autostart-enabled=true\n"
        "StartupNotify=false\n",
        encoding="utf-8",
    )
    desktop_file.chmod(0o600)
    print(f"Autostart do Brave instalado em {desktop_file}")


def configure(args: argparse.Namespace) -> None:
    config_dir = Path(args.config_dir).expanduser()
    config_dir.mkdir(parents=True, exist_ok=True)
    config = config_dir / "config.env"
    token = args.token or generate_token()
    values = {
        "STUDYCAPTURE_VAULT": str(Path(args.vault).expanduser().resolve()),
        "STUDYCAPTURE_DATA_DIR": str(Path(args.data_dir).expanduser().resolve()),
        "STUDYCAPTURE_EXTENSION_ID": args.extension_id,
        "STUDYCAPTURE_TOKEN": token,
        "GROQ_API_KEY": args.groq_key,
    }
    config.write_text("".join(f"{key}={shlex.quote(value)}\n" for key, value in values.items()), encoding="utf-8")
    config.chmod(0o600)
    if args.install_service:
        unit_dir = Path(os.getenv("XDG_CONFIG_HOME", Path.home() / ".config")) / "systemd/user"
        unit_dir.mkdir(parents=True, exist_ok=True)
        unit = unit_dir / "studycapture.service"
        unit.write_text(SERVICE.format(project=Path.cwd(), config=config, python=Path(os.sys.executable)), encoding="utf-8")
        unit.chmod(0o600)
        subprocess.run(["systemctl", "--user", "daemon-reload"], check=False)
        subprocess.run(["systemctl", "--user", "enable", "--now", "studycapture.service"], check=False)
        install_browser_autostart(Path.cwd())
        linger = subprocess.run(["loginctl", "enable-linger", getpass.getuser()], capture_output=True, text=True, check=False)
        if linger.returncode == 0:
            print("Inicialização do usuário habilitada para o boot")
        else:
            print('Para iniciar antes do login, execute: sudo loginctl enable-linger "$USER"')
        print(f"Serviço instalado em {unit}")
    print(f"Configuração salva em {config}")
    print(f"Token para a extensão: {token}")


def main() -> None:
    parser = argparse.ArgumentParser(prog="studycapture")
    sub = parser.add_subparsers(dest="command", required=True)
    command = sub.add_parser("configure")
    command.add_argument("--vault", default=DEFAULT_VAULT_PATH)
    command.add_argument("--groq-key", required=True)
    command.add_argument("--extension-id", required=True)
    command.add_argument("--data-dir", default="~/.local/share/studycapture")
    command.add_argument("--config-dir", default="~/.config/studycapture")
    command.add_argument("--token")
    command.add_argument("--install-service", action="store_true", default=True)
    command.set_defaults(func=configure)
    args = parser.parse_args()
    args.func(args)


if __name__ == "__main__":
    main()
