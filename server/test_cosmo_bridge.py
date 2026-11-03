import http.client
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import Mock, patch

from app import DownloadQueue
from bot_bridge import start_bridge
from delivery import DeliveryWorker
from study import StudyStore
from test_integrations import UnixConnection


class CosmoBridgeTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        config = self.root / "config.json"
        self.key = "test-private-bridge-key" * 3
        config.write_text(json.dumps({"key": self.key}))
        self.queue = DownloadQueue(self.root / "data", "/bin/true", start_worker=False)
        self.study = StudyStore(self.queue.root)
        self.server = start_bridge(config, self.queue, self.study)

    def tearDown(self):
        self.server.shutdown()
        self.server.server_close()
        self.study.connection.close()
        self.temp.cleanup()

    def request(self, method, path, data=None, headers=None, authorized=True):
        connection = UnixConnection(str(self.queue.root / "bridge/bridge.sock"))
        values = {"Content-Type": "application/json"}
        if authorized:
            values["X-OmniGet-Key"] = self.key
        values.update(headers or {})
        body = data if isinstance(data, bytes) else json.dumps(data).encode() if data is not None else None
        connection.request(method, path, body, values)
        response = connection.getresponse()
        result = response.status, json.loads(response.read())
        connection.close()
        return result

    def upload(self):
        status, result = self.request("POST", "/upload", b"Private fixture text", {"X-Filename": "../../document.txt"})
        self.assertEqual(status, 201)
        return result["upload_id"]

    def test_stream_upload_scope_filename_and_permissions(self):
        self.assertEqual(self.request("POST", "/upload", b"x", {"X-Filename": "test.txt"}, authorized=False)[0], 401)
        upload_id = self.upload()
        file = self.queue.upload_path(upload_id)
        self.assertEqual(file.parent, self.queue.root / "uploads")
        self.assertEqual(file.stat().st_mode & 0o777, 0o600)
        self.assertEqual(file.read_bytes(), b"Private fixture text")
        self.assertEqual(self.request("POST", "/upload", b"x", {"X-Filename": "test.exe"})[0], 400)
        self.assertEqual(self.request("POST", "/upload", b"", {"X-Filename": "test.txt"})[0], 400)
        self.assertEqual(self.request("POST", "/upload", b"x", {"X-Filename": "test.txt", "Content-Length": str(100 * 1024 * 1024 + 1)})[0], 400)

    def test_tools_return_results_with_persisted_delivery_context(self):
        upload_id = self.upload()
        status, job = self.request("POST", "/tool", {"kind": "document", "upload_id": upload_id, "reply_to": 42})
        self.assertEqual(status, 202)
        self.queue.run(job["id"])
        self.assertEqual(self.queue.jobs[job["id"]]["state"], "completed")
        bot = Mock()
        worker = DeliveryWorker(self.queue, bot)
        worker.tick()
        bot.send_file.assert_called_once()
        self.assertEqual(bot.send_file.call_args.kwargs, {"reply_to": 42, "media": True})
        restarted = DownloadQueue(self.queue.root, "/bin/true", start_worker=False)
        self.assertEqual(restarted.jobs[job["id"]]["delivery"]["state"], "delivered")
        DeliveryWorker(restarted, bot).tick()
        bot.send_file.assert_called_once()

    def test_queue_rejects_forged_context_and_remote_options(self):
        upload_id = self.upload()
        for extra in ({"reply_to": -1}, {"reply_to": True}, {"reply_to": "42"}, {"reply_to": 42, "args": "--exec=id"}):
            status, _ = self.request("POST", "/tool", dict(kind="document", upload_id=upload_id, **extra))
            self.assertEqual(status, 400)
        self.assertEqual(self.request("POST", "/tool", {"kind": "shell", "reply_to": 42})[0], 400)
        self.assertEqual(self.request("POST", "/retry", {"id": [], "reply_to": 42})[0], 400)

    def test_duplicate_tool_requests_reuse_job(self):
        payload = {"kind": "document", "upload_id": self.upload(), "reply_to": 42, "request_id": "42:document:default"}
        _, first = self.request("POST", "/tool", payload)
        _, second = self.request("POST", "/tool", payload)
        self.assertEqual(first["id"], second["id"])
        self.assertEqual(len(self.queue.jobs), 1)

    def test_notes_cards_export_and_review_share_web_store(self):
        status, note = self.request("POST", "/notes", {"title": "Study", "body": "Fixture body"})
        self.assertEqual(status, 201)
        self.assertEqual(self.study.note(note["id"])["body"], "Fixture body")
        self.assertEqual(len(self.request("GET", "/notes?q=Fixture")[1]), 1)
        _, card = self.request("POST", "/cards", {"question": "Q", "answer": "A", "note_id": note["id"]})
        self.assertEqual(self.request("POST", "/review", {"id": card["id"], "rating": "good"})[0], 200)
        self.assertFalse(self.request("GET", "/cards")[1][0]["due"])
        self.assertEqual(self.request("GET", "/study-export")[1]["notes"][0]["id"], note["id"])
        self.assertEqual(self.request("GET", "/api/auth/status")[0], 404)
        self.assertEqual(self.request("GET", "/study-export", authorized=False)[0], 401)

    def test_udemy_missing_session_is_honest_and_scoped(self):
        from service_sessions import ServiceSessions
        self.queue.sessions = ServiceSessions(self.root / "sessions", self.root / "runtime")
        status, result = self.request("GET", "/udemy/courses")
        self.assertEqual(status, 400)
        self.assertIn("error", result)
        self.assertEqual(self.request("GET", "/udemy/courses?page=1000")[0], 400)
        self.assertFalse(self.request("GET", "/services")[1]["udemy_verified"])
        self.assertEqual(self.request("GET", "/sessions/cookies")[0], 404)

    def test_retry_preserves_delivery_and_cancelled_jobs_notify(self):
        upload_id = self.upload()
        _, job = self.request("POST", "/tool", {"kind": "document", "upload_id": upload_id, "reply_to": 42})
        self.assertEqual(self.request("POST", "/cancel", {"id": job["id"]})[0], 200)
        bot = Mock()
        DeliveryWorker(self.queue, bot).tick()
        bot.send_file.assert_not_called()
        self.assertIn("cancelado", bot.send_text.call_args.args[0])
        status, retry = self.request("POST", "/retry", {"id": job["id"], "reply_to": 99})
        self.assertEqual(status, 202)
        self.assertEqual(self.queue.jobs[retry["id"]]["delivery"]["reply_to"], 99)

    def test_delivery_retries_only_unsent_files_after_restart(self):
        job = self.queue.add_job({"kind": "document"}, 42)
        folder = self.queue.root / job["id"] / "files"
        folder.mkdir(parents=True)
        (folder / "a.txt").write_text("a")
        (folder / "b.txt").write_text("b")
        self.queue.jobs[job["id"]]["state"] = "completed"
        bot = Mock()
        bot.send_file.side_effect = [None, ValueError("Telegram unavailable")]
        DeliveryWorker(self.queue, bot).tick()
        self.assertEqual(self.queue.jobs[job["id"]]["delivery"]["sent"], ["a.txt"])
        restarted = DownloadQueue(self.queue.root, "/bin/true", start_worker=False)
        restarted.jobs[job["id"]]["delivery"]["next_at"] = 0
        bot2 = Mock()
        DeliveryWorker(restarted, bot2).tick()
        self.assertEqual(bot2.send_file.call_args.args[0].name, "b.txt")
        self.assertEqual(restarted.jobs[job["id"]]["delivery"]["state"], "delivered")

    def test_delivery_exhaustion_can_be_retried_without_redownload(self):
        job = self.queue.add_job({"kind": "document"}, 42)
        self.queue.jobs[job["id"]]["state"] = "failed"
        bot = Mock()
        bot.send_text.side_effect = ValueError("offline")
        worker = DeliveryWorker(self.queue, bot)
        for _ in range(5):
            self.queue.jobs[job["id"]]["delivery"]["next_at"] = 0
            worker.tick()
        self.assertEqual(self.queue.jobs[job["id"]]["delivery"]["state"], "blocked")
        status, _ = self.request("POST", "/redeliver", {"id": job["id"], "reply_to": 100})
        self.assertEqual(status, 202)
        self.assertEqual(self.queue.jobs[job["id"]]["delivery"]["reply_to"], 100)
        self.assertEqual(self.request("POST", "/redeliver", {"id": job["id"], "reply_to": 100})[0], 400)

    def test_pending_delivery_protects_files_from_deletion(self):
        job = self.queue.add_job({"kind": "document"}, 42)
        self.queue.jobs[job["id"]]["state"] = "completed"
        with self.assertRaises(ValueError):
            self.queue.delete(job["id"])


if __name__ == "__main__":
    unittest.main()
