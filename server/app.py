#!/usr/bin/env python3
"""Private, single-user web queue for omniget-cli (Python standard library)."""
import hmac
import ipaddress
import json
import mimetypes
import os
from pathlib import Path
import queue
import re
import shutil
import signal
import socket
import subprocess
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import unquote, urlsplit
import uuid

STATIC = Path(__file__).with_name("static")
DEFAULT_HOSTS = (
    "youtube.com", "youtu.be", "vimeo.com", "soundcloud.com", "bilibili.com",
    "b23.tv", "instagram.com", "tiktok.com", "x.com", "twitter.com",
    "reddit.com", "twitch.tv", "pinterest.com", "raw.githubusercontent.com",
)


def validate_url(value, allowed_hosts):
    if not isinstance(value, str) or len(value) > 4096 or any(ord(c) < 33 for c in value):
        raise ValueError("La URL no es válida.")
    try:
        url = urlsplit(value)
        port = url.port
    except ValueError as exc:
        raise ValueError("La URL no es válida.") from exc
    host = (url.hostname or "").lower()
    if url.scheme != "https" or not host or url.username is not None or url.password is not None:
        raise ValueError("Usa una URL HTTPS sin credenciales.")
    if port not in (None, 443):
        raise ValueError("Solo se permite el puerto HTTPS estándar.")
    if not any(host == domain or host.endswith("." + domain) for domain in allowed_hosts):
        raise ValueError("Este dominio no está habilitado en el servidor.")
    try:
        addresses = socket.getaddrinfo(host, 443, type=socket.SOCK_STREAM)
    except OSError as exc:
        raise ValueError("No se pudo resolver el dominio.") from exc
    if not addresses or any(not ipaddress.ip_address(addr[4][0]).is_global for addr in addresses):
        raise ValueError("No se permiten destinos de red privada.")
    return value


