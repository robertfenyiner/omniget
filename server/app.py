#!/usr/bin/env python3
"""Private, single-user web queue for omniget-cli (Python standard library)."""
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
from urllib.parse import unquote, urlsplit, parse_qs
import uuid

from auth import AuthStore, AuthError

STATIC = Path(__file__).with_name("static")
DEFAULT_HOSTS = (
    "youtube.com", "youtu.be", "vimeo.com", "soundcloud.com", "bilibili.com",
    "b23.tv", "instagram.com", "tiktok.com", "x.com", "twitter.com",
    "reddit.com", "twitch.tv", "pinterest.com", "raw.githubusercontent.com",
)
PROFILES = {
    "mp4": ["-c:v", "libx264", "-preset", "fast", "-crf", "23", "-c:a", "aac", "-movflags", "+faststart"],
    "mp3": ["-vn", "-c:a", "libmp3lame", "-q:a", "2"],
    "wav": ["-vn", "-c:a", "pcm_s16le"],
    "flac": ["-vn", "-c:a", "flac"],
    "png": ["-frames:v", "1", "-update", "1"],
    "jpg": ["-frames:v", "1", "-update", "1", "-q:v", "2"],
}
UPLOAD_EXTENSIONS = {".mp4", ".mkv", ".webm", ".mp3", ".wav", ".m4a", ".flac", ".mov", ".avi", ".ogg", ".opus", ".jpg", ".jpeg", ".png", ".webp"}
UPLOAD_LIMIT = 1024 ** 3


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
        self.processes = {}
        self.ffmpeg = shutil.which("ffmpeg")
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
        if isinstance(payload, dict) and payload.get("kind") == "convert":
            if (set(payload) - {"kind", "upload_id", "profile"}
                    or not isinstance(payload.get("profile"), str) or payload["profile"] not in PROFILES):
                raise ValueError("El formato de conversión no es válido.")
            if not self.ffmpeg:
                raise ValueError("FFmpeg no está disponible en este servidor.")
            upload = self.upload_path(payload.get("upload_id", ""))
            return self.add_job(dict(kind="convert", upload_id=upload.name, profile=payload["profile"],
                                     title=upload.name[33:]))
        if not isinstance(payload, dict) or set(payload) - {"url", "quality", "audio_only"}:
            raise ValueError("Solicitud no válida.")
        url = validate_url(payload.get("url"), self.allowed_hosts)
        quality = payload.get("quality", 720)
        if type(quality) is not int or quality not in (360, 480, 720, 1080, 1440, 2160):
            raise ValueError("La calidad no es válida.")
        audio = payload.get("audio_only", False)
        if type(audio) is not bool:
            raise ValueError("El modo de audio no es válido.")
        return self.add_job(dict(kind="download", url=url, quality=quality, audio_only=audio))

    def add_job(self, details):
        with self.lock:
            if self.pending.full() or len(self.jobs) >= 500:
                raise ValueError("La cola o el historial están llenos.")
            if shutil.disk_usage(self.root).free < 1024 ** 3:
                raise ValueError("Debe quedar al menos 1 GB libre para iniciar una descarga.")
            job = dict(details, id=uuid.uuid4().hex, state="queued", created_at=int(time.time()), error=None)
            self.jobs[job["id"]] = job
            self.save()
            self.pending.put_nowait(job["id"])
            return dict(job)

    def upload_path(self, upload_id):
        if not isinstance(upload_id, str) or not re.fullmatch(r"[a-f0-9]{32}_[\w. -]{1,120}", upload_id):
            raise ValueError("El archivo de entrada no es válido.")
        path = self.root / "uploads" / upload_id
        if path.is_symlink() or not path.is_file():
            raise ValueError("El archivo de entrada no está disponible.")
        return path

    def cancel(self, job_id):
        with self.lock:
            job = self.jobs.get(job_id)
            if not job or job["state"] not in ("queued", "running"):
                raise ValueError("Este trabajo no se puede cancelar.")
            job.update(state="cancelled", finished_at=int(time.time()))
            process = self.processes.get(job_id)
            if process:
                try:
                    os.killpg(process.pid, signal.SIGKILL)
                except ProcessLookupError:
                    pass
            self.save()

    def retry(self, job_id):
        with self.lock:
            original = dict(self.jobs.get(job_id, {}))
        if original.get("state") not in ("failed", "cancelled"):
            raise ValueError("Este trabajo no se puede reintentar.")
        if original.get("kind") == "convert":
            return self.enqueue({key: original[key] for key in ("kind", "upload_id", "profile")})
        return self.enqueue({key: original[key] for key in ("url", "quality", "audio_only")})

    def delete(self, job_id):
        with self.lock:
            if self.jobs.get(job_id, {}).get("state") not in ("completed", "failed", "cancelled"):
                raise ValueError("Cancela el trabajo antes de eliminarlo.")
            job = self.jobs.pop(job_id)
            shutil.rmtree(self.root / job_id, ignore_errors=True)
            if job.get("kind") == "convert" and not any(j.get("upload_id") == job["upload_id"] for j in self.jobs.values()):
                (self.root / "uploads" / job["upload_id"]).unlink(missing_ok=True)
            self.save()

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
            job = self.jobs.get(job_id)
            if not job or job["state"] == "cancelled":
                return
            job.update(state="running", started_at=int(time.time()))
            self.save()
        directory = self.root / job_id
        output = directory / "files"
        env = os.environ.copy()
        # Do not pass the web bearer or unrelated account credentials to the CLI.
        env = {key: value for key, value in env.items() if key in (
            "PATH", "HOME", "LANG", "LC_ALL", "TMPDIR", "SSL_CERT_FILE", "SSL_CERT_DIR",
        )}
        env["OMNIGET_DATA_DIR"] = str(self.root / "runtime")
        try:
            output.mkdir(parents=True, mode=0o700)
            if job.get("kind") == "convert":
                # Do not allow playlists or remote protocols inside uploaded files.
                command = [self.ffmpeg, "-nostdin", "-y", "-protocol_whitelist", "file,pipe",
                           "-format_whitelist", "mov,matroska,webm,mp3,wav,flac,ogg,avi,aac,image2,png_pipe,jpeg_pipe,webp_pipe", "-i",
                           str(self.upload_path(job["upload_id"])), "-map_metadata", "-1",
                           *PROFILES[job["profile"]], str(output / ("converted." + job["profile"]))]
            else:
                command = [self.cli, "download", job["url"], "-q", str(job["quality"]), "-o", str(output)]
                if job["audio_only"]:
                    command.append("--audio-only")
            with (directory / "output.log").open("wb") as log:
                process = subprocess.Popen(command, stdout=log, stderr=subprocess.STDOUT,
                                           env=env, start_new_session=True, stdin=subprocess.DEVNULL)
                with self.lock:
                    self.processes[job_id] = process
                    if job["state"] == "cancelled":
                        try:
                            os.killpg(process.pid, signal.SIGKILL)
                        except ProcessLookupError:
                            pass
                try:
                    result = process.wait(timeout=self.timeout)
                except subprocess.TimeoutExpired:
                    os.killpg(process.pid, signal.SIGKILL)
                    process.wait()
                    raise ValueError("La descarga superó el tiempo máximo.")
            if result:
                raise ValueError("No se pudo completar el trabajo. Revisa el archivo de entrada o vuelve a intentarlo.")
            with self.lock:
                if job["state"] != "cancelled":
                    job.update(state="completed", finished_at=int(time.time()))
        except Exception as exc:
            with self.lock:
                if job["state"] != "cancelled":
                    job.update(state="failed", error=str(exc), finished_at=int(time.time()))
        finally:
            with self.lock:
                self.processes.pop(job_id, None)
            self.save()


