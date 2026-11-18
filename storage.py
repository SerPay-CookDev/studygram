"""SQLite persistence for users, messages, and per-user chat hides."""

import hashlib
import json
import os
import sqlite3
from datetime import datetime, timezone

DB_PATH = os.path.abspath(os.getenv(
    "CHAT_DB_PATH", os.path.join(os.path.dirname(__file__), "chat.db")
))
DEFAULT_CHAT_FILE = r"\\192.168.13.30\обмен\24-28\chat.jsonl"
LEGACY_CHAT_FILE = os.getenv("LEGACY_CHAT_FILE", DEFAULT_CHAT_FILE)
LEGACY_USERS_FILE = os.getenv(
    "LEGACY_USERS_FILE",
    os.path.join(os.path.dirname(LEGACY_CHAT_FILE), "users.json")
)
ATTACH_DIR = os.getenv(
    "ATTACH_DIR", os.path.join(os.path.dirname(DB_PATH), "attachments")
)


def connect():
    """Open a short-lived connection to the local SQLite database file."""
    os.makedirs(os.path.dirname(DB_PATH), exist_ok=True)
    conn = sqlite3.connect(DB_PATH, timeout=10)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")
    return conn


def _now():
    return datetime.now(timezone.utc).isoformat(timespec="microseconds")


def _legacy_records():
    records = []
    try:
        with open(LEGACY_CHAT_FILE, encoding="utf-8") as source:
            for position, line in enumerate(source):
                if not line.strip():
                    continue
                try:
                    item = json.loads(line)
                except json.JSONDecodeError:
                    continue
                if isinstance(item, dict):
                    records.append((position, line, item))
    except (FileNotFoundError, OSError):
        pass
    return records


def _conversation_key(message):
    if message.get("chat_type") == "group":
        group_id = message.get("group_id") or message.get("recipient") or "unknown"
        return f"group:{str(group_id).casefold()}"
    participants = sorted({
        str(message.get("sender", "")).casefold(),
        str(message.get("recipient", "")).casefold()
    })
    return "dm:" + "|".join(participants)


def _migrate_legacy(conn):
    if conn.execute(
        "SELECT 1 FROM app_migrations WHERE migration_key = ?",
        ("legacy_json_import_v1",)
    ).fetchone():
        return

    try:
        with open(LEGACY_USERS_FILE, encoding="utf-8") as source:
            users = json.load(source)
    except (FileNotFoundError, OSError, json.JSONDecodeError):
        users = {}

    records = _legacy_records()
    base_time = _now()
    message_ids = {}

    for position, raw_line, item in records:
        if item.get("record_type") in {"delete_message", "delete_chat"}:
            continue
        sender = item.get("sender")
        recipient = item.get("recipient")
        if not sender or not recipient:
            continue

        message_id = str(item.get("id") or hashlib.sha256(
            f"{position}\0{raw_line}".encode("utf-8")
        ).hexdigest())
        message_ids[message_id] = item
        is_group = (item.get("chat_type") == "group"
                    or item.get("type") == "group_message")
        sent_at = datetime.now(timezone.utc).isoformat(timespec="microseconds")
        conn.execute(
            """INSERT OR IGNORE INTO messages
               (id, sender, recipient, chat_type, group_id, sent_at,
                time_label, body, file_name)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)""",
            (message_id, str(sender), str(recipient),
             "group" if is_group else "direct",
             str(item.get("group_id")) if item.get("group_id") is not None else None,
             sent_at, str(item.get("time", "")), str(item.get("text", "")),
             item.get("file"))
        )

    for position, _raw_line, item in records:
        record_type = item.get("record_type")
        if record_type == "delete_message":
            target = message_ids.get(str(item.get("message_id")))
            requester = str(item.get("requester", "")).casefold()
            if not target:
                continue
            sender = str(target.get("sender", "")).casefold()
            recipient = str(target.get("recipient", "")).casefold()
            is_group = (target.get("chat_type") == "group"
                        or target.get("type") == "group_message")
            if (is_group and requester == sender) or (
                    not is_group and requester in {sender, recipient}):
                conn.execute(
                    "UPDATE messages SET deleted = 1 WHERE id = ?",
                    (str(item.get("message_id")),)
                )
        elif record_type == "delete_chat":
            user_key = str(item.get("user", "")).casefold()
            chat_key = item.get("chat_key")
            if user_key and chat_key:
                conn.execute(
                    """INSERT INTO chat_hides (user_key, chat_key, cleared_at)
                       VALUES (?, ?, ?)
                       ON CONFLICT (user_key, chat_key)
                       DO UPDATE SET cleared_at = excluded.cleared_at""",
                    (user_key, str(chat_key), f"{base_time}#{position:012d}")
                )

    if isinstance(users, dict):
        for key, account in users.items():
            if not isinstance(account, dict) or not account.get("name"):
                continue
            conn.execute(
                """INSERT OR IGNORE INTO users
                   (username_key, name, salt, password_hash)
                   VALUES (?, ?, ?, ?)""",
                (str(key).casefold(), str(account["name"]),
                 str(account.get("salt", "")), str(account.get("hash", "")))
            )

    conn.execute(
        "INSERT INTO app_migrations (migration_key) VALUES (?)",
        ("legacy_json_import_v1",)
    )