class DownloadQueue:
    def __init__(self, data_dir, cli, allowed_hosts=DEFAULT_HOSTS, timeout=2700, start_worker=True):
        self.root = Path(data_dir).resolve()
        self.root.mkdir(parents=True, exist_ok=True, mode=0o700)
        self.cli = str(Path(cli).resolve())
        self.allowed_hosts = allowed_hosts
        self.timeout = timeout
        self.lock = threading.RLock()
        self.pending = queue.Queue(maxsize=20)
        self.jobs = {}
        index = self.root / "jobs.json"
        if index.exists():
            for job in json.loads(index.read_text()):
                if not re.fullmatch(r"[a-f0-9]{32}", job.get("id", "")):
                    continue
                if job["state"] in ("queued", "running"):
                    job.update(state="failed", error="El servidor se reinició durante la descarga.")
                self.jobs[job["id"]] = job
        if start_worker:
            threading.Thread(target=self.work, daemon=True).start()

    def save(self):
        with self.lock:
            temporary = self.root / "jobs.json.tmp"
            temporary.write_text(json.dumps(list(self.jobs.values()), ensure_ascii=False))
            temporary.chmod(0o600)
            temporary.replace(self.root / "jobs.json")

    def enqueue(self, payload):
        if not isinstance(payload, dict) or set(payload) - {"url", "quality", "audio_only"}:
            raise ValueError("Solicitud no válida.")
        url = validate_url(payload.get("url"), self.allowed_hosts)
        quality = payload.get("quality", 720)
        if type(quality) is not int or quality not in (360, 480, 720, 1080, 1440, 2160):
            raise ValueError("La calidad no es válida.")
        audio = payload.get("audio_only", False)
        if type(audio) is not bool:
            raise ValueError("El modo de audio no es válido.")
        with self.lock:
            if self.pending.full() or len(self.jobs) >= 500:
                raise ValueError("La cola o el historial están llenos.")
            if shutil.disk_usage(self.root).free < 1024 ** 3:
                raise ValueError("Debe quedar al menos 1 GB libre para iniciar una descarga.")
            job = dict(id=uuid.uuid4().hex, url=url, quality=quality, audio_only=audio,
                       state="queued", created_at=int(time.time()), error=None)
            self.jobs[job["id"]] = job
            self.save()
            self.pending.put_nowait(job["id"])
            return dict(job)

    def files(self, job_id):
        folder = self.root / job_id / "files"
        if not folder.is_dir():
            return []
        result = []
        for path in sorted(folder.rglob("*")):
            if path.is_file() and not path.is_symlink() and path.resolve().is_relative_to(folder.resolve()):
                if path.suffix.lower() not in (".part", ".ytdl", ".tmp"):
                    result.append(dict(name=path.relative_to(folder).as_posix(), size=path.stat().st_size))
        return result

    def snapshot(self):
        with self.lock:
            return [dict(job, files=self.files(job["id"]) if job["state"] == "completed" else [])
                    for job in reversed(list(self.jobs.values()))]

    def file_path(self, job_id, name):
        with self.lock:
            if self.jobs.get(job_id, {}).get("state") != "completed":
                raise ValueError("Archivo no disponible.")
        folder = (self.root / job_id / "files").resolve()
        path = folder / name
        if path.is_symlink() or not path.resolve().is_relative_to(folder) or not path.is_file():
            raise ValueError("Archivo no disponible.")
        return path

    def work(self):
        while True:
            job_id = self.pending.get()
            try:
                self.run(job_id)
            finally:
                self.pending.task_done()

    def run(self, job_id):
        with self.lock:
            job = self.jobs[job_id]
            job.update(state="running", started_at=int(time.time()))
            self.save()
        directory = self.root / job_id
        output = directory / "files"
        command = [self.cli, "download", job["url"], "-q", str(job["quality"]), "-o", str(output)]
        if job["audio_only"]:
            command.append("--audio-only")
        env = os.environ.copy()
        # Do not pass the web bearer or unrelated account credentials to the CLI.
        env = {key: value for key, value in env.items() if key in (
            "PATH", "HOME", "LANG", "LC_ALL", "TMPDIR", "SSL_CERT_FILE", "SSL_CERT_DIR",
        )}
        env["OMNIGET_DATA_DIR"] = str(self.root / "runtime")
        try:
            output.mkdir(parents=True, mode=0o700)
            with (directory / "output.log").open("wb") as log:
                process = subprocess.Popen(command, stdout=log, stderr=subprocess.STDOUT,
                                           env=env, start_new_session=True, stdin=subprocess.DEVNULL)
                try:
                    result = process.wait(timeout=self.timeout)
                except subprocess.TimeoutExpired:
                    os.killpg(process.pid, signal.SIGKILL)
                    process.wait()
                    raise ValueError("La descarga superó el tiempo máximo.")
            if result:
                raise ValueError("La CLI no pudo completar la descarga. Consulta el registro local del trabajo.")
            with self.lock:
                job.update(state="completed", finished_at=int(time.time()))
        except Exception as exc:
            with self.lock:
                job.update(state="failed", error=str(exc), finished_at=int(time.time()))
        finally:
            self.save()