class Handler(BaseHTTPRequestHandler):
    server_version = "OmniGetWeb"

    def log_message(self, *_):
        # Access logs must not store URLs, bearer tokens, or downloaded filenames.
        pass

    def respond(self, status, data, content_type="application/json; charset=utf-8", headers=None):
        body = data if isinstance(data, bytes) else json.dumps(data, ensure_ascii=False).encode()
        self.send_response(status)
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        self.send_header("X-Content-Type-Options", "nosniff")
        self.send_header("Referrer-Policy", "no-referrer")
        self.send_header("X-Frame-Options", "DENY")
        self.send_header("Content-Security-Policy", "default-src 'self'; script-src 'self'; style-src 'self'; connect-src 'self'; img-src 'self'; object-src 'none'; frame-ancestors 'none'; base-uri 'none'; form-action 'self'")
        for key, value in (headers or {}).items():
            self.send_header(key, value)
        self.end_headers()
        self.wfile.write(body)

    def authenticated(self):
        self.session = self.server.auth.session(self.headers.get("Cookie"))
        if not self.session:
            self.respond(401, {"error": "Introduce tu contraseña para entrar."})
            return False
        if self.server.auth.record["must_change"]:
            self.respond(403, {"error": "Debes cambiar la contraseña temporal antes de continuar.", "must_change": True})
            return False
        return True

    def do_GET(self):
        path = urlsplit(self.path).path
        if path == "/api/health":
            return self.respond(200, {"ok": True})
        if path == "/api/auth/status":
            session = self.server.auth.session(self.headers.get("Cookie"))
            return self.respond(200, {"authenticated": bool(session),
                                      "must_change": bool(session and self.server.auth.record["must_change"])})
        if path.startswith("/api/"):
            if not self.authenticated():
                return
            if path == "/api/jobs":
                return self.respond(200, self.server.downloads.snapshot())
            if path == "/api/capabilities":
                return self.respond(200, {"conversion": bool(self.server.downloads.ffmpeg),
                                         "profiles": list(PROFILES), "upload_limit": UPLOAD_LIMIT,
                                         "allowed_hosts": self.server.downloads.allowed_hosts})
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

    def read_json(self):
        length = int(self.headers.get("Content-Length", "0"))
        if not 0 < length <= 8192 or self.headers.get("Transfer-Encoding"):
            raise AuthError("Solicitud demasiado grande o vacía.", 413)
        if self.headers.get_content_type() != "application/json":
            raise AuthError("Envía una solicitud JSON.", 415)
        return json.loads(self.rfile.read(length))

    def auth_post(self, path):
        try:
            payload = self.read_json()
            if not isinstance(payload, dict):
                raise AuthError("Solicitud no válida.", 400)
            auth = self.server.auth
            session = auth.session(self.headers.get("Cookie"))
            if path == "/api/auth/login":
                session = auth.login(payload.get("password"), self.client_address[0])
            elif path == "/api/auth/password":
                session = auth.change(session, payload.get("current_password"), payload.get("new_password"), self.client_address[0])
            elif path == "/api/auth/logout":
                auth.logout(session)
                return self.respond(200, {"ok": True}, headers={"Set-Cookie": f"omniget_session=; HttpOnly; SameSite=Strict; Path=/; Max-Age=0"})
            else:
                return self.respond(404, {"error": "Ruta no disponible."})
            self.respond(200, {"authenticated": True, "must_change": auth.record["must_change"]},
                         headers={"Set-Cookie": auth.cookie(session, getattr(self.server, "secure_cookies", False))})
        except AuthError as exc:
            self.respond(exc.status, {"error": str(exc)})
        except (ValueError, UnicodeDecodeError):
            self.respond(400, {"error": "Solicitud no válida."})

    def upload(self):
        path = None
        try:
            length = int(self.headers.get("Content-Length", "0"))
            if not 0 < length <= UPLOAD_LIMIT or self.headers.get("Transfer-Encoding"):
                raise AuthError("El archivo debe ocupar entre 1 byte y 1 GB.", 413)
            if shutil.disk_usage(self.server.downloads.root).free < length + 1024 ** 3:
                raise ValueError("No hay espacio suficiente en el servidor.")
            name = parse_qs(urlsplit(self.path).query).get("name", [""])[0]
            name = re.sub(r"[^\w. -]", "_", name)[:120]
            if Path(name).suffix.lower() not in UPLOAD_EXTENSIONS:
                raise ValueError("Usa un archivo de vídeo, audio o imagen compatible.")
            directory = self.server.downloads.root / "uploads"
            directory.mkdir(exist_ok=True, mode=0o700)
            path = directory / (uuid.uuid4().hex + "_" + name)
            with path.open("xb") as output:
                remaining = length
                while remaining:
                    block = self.rfile.read(min(remaining, 1024 * 1024))
                    if not block:
                        raise ValueError("La subida quedó incompleta.")
                    output.write(block)
                    remaining -= len(block)
            return self.respond(201, {"upload_id": path.name, "name": name, "size": length})
        except (ValueError, OSError) as exc:
            if path:
                path.unlink(missing_ok=True)
            self.respond(exc.status if isinstance(exc, AuthError) else 400, {"error": str(exc)})

    def do_POST(self):
        origin = self.headers.get("Origin")
        if origin and origin not in ("http://" + self.headers.get("Host", ""), "https://" + self.headers.get("Host", "")):
            return self.respond(403, {"error": "Origen no autorizado."})
        path = urlsplit(self.path).path
        if path.startswith("/api/auth/"):
            return self.auth_post(path)
        if not self.authenticated():
            return
        if path == "/api/uploads":
            return self.upload()
        try:
            payload = self.read_json()
            if path == "/api/jobs":
                return self.respond(202, self.server.downloads.enqueue(payload))
            match = re.fullmatch(r"/api/jobs/([a-f0-9]{32})/(cancel|retry|delete)", path)
            if match:
                result = getattr(self.server.downloads, match[2])(match[1])
                return self.respond(202 if match[2] == "retry" else 200, result or {"ok": True})
            return self.respond(404, {"error": "Ruta no disponible."})
        except AuthError as exc:
            self.respond(exc.status, {"error": str(exc)})
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
    auth = AuthStore(os.environ.get("OMNIGET_AUTH_DIR", str(Path.home() / ".config/omniget-web")))
    cli = Path(os.environ.get("OMNIGET_CLI", str(Path.home() / ".local/bin/omniget-cli"))).resolve()
    if not cli.is_file() or not os.access(cli, os.X_OK):
        raise SystemExit("No se encontró una CLI ejecutable. Configura OMNIGET_CLI.")
    bind = os.environ.get("OMNIGET_WEB_BIND", "127.0.0.1")
    port = int(os.environ.get("OMNIGET_WEB_PORT", "8790"))
    hosts = tuple(h.strip().lower() for h in os.environ.get("OMNIGET_ALLOWED_HOSTS", ",".join(DEFAULT_HOSTS)).split(",") if h.strip())
    downloads = DownloadQueue(os.environ.get("OMNIGET_WEB_DATA", str(Path.home() / ".local/share/omniget-web")), cli, hosts)
    with Server((bind, port), Handler) as server:
        server.auth = auth
        server.secure_cookies = os.environ.get("OMNIGET_SECURE_COOKIES") == "1"
        server.downloads = downloads
        print(f"OmniGet web: http://{bind}:{port}", flush=True)
        server.serve_forever()


if __name__ == "__main__":
    main()