def initialize():
    """Create the database schema and import old JSON data once if available."""
    with connect() as conn:
        conn.executescript("""
            CREATE TABLE IF NOT EXISTS users (
                username_key TEXT PRIMARY KEY,
                name TEXT NOT NULL,
                salt TEXT NOT NULL,
                password_hash TEXT NOT NULL
            );
            CREATE TABLE IF NOT EXISTS messages (
                id TEXT PRIMARY KEY,
                sender TEXT NOT NULL,
                recipient TEXT NOT NULL,
                chat_type TEXT NOT NULL DEFAULT 'direct',
                group_id TEXT,
                sent_at TEXT NOT NULL,
                time_label TEXT NOT NULL DEFAULT '',
                body TEXT NOT NULL DEFAULT '',
                file_name TEXT,
                deleted INTEGER NOT NULL DEFAULT 0
            );
            CREATE INDEX IF NOT EXISTS messages_sender_idx
                ON messages (sender COLLATE NOCASE, sent_at);
            CREATE INDEX IF NOT EXISTS messages_recipient_idx
                ON messages (recipient COLLATE NOCASE, sent_at);
            CREATE TABLE IF NOT EXISTS chat_hides (
                user_key TEXT NOT NULL,
                chat_key TEXT NOT NULL,
                cleared_at TEXT NOT NULL,
                PRIMARY KEY (user_key, chat_key)
            );
            CREATE TABLE IF NOT EXISTS app_migrations (
                migration_key TEXT PRIMARY KEY,
                completed_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
            );
        """)
        _migrate_legacy(conn)


def get_user(username_key):
    with connect() as conn:
        row = conn.execute(
            """SELECT name, salt, password_hash AS hash FROM users
               WHERE username_key = ?""",
            (username_key,)
        ).fetchone()
        return dict(row) if row else None


def add_user(username_key, name, salt, password_hash):
    with connect() as conn:
        cursor = conn.execute(
            """INSERT OR IGNORE INTO users
               (username_key, name, salt, password_hash) VALUES (?, ?, ?, ?)""",
            (username_key, name, salt, password_hash)
        )
        return cursor.rowcount == 1


def search_users(query, current_user_key, limit=8):
    with connect() as conn:
        rows = conn.execute(
            """SELECT name FROM users
               WHERE username_key <> ? AND instr(username_key, ?) > 0
               ORDER BY username_key LIMIT ?""",
            (current_user_key, query, limit)
        ).fetchall()
        return [row["name"] for row in rows]


def read_messages_for_user(username):
    user_key = username.casefold()
    with connect() as conn:
        rows = conn.execute(
            """SELECT id, sender, recipient, chat_type, group_id,
                      time_label AS time, body AS text, file_name AS file, sent_at
               FROM messages
               WHERE deleted = 0 AND
                     (sender = ? COLLATE NOCASE OR recipient = ? COLLATE NOCASE)
               ORDER BY sent_at, rowid""",
            (username, username)
        ).fetchall()
        hides = {
            row["chat_key"]: row["cleared_at"]
            for row in conn.execute(
                "SELECT chat_key, cleared_at FROM chat_hides WHERE user_key = ?",
                (user_key,)
            ).fetchall()
        }

    visible = []
    for result in rows:
        row = dict(result)
        cutoff = hides.get(_conversation_key(row))
        if cutoff is not None:
            # Imported JSON deletion events include a line position after '#'.
            cutoff_time = cutoff.split("#", 1)[0]
            if row["sent_at"] <= cutoff_time:
                continue
        row.pop("sent_at", None)
        visible.append(row)
    return visible


def save_message(message):
    is_group = message.get("chat_type") == "group"
    with connect() as conn:
        conn.execute(
            """INSERT INTO messages
               (id, sender, recipient, chat_type, group_id, sent_at,
                time_label, body, file_name)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)""",
            (str(message.get("id") or os.urandom(16).hex()),
             message["sender"], message["recipient"],
             "group" if is_group else "direct", message.get("group_id"),
             _now(), message.get("time", ""), message.get("text", ""),
             message.get("file"))
        )


def delete_message(message_id, requester):
    username = requester.casefold()
    with connect() as conn:
        cursor = conn.execute(
            """UPDATE messages SET deleted = 1
               WHERE id = ? AND deleted = 0
                 AND ((chat_type = 'group' AND sender = ? COLLATE NOCASE)
                   OR (chat_type <> 'group'
                       AND (sender = ? COLLATE NOCASE
                            OR recipient = ? COLLATE NOCASE)))""",
            (str(message_id), requester, requester, requester)
        )
        return cursor.rowcount == 1


def _hide_chat(username, chat_key):
    with connect() as conn:
        conn.execute(
            """INSERT INTO chat_hides (user_key, chat_key, cleared_at)
               VALUES (?, ?, ?)
               ON CONFLICT (user_key, chat_key)
               DO UPDATE SET cleared_at = excluded.cleared_at""",
            (username.casefold(), chat_key, _now())
        )


def delete_chat(username, peer):
    chat_key = "dm:" + "|".join(sorted({username.casefold(), peer.casefold()}))
    _hide_chat(username, chat_key)


def delete_user_chat_key(username, chat_key):
    _hide_chat(username, chat_key)