class Handler(BaseHTTPRequestHandler):
    server_version = "OmniGetWeb"

    def log_message(self, *_):
        # Access logs must not store URLs, bearer tokens, or downloaded filenames.
        pass

    def respond(self, status, data, content_type="application/json; charset=utf-8"):
        body = data if isinstance(data, bytes) else json.dumps(data, ensure_ascii=False).encode()
        self.send_response(status)
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        self.send_header("X-Content-Type-Options", "nosniff")
        self.send_header("Referrer-Policy", "no-referrer")
        self.send_header("X-Frame-Options", "DENY")
        self.send_header("Content-Security-Policy", "default-src 'self'; script-src 'self'; style-src 'self'; connect-src 'self'; img-src 'self'; object-src 'none'; frame-ancestors 'none'; base-uri 'none'; form-action 'self'")
        self.end_headers()
        self.wfile.write(body)

    def authenticated(self):
        expected = "Bearer " + self.server.token
        if not hmac.compare_digest(self.headers.get("Authorization", "").encode(), expected.encode()):
            self.respond(401, {"error": "Introduce la clave de acceso del servidor."})
            return False
        return True

    def do_GET(self):
        path = urlsplit(self.path).path
        if path == "/api/health":
            return self.respond(200, {"ok": True})
        if path.startswith("/api/"):
            if not self.authenticated():
                return
            if path == "/api/jobs":
                return self.respond(200, self.server.downloads.snapshot())
            match = re.fullmatch(r"/api/files/([a-f0-9]{32})/(.+)", path)
            if match:
                try:
                    file = self.server.downloads.file_path(match[1], unquote(match[2]))
                    with file.open("rb") as stream:
                        self.send_response(200)
                        self.send_header("Content-Type", "application/octet-stream")
                        self.send_header("Content-Disposition", "attachment")
                        self.send_header("Content-Length", str(file.stat().st_size))
                        self.send_header("Cache-Control", "no-store")
                        self.send_header("X-Content-Type-Options", "nosniff")
                        self.end_headers()
                        shutil.copyfileobj(stream, self.wfile)
                    return
                except (ValueError, OSError):
                    return self.respond(404, {"error": "Archivo no disponible."})
            return self.respond(404, {"error": "Ruta no disponible."})
        names = {"/": "index.html", "/app.js": "app.js", "/style.css": "style.css"}
        if path not in names:
            return self.respond(404, {"error": "Ruta no disponible."})
        file = STATIC / names[path]
        self.respond(200, file.read_bytes(), mimetypes.guess_type(file.name)[0] + "; charset=utf-8")

    def do_POST(self):
        if not self.authenticated():
            return
        if urlsplit(self.path).path != "/api/jobs":
            return self.respond(404, {"error": "Ruta no disponible."})
        try:
            length = int(self.headers.get("Content-Length", "0"))
            if not 0 < length <= 8192 or self.headers.get("Transfer-Encoding"):
                return self.respond(413, {"error": "Solicitud demasiado grande o vacía."})
            payload = json.loads(self.rfile.read(length))
            job = self.server.downloads.enqueue(payload)
            self.respond(202, job)
        except (ValueError, UnicodeDecodeError) as exc:
            self.respond(400, {"error": str(exc)})


class Server(ThreadingHTTPServer):
    daemon_threads = True
    request_queue_size = 16

    def get_request(self):
        connection, address = super().get_request()
        connection.settimeout(15)
        return connection, address


def main():
    os.umask(0o077)
    token = os.environ.get("OMNIGET_WEB_TOKEN", "")
    if len(token) < 32 or not token.isascii() or any(ord(c) < 33 for c in token):
        raise SystemExit("OMNIGET_WEB_TOKEN debe contener al menos 32 caracteres ASCII sin espacios.")
    cli = Path(os.environ.get("OMNIGET_CLI", str(Path.home() / ".local/bin/omniget-cli"))).resolve()
    if not cli.is_file() or not os.access(cli, os.X_OK):
        raise SystemExit("No se encontró una CLI ejecutable. Configura OMNIGET_CLI.")
    bind = os.environ.get("OMNIGET_WEB_BIND", "127.0.0.1")
    port = int(os.environ.get("OMNIGET_WEB_PORT", "8790"))
    hosts = tuple(h.strip().lower() for h in os.environ.get("OMNIGET_ALLOWED_HOSTS", ",".join(DEFAULT_HOSTS)).split(",") if h.strip())
    downloads = DownloadQueue(os.environ.get("OMNIGET_WEB_DATA", str(Path.home() / ".local/share/omniget-web")), cli, hosts)
    with Server((bind, port), Handler) as server:
        server.token = token
        server.downloads = downloads
        print(f"OmniGet web: http://{bind}:{port}", flush=True)
        server.serve_forever()


if __name__ == "__main__":
    main()
