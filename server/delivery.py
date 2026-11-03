"""Persistent Telegram outbox for Cosmo tool jobs, separate from execution."""
import threading
import time


class DeliveryWorker:
    def __init__(self, downloads, telegram):
        self.downloads = downloads
        self.telegram = telegram
        self.lock = threading.Lock()

    def tick(self):
        # One sender, including manual retry requests. Download files are never
        # regenerated just because Telegram is temporarily unavailable.
        with self.lock:
            for snapshot in self.downloads.snapshot():
                outbox = snapshot.get("delivery")
                if (not outbox or outbox["state"] != "pending" or outbox.get("next_at", 0) > time.time()
                        or snapshot["state"] not in ("completed", "failed", "cancelled")):
                    continue
                job_id = snapshot["id"]
                try:
                    if snapshot["state"] == "completed":
                        files = [f for f in snapshot["files"] if f["name"] != "ocr-info.json"]
                        for file in files:
                            if file["name"] in outbox["sent"]:
                                continue
                            path = self.downloads.file_path(job_id, file["name"])
                            self.telegram.send_file(path, reply_to=outbox["reply_to"], media=True)
                            # Persist each acknowledgement before sending the next file.
                            with self.downloads.lock:
                                self.downloads.jobs[job_id]["delivery"]["sent"].append(file["name"])
                                self.downloads.save()
                        if "__summary__" not in outbox["sent"]:
                            names = ", ".join(f["name"] for f in files)
                            text = f"Cosmo · Completado\nID: {job_id}\nArchivos: {names or 'sin archivos'}"
                            if snapshot.get("kind") in ("document", "transcribe"):
                                name = "document.txt" if snapshot["kind"] == "document" else "transcript.txt"
                                text += f"\nGuardar como nota: /guardar {job_id} {name}"
                            self.telegram.send_text(text, reply_to=outbox["reply_to"])
                            with self.downloads.lock:
                                self.downloads.jobs[job_id]["delivery"]["sent"].append("__summary__")
                                self.downloads.save()
                    else:
                        state = "cancelado" if snapshot["state"] == "cancelled" else "fallido"
                        self.telegram.send_text(f"Cosmo · Trabajo {state}\nID: {job_id}\n"
                                                f"{snapshot.get('error') or ''}\n/reintentar {job_id}",
                                                reply_to=outbox["reply_to"])
                    with self.downloads.lock:
                        self.downloads.jobs[job_id]["delivery"].update(state="delivered", error=None)
                        self.downloads.save()
                except (ValueError, OSError):
                    with self.downloads.lock:
                        delivery = self.downloads.jobs.get(job_id, {}).get("delivery")
                        if delivery:
                            attempts = delivery["attempts"] + 1
                            delivery.update(attempts=attempts, state="blocked" if attempts >= 5 else "pending",
                                            next_at=time.time() + min(300, 5 * 2 ** attempts),
                                            error="No se pudo entregar. Usa /resultado ID para reintentar el envío.")
                            self.downloads.save()

    def work(self):
        while True:
            self.tick()
            time.sleep(3)
