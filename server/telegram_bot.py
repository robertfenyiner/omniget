"""Use the owner's existing bot without starting another update poller."""
import http.client
import json
from pathlib import Path
import re
import secrets
import urllib.parse
import urllib.request


class TelegramBot:
    def __init__(self, config_path):
        self.path = Path(config_path)

    def config(self):
        if not self.path.exists():
            return None
        config = json.loads(self.path.read_text())
        if not config.get("token") or not config.get("chat_id"):
            return None
        return config

    def call(self, method, payload):
        config = self.config()
        if not config:
            raise ValueError("El bot de Telegram no está configurado.")
        url = config["api_base"].rstrip("/") + "/bot" + config["token"] + "/" + method
        request = urllib.request.Request(url, data=json.dumps(payload).encode(), headers={"Content-Type": "application/json"})
        try:
            with urllib.request.urlopen(request, timeout=15) as response:
                result = json.load(response)
            if not result.get("ok"):
                raise ValueError("Telegram rechazó la solicitud.")
            return result["result"]
        except Exception:
            # Never propagate exceptions containing the token-bearing API URL.
            raise ValueError("No se pudo completar la solicitud al bot de Telegram.") from None

    def status(self, verify=False):
        config = self.config()
        if not config:
            return {"configured": False}
        username = self.call("getMe", {})["username"] if verify else config.get("username", "")
        return {"configured": True, "username": username, "notifications": config.get("notifications", True)}

    def send_text(self, text):
        config = self.config()
        if not config:
            raise ValueError("El bot no está configurado.")
        return self.call("sendMessage", {"chat_id": config["chat_id"], "text": text[:4000], "link_preview_options": {"is_disabled": True}})

    def notify_job(self, job):
        config = self.config()
        if not config or not config.get("notifications", True):
            return
        state = {"completed": "Completado", "failed": "No completado", "cancelled": "Cancelado"}.get(job["state"], job["state"])
        title = job.get("title") or urllib.parse.urlsplit(job.get("url", "")).hostname or "Trabajo"
        self.send_text(f"OmniGet · {state}\n{title}\nID: {job['id']}\n{config.get('web_url', '')}")

    def send_file(self, path):
        config = self.config()
        if not config:
            raise ValueError("El bot no está configurado.")
        if path.stat().st_size > 100 * 1024 * 1024:
            raise ValueError("El envío desde OmniGet al bot admite archivos de hasta 100 MB. Usa la descarga web para archivos mayores.")
        base = urllib.parse.urlsplit(config["api_base"])
        boundary = "omniget-" + secrets.token_hex(16)
        name = re.sub(r"[^A-Za-z0-9_. -]", "_", path.name)[:140] or "result.bin"
        prefix = (f"--{boundary}\r\nContent-Disposition: form-data; name=\"chat_id\"\r\n\r\n{config['chat_id']}\r\n"
                  f"--{boundary}\r\nContent-Disposition: form-data; name=\"document\"; filename=\"{name}\"\r\n"
                  "Content-Type: application/octet-stream\r\n\r\n").encode()
        suffix = f"\r\n--{boundary}--\r\n".encode()
        cls = http.client.HTTPSConnection if base.scheme == "https" else http.client.HTTPConnection
        connection = cls(base.hostname, base.port, timeout=60)
        try:
            target = base.path.rstrip("/") + "/bot" + config["token"] + "/sendDocument"
            connection.putrequest("POST", target)
            connection.putheader("Content-Type", "multipart/form-data; boundary=" + boundary)
            connection.putheader("Content-Length", str(len(prefix) + path.stat().st_size + len(suffix)))
            connection.endheaders()
            connection.send(prefix)
            with path.open("rb") as stream:
                while block := stream.read(1024 * 1024):
                    connection.send(block)
            connection.send(suffix)
            response = connection.getresponse()
            data = json.loads(response.read(1024 * 1024))
            if response.status != 200 or not data.get("ok"):
                raise ValueError("Telegram rechazó el archivo.")
            return {"message_id": data["result"]["message_id"], "sent": True}
        except Exception:
            raise ValueError("No se pudo entregar el archivo al bot de Telegram.") from None
        finally:
            connection.close()
