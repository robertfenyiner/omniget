"""Private notes and flashcards; no remote AI calls or HTML rendering."""
import json
from pathlib import Path
import sqlite3
import threading
import time
import uuid

TEXT_LIMIT = 2 * 1024 * 1024


def bounded_text(value, limit, label, empty=False):
    if not isinstance(value, str) or len(value.encode("utf-8")) > limit or (not empty and not value.strip()):
        raise ValueError(f"{label} no es válido o supera el tamaño máximo.")
    return value.strip()


class StudyStore:
    def __init__(self, directory):
        self.lock = threading.RLock()
        self.path = Path(directory) / "study.sqlite3"
        self.connection = sqlite3.connect(self.path, check_same_thread=False)
        self.connection.row_factory = sqlite3.Row
        self.connection.execute("PRAGMA foreign_keys=ON")
        self.connection.executescript("""
            CREATE TABLE IF NOT EXISTS notes (
                id TEXT PRIMARY KEY, title TEXT NOT NULL, body TEXT NOT NULL,
                created_at INTEGER NOT NULL, updated_at INTEGER NOT NULL
            );
            CREATE TABLE IF NOT EXISTS cards (
                id TEXT PRIMARY KEY, note_id TEXT REFERENCES notes(id) ON DELETE CASCADE,
                question TEXT NOT NULL, answer TEXT NOT NULL, due_at INTEGER NOT NULL,
                interval_days INTEGER NOT NULL DEFAULT 0, reviews INTEGER NOT NULL DEFAULT 0
            );
        """)
        self.path.chmod(0o600)

    def notes(self, query=""):
        query = bounded_text(query, 500, "La búsqueda", empty=True)
        with self.lock:
            rows = self.connection.execute("""SELECT id,title,created_at,updated_at,
                length(body) AS characters, substr(body,1,180) AS preview
                FROM notes WHERE title LIKE ? OR body LIKE ? ORDER BY updated_at DESC""",
                ("%" + query + "%", "%" + query + "%")).fetchall()
            return [dict(row) for row in rows]

    def note(self, note_id):
        with self.lock:
            row = self.connection.execute("SELECT * FROM notes WHERE id=?", (note_id,)).fetchone()
            if not row:
                raise ValueError("La nota no está disponible.")
            return dict(row)

    def save_note(self, payload):
        if not isinstance(payload, dict) or set(payload) - {"id", "title", "body"}:
            raise ValueError("Solicitud de nota no válida.")
        title = bounded_text(payload.get("title"), 250, "El título")
        body = bounded_text(payload.get("body"), TEXT_LIMIT, "El texto", empty=True)
        note_id = payload.get("id")
        if note_id is not None and (not isinstance(note_id, str) or len(note_id) != 32):
            raise ValueError("La nota no es válida.")
        now = int(time.time())
        with self.lock, self.connection:
            used = self.connection.execute("SELECT COALESCE(SUM(length(CAST(body AS BLOB))),0) FROM notes WHERE id != ?", (note_id or "",)).fetchone()[0]
            if used + len(body.encode("utf-8")) > 64 * 1024 * 1024:
                raise ValueError("La biblioteca de notas alcanzó su límite de 64 MB.")
            if note_id:
                self.note(note_id)
                self.connection.execute("UPDATE notes SET title=?,body=?,updated_at=? WHERE id=?", (title, body, now, note_id))
            else:
                if self.connection.execute("SELECT count(*) FROM notes").fetchone()[0] >= 1000:
                    raise ValueError("Se alcanzó el límite de 1000 notas.")
                note_id = uuid.uuid4().hex
                self.connection.execute("INSERT INTO notes VALUES (?,?,?,?,?)", (note_id, title, body, now, now))
            return self.note(note_id)

    def delete_note(self, note_id):
        with self.lock, self.connection:
            self.note(note_id)
            self.connection.execute("DELETE FROM notes WHERE id=?", (note_id,))

    def cards(self, note_id=None):
        with self.lock:
            sql = "SELECT * FROM cards" + (" WHERE note_id=?" if note_id else "") + " ORDER BY due_at,id"
            rows = self.connection.execute(sql, (note_id,) if note_id else ()).fetchall()
            return [dict(row, due=row["due_at"] <= time.time()) for row in rows]

    def save_card(self, payload):
        if not isinstance(payload, dict) or set(payload) - {"note_id", "question", "answer"}:
            raise ValueError("Solicitud de tarjeta no válida.")
        question = bounded_text(payload.get("question"), 4000, "La pregunta")
        answer = bounded_text(payload.get("answer"), 12000, "La respuesta")
        note_id = payload.get("note_id")
        if note_id == "":
            note_id = None
        if note_id is not None and not isinstance(note_id, str):
            raise ValueError("La nota no es válida.")
        card_id = uuid.uuid4().hex
        with self.lock, self.connection:
            if note_id:
                self.note(note_id)
            if self.connection.execute("SELECT count(*) FROM cards").fetchone()[0] >= 5000:
                raise ValueError("Se alcanzó el límite de 5000 tarjetas.")
            self.connection.execute("INSERT INTO cards VALUES (?,?,?,?,?,0,0)", (card_id, note_id, question, answer, int(time.time())))
        return {"id": card_id}

    def review(self, card_id, rating):
        if rating not in ("again", "good", "easy"):
            raise ValueError("La valoración no es válida.")
        with self.lock, self.connection:
            row = self.connection.execute("SELECT * FROM cards WHERE id=?", (card_id,)).fetchone()
            if not row:
                raise ValueError("La tarjeta no está disponible.")
            days = 0 if rating == "again" else min(365, max(1, row["interval_days"]) * (3 if rating == "easy" else 2))
            due = int(time.time()) + (600 if not days else days * 86400)
            self.connection.execute("UPDATE cards SET interval_days=?,due_at=?,reviews=reviews+1 WHERE id=?", (days, due, card_id))
            return {"due_at": due, "interval_days": days}

    def delete_card(self, card_id):
        with self.lock, self.connection:
            self.connection.execute("DELETE FROM cards WHERE id=?", (card_id,))

    def export(self):
        with self.lock:
            notes = [dict(row) for row in self.connection.execute("SELECT * FROM notes")]
            return json.dumps({"version": 1, "notes": notes, "cards": self.cards()}, ensure_ascii=False, indent=2).encode()
