import http.client
import json
from pathlib import Path
import socket
import tempfile
import unittest
from unittest.mock import patch
import urllib.error
import urllib.request

from app import DownloadQueue
from bot_bridge import start_bridge
from service_sessions import ServiceSessions, SameUdemyRedirect, domain_matches
from telegram_bot import TelegramBot
from ocr import extract

COOKIE_TEXT = """# Netscape HTTP Cookie File
.udemy.com\tTRUE\t/\tTRUE\t0\taccess_token\tFAKE_PRIVATE_TOKEN
#HttpOnly_.udemy.com\tTRUE\t/\tTRUE\t0\tdj_session_id\tFAKE_PRIVATE_SESSION
.notudemy.com\tTRUE\t/\tTRUE\t0\taccess_token\tWRONG_DOMAIN_TOKEN
.udemy.com.evil.invalid\tTRUE\t/\tTRUE\t0\tsecret\tWRONG_DOMAIN_TOKEN
.youtube.com\tTRUE\t/\tTRUE\t0\tSID\tOTHER_SERVICE_TOKEN
.udemy.com\tTRUE\t/\tTRUE\t1\texpired\tEXPIRED_TOKEN
"""


class SessionTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.sessions = ServiceSessions(Path(self.temp.name) / "private", Path(self.temp.name) / "runtime")

    def tearDown(self):
        self.temp.cleanup()

    def test_cookie_domains_http_only_session_zero_and_expiry(self):
        result = self.sessions.import_text("udemy.com", COOKIE_TEXT)
        self.assertEqual(result, {"service": "udemy.com", "count": 2})
        jar = self.sessions.jar("udemy.com")
        self.assertEqual({cookie.name for cookie in jar}, {"access_token", "dj_session_id"})
        self.assertNotIn("FAKE_PRIVATE", json.dumps(self.sessions.status()))
        self.sessions.prepare_runtime()
        path = self.sessions.runtime / "cookies/cookies.txt"
        self.assertEqual(path.stat().st_mode & 0o777, 0o600)
        self.assertNotIn("WRONG_DOMAIN", path.read_text())
        self.assertIn("#HttpOnly_", path.read_text())
        self.sessions.remove("udemy.com")
        self.assertEqual(len(self.sessions.jar("udemy.com")), 0)
        self.assertNotIn("FAKE_PRIVATE", path.read_text())

    def test_rejects_missing_service_and_external_redirects(self):
        for domain in ("udemy.com.evil.invalid", "notudemy.com", None):
            with self.assertRaises(ValueError):
                self.sessions.import_text(domain, COOKIE_TEXT)
        self.assertFalse(domain_matches("notudemy.com", "udemy.com"))
        with self.assertRaises(ValueError):
            SameUdemyRedirect().redirect_request(None, None, 302, "", {}, "https://evil.invalid/steal")
        with self.assertRaises(ValueError):
            SameUdemyRedirect().redirect_request(None, None, 302, "", {}, "https://user:password@www.udemy.com/api-2.0/")

    def test_udemy_requires_session_and_sanitizes_remote_data(self):
        with self.assertRaises(ValueError):
            self.sessions.udemy_get("/api-2.0/users/me/")
        fixture = {"results": [{"id": 42, "title": "Owned course", "published_title": "owned-course", "secret": "do-not-expose"}], "next": None, "count": 1}
        with patch.object(self.sessions, "udemy_get", return_value=fixture):
            result = self.sessions.udemy_courses()
        self.assertEqual(result["courses"][0]["id"], 42)
        self.assertNotIn("do-not-expose", json.dumps(result))
        self.assertTrue(self.sessions.last_udemy_check)
        lecture_fixture = {"results": [{"_class": "lecture", "id": 100, "title": "Lecture", "asset": {"asset_type": "Video", "course_is_drmed": True}}]}
        with patch.object(self.sessions, "udemy_get", return_value=lecture_fixture):
            self.assertTrue(self.sessions.udemy_curriculum(42)["lectures"][0]["drm"])
        with patch.object(self.sessions, "udemy_get", return_value=[]), self.assertRaises(ValueError):
            self.sessions.udemy_curriculum(42)

    def test_remote_auth_failure_does_not_expose_tokens(self):
        self.sessions.import_text("udemy.com", COOKIE_TEXT)
        error = urllib.error.HTTPError("https://www.udemy.com/private?secret=FAKE_PRIVATE_TOKEN", 403, "Forbidden", {}, None)
        with patch("urllib.request.OpenerDirector.open", side_effect=error):
            with self.assertRaises(ValueError) as caught:
                self.sessions.udemy_courses()
        self.assertNotIn("FAKE_PRIVATE", str(caught.exception))


class UnixConnection(http.client.HTTPConnection):
    def __init__(self, path):
        super().__init__("localhost", timeout=3)
        self.path = path

    def connect(self):
        self.sock = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
        self.sock.connect(self.path)


class BridgeTests(unittest.TestCase):
    def test_bridge_auth_scope_queue_and_cancel(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            config = root / "bridge-config.json"
            key = "test-key-" * 8
            config.write_text(json.dumps({"key": key}))
            queue = DownloadQueue(root / "data", "/bin/true", start_worker=False)
            server = start_bridge(config, queue)
            def request(method, path, data=None, authorized=True):
                connection = UnixConnection(str(queue.root / "bridge/bridge.sock"))
                headers = {"Content-Type": "application/json"}
                if authorized:
                    headers["X-OmniGet-Key"] = key
                connection.request(method, path, json.dumps(data) if data is not None else None, headers)
                response = connection.getresponse()
                status, body = response.status, json.loads(response.read())
                connection.close()
                return status, body
            try:
                self.assertEqual(request("GET", "/status", authorized=False)[0], 401)
                self.assertEqual(request("GET", "/api/study/export")[0], 404)
                with patch("app.socket.getaddrinfo", return_value=[(2, 1, 6, "", ("93.184.216.34", 443))]):
                    status, job = request("POST", "/download", {"url": "https://youtu.be/test"})
                self.assertEqual(status, 202)
                self.assertEqual(request("POST", "/cancel", {"id": job["id"]})[0], 200)
                self.assertEqual(queue.jobs[job["id"]]["state"], "cancelled")
                self.assertEqual(request("GET", "/status")[1]["pending"], 0)
            finally:
                server.shutdown()
                server.server_close()


class TelegramTests(unittest.TestCase):
    def test_bot_errors_redact_token_and_recipient_is_fixed(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "telegram.json"
            path.write_text(json.dumps({"token": "SENSITIVE_FAKE_TOKEN", "chat_id": 123, "api_base": "https://api.telegram.org"}))
            bot = TelegramBot(path)
            with patch("urllib.request.urlopen", side_effect=RuntimeError("SENSITIVE_FAKE_TOKEN")):
                with self.assertRaises(ValueError) as caught:
                    bot.send_text("Test")
            self.assertNotIn("SENSITIVE_FAKE_TOKEN", str(caught.exception))
            with patch.object(bot, "call", return_value={"message_id": 1}) as call:
                bot.send_text("Test")
                self.assertEqual(call.call_args.args[1]["chat_id"], 123)
            self.assertNotIn("token", bot.status())


class OcrTests(unittest.TestCase):
    def test_page_limit_rejects_large_document_before_rendering(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            with patch("ocr.subprocess.check_output", return_value="Pages: 51\n"), patch("ocr.subprocess.run") as rendering:
                with self.assertRaises(ValueError):
                    extract(root / "large.pdf", root / "output")
                rendering.assert_not_called()


if __name__ == "__main__":
    unittest.main()
