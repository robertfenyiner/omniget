import http.client
import json
import socket
from pathlib import Path
import tempfile
import threading
import unittest
from unittest.mock import patch

from app import DownloadQueue, Handler, Server, validate_url

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
        self.server.token = "test-key-" * 8
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
            request_headers["Authorization"] = "Bearer " + self.server.token
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


if __name__ == "__main__":
    unittest.main()
