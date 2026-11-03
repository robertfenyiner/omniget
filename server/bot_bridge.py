"""Authenticated Unix socket bridge; exposes only queue status/download/cancel."""
import hmac
from http.server import BaseHTTPRequestHandler
import json
from pathlib import Path
import re
import socketserver
import threading


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
        if self.path != "/status":
            return self.response(404, {"error": "Ruta no disponible."})
        jobs = self.server.downloads.snapshot()
        recent = [{"id": j["id"], "state": j["state"], "kind": j.get("kind", "download")} for j in jobs[:5]]
        self.response(200, {"ok": True, "jobs": recent, "total": len(jobs),
                            "pending": sum(j["state"] in ("queued", "running") for j in jobs)})

    def do_POST(self):
        if not self.authorized():
            return
        try:
            length = int(self.headers.get("Content-Length", "0"))
            if not 0 < length <= 8192:
                raise ValueError("Solicitud no válida.")
            data = json.loads(self.rfile.read(length))
            if not isinstance(data, dict):
                raise ValueError("Solicitud no válida.")
            if self.path == "/download":
                payload = {key: data[key] for key in ("url", "quality", "audio_only") if key in data}
                job = self.server.downloads.enqueue(payload)
                return self.response(202, {"id": job["id"], "state": job["state"]})
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


def start_bridge(config_path, downloads):
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
    threading.Thread(target=server.serve_forever, daemon=True).start()
    return server
