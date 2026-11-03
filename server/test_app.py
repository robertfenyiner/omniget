import http.client
import json
import socket
from pathlib import Path
import tempfile
import threading
import time
import unittest
from unittest.mock import patch

from app import DownloadQueue, Handler, Server, validate_url
from auth import AuthStore, AuthError

PUBLIC_DNS = [(2, 1, 6, "", ("93.184.216.34", 443))]
REAL_DNS = socket.getaddrinfo


def public_dns(host, *args, **kwargs):
    if host in ("127.0.0.1", "localhost"):
        return REAL_DNS(host, *args, **kwargs)
    return PUBLIC_DNS


class WebTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.downloads = DownloadQueue(self.temp.name, "/bin/true", start_worker=False)
        self.server = Server(("127.0.0.1", 0), Handler)
        self.server.auth = AuthStore(Path(self.temp.name) / "auth")
        initial = self.server.auth.initial_path.read_text().strip()
        limited = self.server.auth.login(initial, "test")
        self.session = self.server.auth.change(limited, initial, "permanent-test-password", "test")
        self.server.downloads = self.downloads
        self.thread = threading.Thread(target=self.server.serve_forever, daemon=True)
        self.thread.start()

    def tearDown(self):
        self.server.shutdown()
        self.server.server_close()
        self.thread.join()
        self.temp.cleanup()

    def request(self, method, path, body=None, authorized=True, headers=None):
        connection = http.client.HTTPConnection(*self.server.server_address, timeout=3)
        request_headers = {"Content-Type": "application/json"}
        if authorized:
            request_headers["Cookie"] = "omniget_session=" + self.session
        request_headers.update(headers or {})
        connection.request(method, path, body, request_headers)
        response = connection.getresponse()
        result = response.status, response.read(), dict(response.headers)
        connection.close()
        return result

    def test_health_is_public_but_jobs_and_files_require_auth(self):
        self.assertEqual(self.request("GET", "/api/health", authorized=False)[0], 200)
        for path in ("/api/jobs", "/api/files/" + "a" * 32 + "/secret"):
            self.assertEqual(self.request("GET", path, authorized=False)[0], 401)
        self.assertEqual(self.request("POST", "/api/jobs", "{}", authorized=False)[0], 401)

    @patch("app.socket.getaddrinfo", side_effect=public_dns)
    def test_job_can_be_queued_and_persisted(self, _dns):
        status, body, _ = self.request("POST", "/api/jobs", json.dumps({"url": "https://youtu.be/test"}))
        self.assertEqual(status, 202)
        job = json.loads(body)
        self.assertEqual(job["state"], "queued")
        self.assertEqual(len(self.downloads.jobs), 1)
        restarted = DownloadQueue(self.temp.name, "/bin/true", start_worker=False)
        self.assertEqual(restarted.jobs[job["id"]]["state"], "failed")

    @patch("app.socket.getaddrinfo", side_effect=public_dns)
    def test_rejects_untrusted_urls_and_arbitrary_cli_options(self, _dns):
        for url in ("file:///etc/passwd", "https://youtube.com.evil.invalid/x", "https://localhost/x",
                    "http://youtu.be/x", "https://user:pass@youtu.be/x", "https://youtu.be:8080/x",
                    "--exec=whoami", "https://youtu.be/x\n--exec=whoami"):
            with self.subTest(url=url), self.assertRaises(ValueError):
                validate_url(url, ("youtube.com", "youtu.be"))
        for payload in ({"url": "https://youtu.be/x", "quality": "720 --exec=id"},
                        {"url": "https://youtu.be/x", "args": ["--exec=id"]},
                        {"url": "https://youtu.be/x", "audio_only": "yes"}):
            self.assertEqual(self.request("POST", "/api/jobs", json.dumps(payload))[0], 400)

    @patch("app.socket.getaddrinfo", return_value=[(2, 1, 6, "", ("127.0.0.1", 443))])
    def test_private_dns_answers_are_rejected(self, _dns):
        with self.assertRaises(ValueError):
            validate_url("https://youtu.be/x", ("youtu.be",))

    def test_file_access_is_confined_to_completed_job(self):
        job_id = "a" * 32
        self.downloads.jobs[job_id] = {"id": job_id, "state": "completed"}
        folder = Path(self.temp.name) / job_id / "files"
        folder.mkdir(parents=True)
        (folder / "video.mp4").write_bytes(b"fake-media")
        (Path(self.temp.name) / "secret").write_bytes(b"secret")
        (folder / "link.mp4").symlink_to(Path(self.temp.name) / "secret")
        self.assertEqual(self.request("GET", f"/api/files/{job_id}/video.mp4")[1], b"fake-media")
        for name in ("..%2F..%2Fsecret", "link.mp4", "%2Fetc%2Fpasswd"):
            self.assertEqual(self.request("GET", f"/api/files/{job_id}/{name}")[0], 404)

    def test_static_page_has_security_headers_and_no_cors(self):
        status, body, headers = self.request("GET", "/", authorized=False)
        self.assertEqual(status, 200)
        self.assertIn(b'lang="es"', body)
        self.assertIn("frame-ancestors 'none'", headers["Content-Security-Policy"])
        self.assertNotIn("Access-Control-Allow-Origin", headers)
        self.assertEqual(self.request("GET", "/../app.py")[0], 404)

    @patch("app.socket.getaddrinfo", side_effect=public_dns)
    def test_cli_success_failure_and_timeout(self, _dns):
        for cli, expected in (("/bin/true", "completed"), ("/bin/false", "failed")):
            self.downloads.cli = cli
            job = self.downloads.enqueue({"url": "https://youtu.be/test"})
            self.downloads.run(job["id"])
            self.assertEqual(self.downloads.jobs[job["id"]]["state"], expected)
        script = Path(self.temp.name) / "slow-cli"
        script.write_text("#!/bin/sh\nsleep 30\n")
        script.chmod(0o700)
        self.downloads.cli = str(script)
        self.downloads.timeout = .1
        job = self.downloads.enqueue({"url": "https://youtu.be/test"})
        self.downloads.run(job["id"])
        self.assertEqual(self.downloads.jobs[job["id"]]["state"], "failed")
        self.assertIn("tiempo máximo", self.downloads.jobs[job["id"]]["error"])

    def test_large_request_and_invalid_json_are_rejected(self):
        self.assertEqual(self.request("POST", "/api/jobs", "x" * 8193)[0], 413)
        self.assertEqual(self.request("POST", "/api/jobs", "{")[0], 400)

    def test_temporary_password_cannot_access_jobs_and_change_revokes_sessions(self):
        with tempfile.TemporaryDirectory() as directory:
            auth = AuthStore(directory)
            initial = auth.initial_path.read_text().strip()
            self.server.auth = auth
            status, _, headers = self.request("POST", "/api/auth/login", json.dumps({"password": initial}), authorized=False)
            self.assertEqual(status, 200)
            self.assertIn("HttpOnly", headers["Set-Cookie"])
            cookie = headers["Set-Cookie"].split(";", 1)[0]
            self.assertEqual(self.request("GET", "/api/jobs", headers={"Cookie": cookie})[0], 403)
            status, _, headers = self.request("POST", "/api/auth/password", json.dumps({"current_password": initial, "new_password": "my-new-password-123"}), headers={"Cookie": cookie})
            self.assertEqual(status, 200)
            self.assertFalse(auth.initial_path.exists())
            self.assertEqual(self.request("GET", "/api/jobs", headers={"Cookie": cookie})[0], 401)
            new_cookie = headers["Set-Cookie"].split(";", 1)[0]
            self.assertEqual(self.request("GET", "/api/jobs", headers={"Cookie": new_cookie})[0], 200)
            auth.logout(new_cookie.split("=", 1)[1])
            self.assertEqual(self.request("GET", "/api/jobs", headers={"Cookie": new_cookie})[0], 401)
            self.assertNotIn("my-new-password-123", auth.path.read_text())

    def test_wrong_password_rate_limit_and_cross_origin_post(self):
        for _ in range(8):
            with self.assertRaises(AuthError):
                self.server.auth.login("wrong-password", "attacker")
        with self.assertRaises(AuthError) as caught:
            self.server.auth.login("permanent-test-password", "attacker")
        self.assertEqual(caught.exception.status, 429)
        self.assertEqual(self.request("POST", "/api/auth/login", "{}", headers={"Origin": "https://untrusted.invalid"})[0], 403)

    @patch("app.socket.getaddrinfo", side_effect=public_dns)
    def test_cancel_retry_delete_and_deleted_pending_job(self, _dns):
        job = self.downloads.enqueue({"url": "https://youtu.be/test"})
        self.downloads.cancel(job["id"])
        self.assertEqual(self.downloads.jobs[job["id"]]["state"], "cancelled")
        retried = self.downloads.retry(job["id"])
        self.assertNotEqual(retried["id"], job["id"])
        self.downloads.delete(job["id"])
        self.downloads.run(job["id"])
        self.assertNotIn(job["id"], self.downloads.jobs)

    def test_upload_conversion_and_input_confinement(self):
        self.downloads.ffmpeg = "/usr/bin/ffmpeg"
        status, body, _ = self.request("POST", "/api/uploads?name=demo.wav", b"fake-audio",
                                       headers={"Content-Type": "application/octet-stream"})
        self.assertEqual(status, 201)
        upload = json.loads(body)
        job = self.downloads.enqueue({"kind": "convert", "upload_id": upload["upload_id"], "profile": "mp3"})
        self.assertEqual(job["kind"], "convert")
        for invalid_profile in ([], {}, None, "--exec"):
            self.assertEqual(self.request("POST", "/api/jobs", json.dumps({"kind": "convert", "upload_id": upload["upload_id"], "profile": invalid_profile}))[0], 400)
        for source in ("../../etc/passwd", "missing.mp4"):
            with self.assertRaises(ValueError):
                self.downloads.enqueue({"kind": "convert", "upload_id": source, "profile": "mp3"})
        self.assertEqual(self.request("POST", "/api/uploads?name=playlist.m3u8", b"fake")[0], 400)
        self.assertEqual(self.request("POST", "/api/uploads?name=demo.mp4", b"x",
                                     headers={"Content-Length": str(1024 ** 3 + 1)})[0], 413)

    def test_password_state_survives_restart_and_sessions_do_not(self):
        restored = AuthStore(self.server.auth.directory)
        self.assertTrue(restored.verify("permanent-test-password"))
        self.assertFalse(restored.record["must_change"])
        self.assertIsNone(restored.session("omniget_session=" + self.session))

    @patch("app.socket.getaddrinfo", side_effect=public_dns)
    def test_running_job_cancellation_stops_the_process(self, _dns):
        script = Path(self.temp.name) / "slow-cancellable-cli"
        script.write_text("#!/bin/sh\nsleep 30\n")
        script.chmod(0o700)
        self.downloads.cli = str(script)
        job = self.downloads.enqueue({"url": "https://youtu.be/test"})
        worker = threading.Thread(target=self.downloads.run, args=(job["id"],))
        worker.start()
        try:
            deadline = time.monotonic() + 3
            while job["id"] not in self.downloads.processes and time.monotonic() < deadline:
                time.sleep(.01)
            self.assertIn(job["id"], self.downloads.processes)
            self.downloads.cancel(job["id"])
            worker.join(timeout=3)
            self.assertFalse(worker.is_alive())
            self.assertEqual(self.downloads.jobs[job["id"]]["state"], "cancelled")
            self.assertNotIn(job["id"], self.downloads.processes)
        finally:
            if worker.is_alive():
                self.downloads.cancel(job["id"])
                worker.join(timeout=3)


if __name__ == "__main__":
    unittest.main()
