import json
import re
import sqlite3
import uuid
from contextlib import contextmanager
from datetime import datetime, timezone
from pathlib import Path

DATA_DIR = Path("data")
DB_PATH = DATA_DIR / "app.db"
WORKSPACE = Path("workspace")

SCHEMA = """
CREATE TABLE IF NOT EXISTS users (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  slug TEXT UNIQUE NOT NULL,
  name TEXT NOT NULL,
  created_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS conversations (
  id TEXT PRIMARY KEY,
  user_id INTEGER NOT NULL REFERENCES users(id),
  title TEXT NOT NULL,
  inputs TEXT NOT NULL DEFAULT '{}',
  created_at TEXT NOT NULL,
  updated_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS messages (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  conversation_id TEXT NOT NULL REFERENCES conversations(id),
  role TEXT NOT NULL,
  content TEXT NOT NULL,
  meta TEXT NOT NULL DEFAULT '{}',
  created_at TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_conv_user ON conversations(user_id, updated_at DESC);
CREATE INDEX IF NOT EXISTS idx_msg_conv ON messages(conversation_id, id);
"""


def _now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


@contextmanager
def db():
    DATA_DIR.mkdir(exist_ok=True)
    conn = sqlite3.connect(DB_PATH, timeout=10)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")
    try:
        yield conn
        conn.commit()
    finally:
        conn.close()


def init_db() -> None:
    with db() as c:
        c.executescript(SCHEMA)


# ---------- users ----------
def slugify(name: str) -> str:
    return re.sub(r"[^a-z0-9]+", "-", name.strip().lower()).strip("-")[:40]


def get_user_by_slug(slug: str):
    with db() as c:
        row = c.execute("SELECT * FROM users WHERE slug = ?", (slug,)).fetchone()
    return dict(row) if row else None


def get_or_create_user(name: str) -> dict:
    name = " ".join(name.split())[:60]
    slug = slugify(name)
    if not slug:
        raise ValueError("Please enter a name with letters or numbers.")
    user = get_user_by_slug(slug)
    if user:
        return user
    with db() as c:
        c.execute("INSERT INTO users(slug, name, created_at) VALUES (?, ?, ?)", (slug, name, _now()))
    return get_user_by_slug(slug)


# ---------- conversations ----------
def conv_dir(user_slug: str, conv_id: str) -> Path:
    return WORKSPACE / user_slug / conv_id


def _conv(row) -> dict:
    conv = dict(row)
    conv["inputs"] = json.loads(conv["inputs"] or "{}")
    return conv


def get_conversation(user: dict, conv_id: str):
    with db() as c:
        row = c.execute(
            "SELECT * FROM conversations WHERE id = ? AND user_id = ?", (conv_id, user["id"])
        ).fetchone()
    return _conv(row) if row else None


def create_conversation(user: dict) -> dict:
    conv_id, now = uuid.uuid4().hex[:12], _now()
    with db() as c:
        c.execute(
            "INSERT INTO conversations(id, user_id, title, created_at, updated_at) VALUES (?, ?, 'New chat', ?, ?)",
            (conv_id, user["id"], now, now),
        )
    for sub in ("uploads", "output"):
        (conv_dir(user["slug"], conv_id) / sub).mkdir(parents=True, exist_ok=True)
    return get_conversation(user, conv_id)


def new_or_empty_conversation(user: dict) -> dict:
    """Reuse an untouched empty chat instead of piling up blank ones."""
    with db() as c:
        row = c.execute(
            """SELECT id FROM conversations
               WHERE user_id = ? AND inputs = '{}'
                 AND NOT EXISTS (SELECT 1 FROM messages m WHERE m.conversation_id = conversations.id)
               ORDER BY created_at DESC LIMIT 1""",
            (user["id"],),
        ).fetchone()
    return get_conversation(user, row["id"]) if row else create_conversation(user)


def list_conversations(user: dict) -> list:
    with db() as c:
        rows = c.execute(
            "SELECT id, title, updated_at FROM conversations WHERE user_id = ? ORDER BY updated_at DESC",
            (user["id"],),
        ).fetchall()
    return [dict(r) for r in rows]


def set_title(conv_id: str, title: str) -> None:
    with db() as c:
        c.execute("UPDATE conversations SET title = ? WHERE id = ?", (" ".join(title.split())[:50], conv_id))


def set_input(conv_id: str, kind: str, value: str) -> None:
    with db() as c:
        row = c.execute("SELECT inputs FROM conversations WHERE id = ?", (conv_id,)).fetchone()
        inputs = json.loads(row["inputs"] or "{}")
        inputs[kind] = value
        c.execute(
            "UPDATE conversations SET inputs = ?, updated_at = ? WHERE id = ?",
            (json.dumps(inputs), _now(), conv_id),
        )


# ---------- messages ----------
def add_message(conv_id: str, role: str, content: str, meta: dict | None = None) -> None:
    now = _now()
    with db() as c:
        c.execute(
            "INSERT INTO messages(conversation_id, role, content, meta, created_at) VALUES (?, ?, ?, ?, ?)",
            (conv_id, role, content, json.dumps(meta or {}), now),
        )
        c.execute("UPDATE conversations SET updated_at = ? WHERE id = ?", (now, conv_id))


def get_messages(conv_id: str) -> list:
    with db() as c:
        rows = c.execute(
            "SELECT id, role, content, meta FROM messages WHERE conversation_id = ? ORDER BY id", (conv_id,)
        ).fetchall()
    return [{**dict(r), "meta": json.loads(r["meta"] or "{}")} for r in rows]


def count_user_messages(conv_id: str) -> int:
    with db() as c:
        return c.execute(
            "SELECT COUNT(*) FROM messages WHERE conversation_id = ? AND role = 'user'", (conv_id,)
        ).fetchone()[0]


# ---------- files ----------
def list_files(user_slug: str, conv_id: str) -> list:
    base, items = conv_dir(user_slug, conv_id), []
    for group in ("uploads", "output"):
        for f in sorted((base / group).glob("*")):
            if f.is_file():
                stat = f.stat()
                items.append({"group": group, "name": f.name, "path": f.as_posix(),
                              "size": stat.st_size, "modified": stat.st_mtime})
    return items