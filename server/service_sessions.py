"""Private cookie jars with explicit domain boundaries and no secret responses."""
import http.cookiejar
import json
import os
from pathlib import Path
import tempfile
import threading
import time
import urllib.error
import urllib.parse
import urllib.request

SERVICES = ("youtube.com", "instagram.com", "facebook.com", "udemy.com")


def domain_matches(host, domain):
    host, domain = host.lower().lstrip("."), domain.lower().lstrip(".")
    return host == domain or host.endswith("." + domain)


class SameUdemyRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, request, fp, code, msg, headers, newurl):
        url = urllib.parse.urlsplit(newurl)
        if (url.scheme != "https" or url.hostname != "www.udemy.com" or url.port not in (None, 443)
                or url.username is not None or url.password is not None):
            raise ValueError("Udemy redirigió la sesión fuera de su dominio. Solicitud detenida.")
        return super().redirect_request(request, fp, code, msg, headers, newurl)


class ServiceSessions:
    def __init__(self, directory, runtime):
        self.directory = Path(directory)
        self.directory.mkdir(parents=True, exist_ok=True, mode=0o700)
        self.cookies = self.directory / "cookies"
        self.cookies.mkdir(exist_ok=True, mode=0o700)
        self.runtime = Path(runtime)
        self.lock = threading.RLock()
        self.last_udemy_check = None

    def import_text(self, service, content):
        if service not in SERVICES or not isinstance(content, str) or len(content.encode()) > 1024 * 1024:
            raise ValueError("El servicio o el archivo de cookies no es válido.")
        with self.lock, tempfile.TemporaryDirectory(dir=self.directory) as working:
            path = Path(working) / "input.txt"
            normalized = []
            for line in content.splitlines():
                fields = line.split("\t")
                if len(fields) == 7 and fields[4] == "0":
                    # Browser exports commonly encode session cookies as zero;
                    # MozillaCookieJar expects an empty expiration for them.
                    fields[4] = ""
                    line = "\t".join(fields)
                normalized.append(line)
            path.write_text("\n".join(normalized) + "\n")
            path.chmod(0o600)
            source = http.cookiejar.MozillaCookieJar(str(path))
            try:
                source.load(ignore_discard=True, ignore_expires=False)
            except (http.cookiejar.LoadError, ValueError):
                raise ValueError("Usa un archivo de cookies Netscape válido y vigente.")
            target = http.cookiejar.MozillaCookieJar()
            for cookie in source:
                domain = cookie.domain.lstrip(".").lower()
                # Subdomains can only set their own cookies, never cookies for
                # lookalike domains such as notudemy.com or udemy.com.evil.test.
                if not domain_matches(domain, service):
                    continue
                if any(ord(c) < 32 or ord(c) == 127 for c in cookie.name + cookie.value):
                    continue
                target.set_cookie(cookie)
            if not len(target):
                raise ValueError("No hay cookies vigentes del servicio seleccionado.")
            temporary = self.cookies / (service + ".tmp")
            self.save_jar(target, temporary)
            temporary.replace(self.cookies / (service + ".txt"))
            self.last_udemy_check = None if service == "udemy.com" else self.last_udemy_check
            return {"service": service, "count": len(target)}

    @staticmethod
    def save_jar(jar, path):
        fd = os.open(path, os.O_CREAT | os.O_TRUNC | os.O_WRONLY, 0o600)
        os.close(fd)
        jar.save(str(path), ignore_discard=True, ignore_expires=False)
        path.chmod(0o600)

    def jar(self, service):
        jar = http.cookiejar.MozillaCookieJar()
        path = self.cookies / (service + ".txt")
        if path.exists():
            jar.load(str(path), ignore_discard=True, ignore_expires=False)
        return jar

    def prepare_runtime(self):
        with self.lock:
            combined = http.cookiejar.MozillaCookieJar()
            for service in SERVICES:
                for cookie in self.jar(service):
                    combined.set_cookie(cookie)
            directory = self.runtime / "cookies"
            directory.mkdir(parents=True, exist_ok=True, mode=0o700)
            self.save_jar(combined, directory / "cookies.txt")

    def status(self):
        with self.lock:
            result = []
            for service in SERVICES:
                jar = self.jar(service)
                result.append({"service": service, "cookies": len(jar),
                               "unexpired": any(not cookie.is_expired() for cookie in jar)})
            return result

    def remove(self, service):
        if service not in SERVICES:
            raise ValueError("Servicio no válido.")
        with self.lock:
            (self.cookies / (service + ".txt")).unlink(missing_ok=True)
            self.prepare_runtime()
            if service == "udemy.com":
                self.last_udemy_check = None

    def udemy_get(self, path):
        if not path.startswith("/api-2.0/"):
            raise ValueError("Ruta Udemy no válida.")
        with self.lock:
            jar = self.jar("udemy.com")
        if not len(jar):
            raise ValueError("Falta importar una sesión vigente de Udemy desde tu navegador.")
        opener = urllib.request.build_opener(urllib.request.ProxyHandler({}),
            urllib.request.HTTPCookieProcessor(jar), SameUdemyRedirect())
        headers = {"User-Agent": "okhttp/4.9.2 UdemyAndroid 9.7.0(515)",
                   "Accept": "application/json", "Referer": "https://www.udemy.com/"}
        # Only exact Udemy auth cookies can become a bearer, and only for this
        # fixed HTTPS origin. Never forward this header to media/CDN URLs.
        access = next((c.value for c in jar if c.name == "access_token" and domain_matches("www.udemy.com", c.domain)), None)
        if access:
            headers["Authorization"] = "Bearer " + access
            headers["X-Udemy-Authorization"] = "Bearer " + access
        request = urllib.request.Request("https://www.udemy.com" + path, headers=headers)
        try:
            with opener.open(request, timeout=35) as response:
                if "json" not in response.headers.get("Content-Type", ""):
                    raise ValueError("Udemy pide una verificación del navegador. Renueva la sesión importada.")
                data = response.read(8 * 1024 * 1024 + 1)
                if len(data) > 8 * 1024 * 1024:
                    raise ValueError("La respuesta Udemy supera el límite permitido.")
                return json.loads(data)
        except urllib.error.HTTPError as error:
            if error.code in (401, 403):
                raise ValueError("Udemy rechazó la sesión o pide verificación de seguridad. Abre Udemy y vuelve a importar las cookies.")
            raise ValueError(f"Udemy respondió con un error HTTP {error.code}.")
        except (urllib.error.URLError, TimeoutError):
            raise ValueError("No se pudo contactar con Udemy.")

    def udemy_courses(self, page=1):
        if type(page) is not int or not 1 <= page <= 100:
            raise ValueError("La página no es válida.")
        query = urllib.parse.urlencode({"page": page, "page_size": 50,
            "fields[course]": "id,title,published_title,url,num_published_lectures", "ordering": "-last_accessed"})
        data = self.udemy_get("/api-2.0/users/me/subscribed-courses/?" + query)
        if not isinstance(data, dict) or not isinstance(data.get("results"), list):
            raise ValueError("La respuesta Udemy no contiene la biblioteca esperada.")
        courses = []
        for item in data["results"]:
            if isinstance(item, dict) and type(item.get("id")) is int:
                courses.append({"id": item["id"], "title": str(item.get("title", "Curso")),
                                "slug": str(item.get("published_title", "")),
                                "lectures": item.get("num_published_lectures")})
        self.last_udemy_check = int(time.time())
        return {"courses": courses, "page": page, "has_more": bool(data.get("next")),
                "count": data.get("count", len(courses)), "verified_at": self.last_udemy_check}

    def udemy_curriculum(self, course_id, page=1):
        if type(course_id) is not int or course_id <= 0 or type(page) is not int or not 1 <= page <= 100:
            raise ValueError("Curso no válido.")
        query = urllib.parse.urlencode({"page_size": 200, "page": page, "fields[lecture]": "title,asset,object_index",
            "fields[asset]": "asset_type,course_is_drmed,stream_urls,download_urls,captions"})
        data = self.udemy_get(f"/api-2.0/courses/{course_id}/subscriber-curriculum-items/?" + query)
        if not isinstance(data, dict) or not isinstance(data.get("results"), list):
            raise ValueError("La respuesta Udemy no contiene las lecciones esperadas.")
        lectures = []
        for item in data.get("results", []):
            if item.get("_class") != "lecture":
                continue
            asset = item.get("asset") or {}
            lectures.append({"id": item.get("id"), "title": item.get("title", "Lección"),
                             "type": asset.get("asset_type"), "drm": bool(asset.get("course_is_drmed"))})
        return {"course_id": course_id, "lectures": lectures, "has_more": bool(data.get("next")), "page": page}
