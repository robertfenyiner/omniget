"""Scoped private bridge from Cosmo to local tools; never exposes credentials."""
import hmac
from http.server import BaseHTTPRequestHandler
import json
from pathlib import Path
import re
import socketserver
import threading
import shutil
import uuid
from urllib.parse import urlsplit, parse_qs, unquote

TOOL_UPLOAD_LIMIT = 100 * 1024 * 1024


def reply_target(data):
    value = data.get("reply_to")
    if type(value) is not int or value <= 0:
        raise ValueError("Falta el mensaje de Telegram al que responder.")
    return value


class BridgeHandler(BaseHTTPRequestHandler):
    def log_message(self, *_):
        pass

    def response(self, status, value):
        data = json.dumps(value, ensure_ascii=False).encode()
        self.send_response(status)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(data)))
        self.end_headers()
        self.wfile.write(data)

    def authorized(self):
        expected = self.server.bridge_config["key"]
        supplied = self.headers.get("X-OmniGet-Key", "")
        if not hmac.compare_digest(supplied.encode(), expected.encode()):
            self.response(401, {"error": "Acceso no autorizado."})
            return False
        return True

    def do_GET(self):
        if not self.authorized():
            return
        path = urlsplit(self.path).path
        query = parse_qs(urlsplit(self.path).query)
        queue = self.server.downloads
        try:
            if path == "/status":
                jobs = queue.snapshot()
                recent = [{k: j[k] for k in ("id", "state", "kind", "delivery") if k in j} for j in jobs[:5]]
                return self.response(200, {"ok": True, "jobs": recent, "total": len(jobs),
                    "pending": sum(j["state"] in ("queued", "running") for j in jobs)})
            if path == "/capabilities":
                from app import PROFILES
                return self.response(200, {"conversion": bool(queue.ffmpeg), "profiles": list(PROFILES),
                    "ocr": bool(queue.tesseract), "pdf": bool(queue.pdftotext), "study": self.server.study is not None,
                    "transcription": bool(queue.whisper and queue.ffmpeg and (queue.models_dir / "ggml-base-q5_1.bin").is_file()),
                    "upload_limit": TOOL_UPLOAD_LIMIT})
            if path == "/notes" and self.server.study:
                return self.response(200, self.server.study.notes(query.get("q", [""])[0]))
            match = re.fullmatch(r"/notes/([a-f0-9]{32})", path)
            if match and self.server.study:
                return self.response(200, self.server.study.note(match[1]))
            if path == "/cards" and self.server.study:
                return self.response(200, self.server.study.cards())
            if path == "/study-export" and self.server.study:
                return self.response(200, json.loads(self.server.study.export()))
            if path == "/udemy/courses" and queue.sessions:
                return self.response(200, queue.sessions.udemy_courses(int(query.get("page", [1])[0])))
            match = re.fullmatch(r"/udemy/courses/([0-9]+)", path)
            if match and queue.sessions:
                return self.response(200, queue.sessions.udemy_curriculum(int(match[1]), int(query.get("page", [1])[0])))
            if path == "/services" and queue.sessions:
                return self.response(200, {"services": queue.sessions.status(), "udemy_verified": bool(queue.sessions.last_udemy_check)})
            self.response(404, {"error": "Ruta no disponible."})
        except (ValueError, UnicodeError) as exc:
            self.response(400, {"error": str(exc)})

    def upload(self):
        from app import UPLOAD_EXTENSIONS
        length = int(self.headers.get("Content-Length", "0"))
        name = unquote(self.headers.get("X-Filename", ""))
        name = re.sub(r"[^\w. -]", "_", name)[:120]
        if not 0 < length <= TOOL_UPLOAD_LIMIT or Path(name).suffix.lower() not in UPLOAD_EXTENSIONS:
            raise ValueError("Archivo no compatible o mayor de 100 MiB.")
        root = self.server.downloads.root
        if shutil.disk_usage(root).free < length + 1024 ** 3:
            raise ValueError("No queda espacio suficiente para el archivo.")
        folder = root / "uploads"
        folder.mkdir(exist_ok=True, mode=0o700)
        path = folder / (uuid.uuid4().hex + "_" + name)
        try:
            with path.open("xb") as stream:
                path.chmod(0o600)
                remaining = length
                while remaining:
                    block = self.rfile.read(min(1024 * 1024, remaining))
                    if not block:
                        raise ValueError("El archivo llegó incompleto.")
                    stream.write(block)
                    remaining -= len(block)
        except Exception:
            path.unlink(missing_ok=True)
            raise
        self.response(201, {"upload_id": path.name})

    def do_POST(self):
        if not self.authorized():
            return
        try:
            if self.headers.get("Transfer-Encoding"):
                raise ValueError("Solicitud no válida.")
            if self.path == "/upload":
                return self.upload()
            length = int(self.headers.get("Content-Length", "0"))
            if not 0 < length <= 2 * 1024 * 1024 + 8192:
                raise ValueError("Solicitud no válida.")
            data = json.loads(self.rfile.read(length))
            if not isinstance(data, dict):
                raise ValueError("Solicitud no válida.")
            if "id" in data and (not isinstance(data["id"], str) or not re.fullmatch(r"[a-f0-9]{32}", data["id"])):
                raise ValueError("El identificador no es válido.")
            if self.path == "/download":
                if set(data) - {"url", "quality", "audio_only", "reply_to"}:
                    raise ValueError("Solicitud no válida.")
                payload = {key: data[key] for key in ("url", "quality", "audio_only") if key in data}
                job = self.server.downloads.enqueue(payload, reply_target(data) if "reply_to" in data else None)
                return self.response(202, {"id": job["id"], "state": job["state"]})
            if self.path == "/tool":
                payload = {key: value for key, value in data.items() if key not in ("reply_to", "request_id")}
                if payload.get("kind") not in ("convert", "transcribe", "document", "udemy"):
                    raise ValueError("Herramienta no válida.")
                request_id = data.get("request_id")
                if request_id is not None and (not isinstance(request_id, str) or not re.fullmatch(r"[A-Za-z0-9:_-]{1,100}", request_id)):
                    raise ValueError("Solicitud no válida.")
                with self.server.downloads.lock:
                    job = next((j for j in self.server.downloads.jobs.values() if request_id and j.get("request_id") == request_id), None)
                    if job is None:
                        job = self.server.downloads.enqueue(payload, reply_target(data))
                        if request_id:
                            self.server.downloads.jobs[job["id"]]["request_id"] = request_id
                            self.server.downloads.save()
                return self.response(202, {"id": job["id"], "state": job["state"]})
            if self.path == "/retry":
                job = self.server.downloads.retry(data.get("id"), reply_target(data))
                return self.response(202, {"id": job["id"], "state": job["state"]})
            if self.path == "/redeliver":
                with self.server.downloads.lock:
                    job = self.server.downloads.jobs.get(data.get("id"))
                    if not job or job["state"] not in ("completed", "failed", "cancelled"):
                        raise ValueError("El trabajo aún no ha terminado.")
                    if job.get("delivery", {}).get("state") == "pending":
                        raise ValueError("El envío ya está en curso. Consulta /estado.")
                    job["delivery"] = dict(reply_to=reply_target(data), state="pending", attempts=0, sent=[], next_at=0)
                    self.server.downloads.save()
                return self.response(202, {"ok": True})
            if self.server.study:
                study = self.server.study
                if self.path == "/notes":
                    return self.response(201, study.save_note(data))
                if self.path == "/delete-note":
                    study.delete_note(data.get("id"))
                    return self.response(200, {"ok": True})
                if self.path == "/cards":
                    return self.response(201, study.save_card(data))
                if self.path == "/review":
                    return self.response(200, study.review(data.get("id"), data.get("rating")))
                if self.path == "/import-note":
                    file = self.server.downloads.file_path(data.get("id"), data.get("name", "document.txt"))
                    if file.suffix.lower() not in (".txt", ".md", ".srt", ".vtt") or file.stat().st_size > 2 * 1024 * 1024:
                        raise ValueError("Importa un documento de texto de hasta 2 MiB.")
                    return self.response(201, study.save_note({"title": file.name, "body": file.read_text(encoding="utf-8", errors="replace")}))
            if self.path == "/cancel" and isinstance(data.get("id"), str) and re.fullmatch(r"[a-f0-9]{32}", data["id"]):
                self.server.downloads.cancel(data["id"])
                return self.response(200, {"ok": True})
            self.response(404, {"error": "Ruta no disponible."})
        except (ValueError, UnicodeError) as exc:
            self.response(400, {"error": str(exc)})


class BridgeServer(socketserver.ThreadingMixIn, socketserver.UnixStreamServer):
    daemon_threads = True

    def get_request(self):
        connection, address = super().get_request()
        connection.settimeout(15)
        return connection, address


def start_bridge(config_path, downloads, study=None):
    config_path = Path(config_path)
    if not config_path.exists():
        return None
    config = json.loads(config_path.read_text())
    if len(config.get("key", "")) < 32:
        raise ValueError("Clave de puente no válida.")
    directory = downloads.root / "bridge"
    directory.mkdir(exist_ok=True)
    directory.chmod(0o711)
    path = directory / "bridge.sock"
    path.unlink(missing_ok=True)
    server = BridgeServer(str(path), BridgeHandler)
    # Parent is private on the host; the directory alone is mounted into the
    # existing bot. Every operation also requires the private scoped key.
    path.chmod(0o666)
    server.bridge_config = config
    server.downloads = downloads
    server.study = study
    threading.Thread(target=server.serve_forever, daemon=True).start()
    return server
