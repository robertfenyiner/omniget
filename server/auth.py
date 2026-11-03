"""Single-user password authentication; credentials never enter the checkout."""
import hashlib
import hmac
from http.cookies import SimpleCookie, CookieError
import json
import os
from pathlib import Path
import secrets
import threading
import time

COOKIE = "omniget_session"


class AuthError(ValueError):
    def __init__(self, message, status=401):
        super().__init__(message)
        self.status = status


class AuthStore:
    def __init__(self, directory):
        self.directory = Path(directory)
        self.directory.mkdir(parents=True, exist_ok=True, mode=0o700)
        self.path = self.directory / "password.json"
        self.initial_path = self.directory / "initial-password"
        self.lock = threading.RLock()
        self.sessions = {}
        self.attempts = {}
        if self.path.exists():
            self.record = json.loads(self.path.read_text())
        else:
            # Reuse the initial credential if a crash interrupted first-time setup.
            password = self.initial_path.read_text().strip() if self.initial_path.exists() else secrets.token_urlsafe(18)
            self.write_private(self.initial_path, password + "\n")
            self.record = self.password_record(password, must_change=True)
            self.write_private(self.path, json.dumps(self.record))

    @staticmethod
    def write_private(path, text):
        temporary = path.with_suffix(path.suffix + ".tmp")
        descriptor = os.open(temporary, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
        with os.fdopen(descriptor, "w") as stream:
            stream.write(text)
        temporary.chmod(0o600)
        temporary.replace(path)

    @staticmethod
    def digest(password, salt):
        return hashlib.scrypt(password.encode(), salt=bytes.fromhex(salt),
                              n=32768, r=8, p=1, maxmem=64 * 1024 * 1024).hex()

    @classmethod
    def password_record(cls, password, must_change=False):
        if not isinstance(password, str) or not 12 <= len(password) <= 256:
            raise AuthError("La contraseña debe tener entre 12 y 256 caracteres.", 400)
        salt = secrets.token_hex(16)
        return {"salt": salt, "hash": cls.digest(password, salt), "must_change": must_change}

    def verify(self, password):
        if not isinstance(password, str) or len(password) > 256:
            return False
        return hmac.compare_digest(self.digest(password, self.record["salt"]), self.record["hash"])

    def check_rate(self, client):
        now = time.monotonic()
        self.attempts = {key: [t for t in values if t > now - 600]
                         for key, values in self.attempts.items() if any(t > now - 600 for t in values)}
        if len(self.attempts.get(client, [])) >= 8 or sum(map(len, self.attempts.values())) >= 80:
            raise AuthError("Demasiados intentos. Espera diez minutos antes de volver a intentarlo.", 429)
        self.attempts.setdefault(client, []).append(now)

    def new_session(self):
        now = time.time()
        self.sessions = {key: expiry for key, expiry in self.sessions.items() if expiry > now}
        if len(self.sessions) >= 32:
            del self.sessions[next(iter(self.sessions))]
        token = secrets.token_urlsafe(32)
        self.sessions[token] = now + (600 if self.record["must_change"] else 8 * 3600)
        return token

    def login(self, password, client):
        with self.lock:
            self.check_rate(client)
            if not self.verify(password):
                raise AuthError("Contraseña incorrecta.")
            self.attempts.pop(client, None)
            return self.new_session()

    def session(self, cookie_header):
        try:
            cookie = SimpleCookie()
            cookie.load(cookie_header or "")
            token = cookie[COOKIE].value if COOKIE in cookie else ""
        except CookieError:
            return None
        with self.lock:
            return token if self.sessions.get(token, 0) > time.time() else None

    def change(self, session, current_password, new_password, client):
        with self.lock:
            if self.sessions.get(session, 0) <= time.time():
                raise AuthError("La sesión ha caducado. Vuelve a entrar.")
            self.check_rate(client)
            if not self.verify(current_password):
                raise AuthError("La contraseña actual es incorrecta.")
            if current_password == new_password:
                raise AuthError("Elige una contraseña diferente de la temporal o actual.", 400)
            record = self.password_record(new_password)
            self.write_private(self.path, json.dumps(record))
            self.record = record
            self.sessions.clear()
            self.attempts.pop(client, None)
            self.initial_path.unlink(missing_ok=True)
            return self.new_session()

    def logout(self, session):
        with self.lock:
            self.sessions.pop(session, None)

    @staticmethod
    def cookie(token, secure=False):
        return f"{COOKIE}={token}; HttpOnly; SameSite=Strict; Path=/; Max-Age=28800" + ("; Secure" if secure else "")
