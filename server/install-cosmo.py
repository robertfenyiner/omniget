#!/usr/bin/env python3
"""Add an overlay to the existing Cosmo bot, preserving its original image/compose."""
import argparse
import json
import os
from pathlib import Path
import shutil
import secrets
import subprocess
import tempfile
import time
import urllib.parse

from auth import AuthStore
from service_sessions import ServiceSessions
from telegram_bot import TelegramBot


def run(arguments, **options):
    return subprocess.run(arguments, check=True, **options)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--project", type=Path, default=Path("/opt/Downloader-Bot"))
    args = parser.parse_args()
    project = args.project.resolve()
    os.umask(0o077)
    config_root = Path.home() / ".config/omniget-web"
    original = project / "docker-compose.yml"
    if not original.is_file():
        raise SystemExit("No se encontró el Compose original.")
    env = {}
    for line in (project / ".env").read_text().splitlines():
        if "=" in line and not line.lstrip().startswith("#"):
            key, value = line.split("=", 1)
            env[key.strip()] = value.strip().strip('"').strip("'")
    if not env.get("BOT_TOKEN") or not env.get("ADMIN_ID"):
        raise SystemExit("Faltan BOT_TOKEN o ADMIN_ID en la configuración privada de Cosmo.")
    owner = int(env["ADMIN_ID"])
    if owner <= 0:
        raise SystemExit("La integración requiere el chat privado del administrador.")
    ip = subprocess.check_output(["sudo", "-n", "docker", "inspect", "downloader-telegram-bot-api", "--format",
                                  "{{range .NetworkSettings.Networks}}{{.IPAddress}}{{end}}"], text=True).strip()
    if not ip:
        raise SystemExit("No se pudo localizar la API local de Telegram.")
    telegram_path = config_root / "telegram.json"
    telegram = json.loads(telegram_path.read_text()) if telegram_path.exists() else {}
    telegram.update(token=env["BOT_TOKEN"], chat_id=owner, api_base=f"http://{ip}:8081")
    web_settings = {}
    if (config_root / "environment").exists():
        for line in (config_root / "environment").read_text().splitlines():
            if "=" in line and not line.lstrip().startswith("#"):
                key, value = line.split("=", 1)
                web_settings[key.strip()] = value.strip()
    host = web_settings.get("OMNIGET_WEB_BIND", "127.0.0.1")
    port = web_settings.get("OMNIGET_WEB_PORT", "8790")
    telegram.setdefault("web_url", f"http://{host}:{port}")
    telegram.setdefault("notifications", True)
    parsed = urllib.parse.urlsplit(telegram["web_url"])
    if parsed.scheme not in ("http", "https") or parsed.username or parsed.password:
        raise SystemExit("La URL web privada no es válida.")
    config_root.mkdir(parents=True, exist_ok=True, mode=0o700)
    AuthStore.write_private(telegram_path, json.dumps(telegram))
    telegram["username"] = TelegramBot(telegram_path).status(verify=True)["username"]
    AuthStore.write_private(telegram_path, json.dumps(telegram))
    bridge_path = config_root / "bot-bridge.json"
    bridge = json.loads(bridge_path.read_text()) if bridge_path.exists() else {"key": secrets.token_urlsafe(48)}
    AuthStore.write_private(bridge_path, json.dumps(bridge))
    export_path = config_root / "cosmo-bridge-export.json"
    AuthStore.write_private(export_path, json.dumps({"key": bridge["key"], "owner_id": owner, "web_url": telegram["web_url"]}))
    sessions = ServiceSessions(config_root / "services", Path.home() / ".local/share/omniget-web/runtime")
    for filename, domain in (("youtube.txt", "youtube.com"), ("instagram.txt", "instagram.com"), ("facebook.txt", "facebook.com")):
        cookie_file = project / "cookies" / filename
        if cookie_file.is_file():
            sessions.import_text(domain, cookie_file.read_text())
    sessions.prepare_runtime()
    source = project / "handlers/__init__.py"
    text = source.read_text()
    if "router.include_routers(\n" not in text:
        raise SystemExit("El registro de routers de Cosmo cambió; revisa la integración antes de instalar.")
    if "omniget.router" not in text:
        text = "from . import omniget\n" + text.replace("router.include_routers(\n", "router.include_routers(\n    omniget.router,\n", 1)
    with tempfile.TemporaryDirectory(prefix="omniget-cosmo-") as context:
        context = Path(context)
        (context / "handlers_init.py").write_text(text)
        shutil.copyfile(Path(__file__).with_name("cosmo_integration.py"), context / "omniget.py")
        (context / "Dockerfile").write_text("FROM downloader-bot-robert:private-v2\n"
            "COPY --chown=appuser:appgroup omniget.py /app/handlers/omniget.py\n"
            "COPY --chown=appuser:appgroup handlers_init.py /app/handlers/__init__.py\n")
        run(["sudo", "-n", "docker", "build", "--pull=false", "-t", "downloader-bot-robert:omniget-v1", str(context)])
    uid = subprocess.check_output(["sudo", "-n", "docker", "exec", "downloader-bot", "id", "-u", "appuser"], text=True).strip()
    gid = subprocess.check_output(["sudo", "-n", "docker", "exec", "downloader-bot", "id", "-g", "appuser"], text=True).strip()
    private = project / "private"
    run(["sudo", "-n", "install", "-d", "-m", "700", str(private)])
    run(["sudo", "-n", "install", "-o", uid, "-g", gid, "-m", "600", str(export_path), str(private / "omniget-bridge.json")])
    override = project / "docker-compose.omniget.yml"
    if override.exists():
        shutil.copy2(override, override.with_suffix(f".yml.backup-{time.time_ns()}"))
    bridge_dir = Path.home() / ".local/share/omniget-web/bridge"
    if not (bridge_dir / "bridge.sock").exists():
        run(["systemctl", "--user", "restart", "omniget-web"])
        for _ in range(20):
            if (bridge_dir / "bridge.sock").exists():
                break
            time.sleep(.1)
        else:
            raise SystemExit("No apareció el socket de OmniGet tras reiniciar el servicio.")
    override.write_text(f'''services:
  downloader-bot:
    image: downloader-bot-robert:omniget-v1
    volumes:
      - {bridge_dir}:/run/omniget:ro
      - {private / 'omniget-bridge.json'}:/app/omniget-bridge.json:ro
''')
    excludes = project / ".git/info/exclude"
    with excludes.open("a") as file:
        file.write("\n/private/\n/docker-compose.omniget.yml*\n")
    # The original project uses COPY . . in its base image. Protect both the
    # new bridge secret and existing environment backups in future rebuilds.
    dockerignore = project / ".dockerignore"
    ignored = dockerignore.read_text() if dockerignore.exists() else ""
    for pattern in ("/private/", "/.env*", "/docker-compose.omniget.yml*"):
        if pattern not in ignored.splitlines():
            ignored += "\n" + pattern + "\n"
    dockerignore.write_text(ignored)
    run(["sudo", "-n", "docker", "compose", "--project-name", "downloader-bot", "-f", str(original), "-f", str(override),
         "up", "-d", "--no-deps", "--no-build", "downloader-bot"], cwd=project)
    for _ in range(40):
        status = subprocess.check_output(["sudo", "-n", "docker", "inspect", "downloader-bot", "--format", "{{.State.Health.Status}}"], text=True).strip()
        if status == "healthy":
            run(["sudo", "-n", "docker", "exec", "--user", "appuser", "downloader-bot", "python", "-c",
                 "import asyncio,json; from handlers.omniget import request,CONFIG; "
                 "result=asyncio.run(request(json.loads(CONFIG.read_text()),'/status')); "
                 "assert result['ok']; print('Puente local comprobado desde el usuario del bot.')"])
            print("Cosmo sigue sano y la integración OmniGet está desplegada.")
            print("Comandos: /omniget, /omniget_estado, /omniget_descargar URL, /omniget_cancelar ID")
            return
        time.sleep(2)
    raise SystemExit("La integración se desplegó, pero el chequeo de salud no confirmó healthy. Revisa el contenedor antes de continuar.")


if __name__ == "__main__":
    main()
