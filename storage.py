"""PostgreSQL persistence for users, messages, and per-user chat hides."""

import hashlib
import json
import os
import uuid
from datetime import datetime, timedelta, timezone

import psycopg
from psycopg.rows import dict_row

DEFAULT_CHAT_FILE = r"\\192.168.13.30\обмен\24-28\chat.jsonl"
LEGACY_CHAT_FILE = os.getenv("LEGACY_CHAT_FILE", DEFAULT_CHAT_FILE)
LEGACY_USERS_FILE = os.getenv(
    "LEGACY_USERS_FILE",
    os.path.join(os.path.dirname(LEGACY_CHAT_FILE), "users.json")
)


def connect():
    """Use DATABASE_URL or libpq's standard PGHOST/PGDATABASE/etc. variables."""
    return psycopg.connect(os.getenv("DATABASE_URL", ""), row_factory=dict_row)


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
    except FileNotFoundError:
        pass
    return records


def _migrate_legacy(conn):
    with conn.cursor() as cur:
        cur.execute("SELECT 1 FROM app_migrations WHERE migration_key = %s",
                    ("legacy_json_import_v1",))
        if cur.fetchone():
            return

    try:
        with open(LEGACY_USERS_FILE, encoding="utf-8") as source:
            users = json.load(source)
    except FileNotFoundError:
        users = {}

    records = _legacy_records()
    base_time = datetime.now(timezone.utc)
    message_ids = {}
    for position, raw_line, item in records:
        kind = item.get("record_type")
        if kind in {"delete_message", "delete_chat"}:
            continue
        sender = item.get("sender")
        recipient = item.get("recipient")
        if not sender or not recipient:
            continue
        message_id = item.get("id") or hashlib.sha256(
            f"{position}\0{raw_line}".encode("utf-8")
        ).hexdigest()
        message_ids[message_id] = item
        is_group = (item.get("chat_type") == "group"
                    or item.get("type") == "group_message")
        group_id = item.get("group_id")
        chat_type = "group" if is_group else "direct"
        sent_at = base_time + timedelta(microseconds=position)
        with conn.cursor() as cur:
            cur.execute(
                """INSERT INTO messages
                   (id, sender, recipient, chat_type, group_id, sent_at,
                    time_label, body, file_name)
                   VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s)
                   ON CONFLICT (id) DO NOTHING""",
                (str(message_id), str(sender), str(recipient), chat_type,
                 str(group_id) if group_id is not None else None, sent_at,
                 str(item.get("time", "")), str(item.get("text", "")),
                 item.get("file"))
            )

    for position, _raw_line, item in records:
        if item.get("record_type") == "delete_message":
            target = message_ids.get(item.get("message_id"))
            requester = str(item.get("requester", "")).casefold()
            if not target:
                continue
            sender = str(target.get("sender", "")).casefold()
            recipient = str(target.get("recipient", "")).casefold()
            is_group = (target.get("chat_type") == "group"
                        or target.get("type") == "group_message")
            if (is_group and requester == sender) or (
                    not is_group and requester in {sender, recipient}):
                with conn.cursor() as cur:
                    cur.execute("UPDATE messages SET deleted = TRUE WHERE id = %s",
                                (str(item.get("message_id")),))
        elif item.get("record_type") == "delete_chat":
            user_key = str(item.get("user", "")).casefold()
            chat_key = item.get("chat_key")
            if user_key and chat_key:
                cleared_at = base_time + timedelta(microseconds=position)
                with conn.cursor() as cur:
                    cur.execute(
                        """INSERT INTO chat_hides (user_key, chat_key, cleared_at)
                           VALUES (%s, %s, %s)
                           ON CONFLICT (user_key, chat_key)
                           DO UPDATE SET cleared_at = EXCLUDED.cleared_at""",
                        (user_key, str(chat_key), cleared_at)
                    )

    with conn.cursor() as cur:
        if isinstance(users, dict):
            for key, account in users.items():
                if not isinstance(account, dict) or not account.get("name"):
                    continue
                cur.execute(
                    """INSERT INTO users (username_key, name, salt, password_hash)
                       VALUES (%s, %s, %s, %s)
                       ON CONFLICT (username_key) DO NOTHING""",
                    (str(key).casefold(), str(account["name"]),
                     str(account.get("salt", "")),
                     str(account.get("hash", "")))
                )
        cur.execute(
            "INSERT INTO app_migrations (migration_key) VALUES (%s)",
            ("legacy_json_import_v1",)
        )


def initialize():
    """Create the schema and import the old JSON files once, if present."""
    with connect() as conn:
        with conn.cursor() as cur:
            cur.execute("""
                CREATE TABLE IF NOT EXISTS users (
                    username_key TEXT PRIMARY KEY,
                    name TEXT NOT NULL,
                    salt TEXT NOT NULL,
                    password_hash TEXT NOT NULL
                )
            """)
            cur.execute("""
                CREATE TABLE IF NOT EXISTS messages (
                    id TEXT PRIMARY KEY,
                    sender TEXT NOT NULL,
                    recipient TEXT NOT NULL,
                    chat_type TEXT NOT NULL DEFAULT 'direct',
                    group_id TEXT,
                    sent_at TIMESTAMPTZ NOT NULL DEFAULT CURRENT_TIMESTAMP,
                    time_label TEXT NOT NULL DEFAULT '',
                    body TEXT NOT NULL DEFAULT '',
                    file_name TEXT,
                    deleted BOOLEAN NOT NULL DEFAULT FALSE
                )
            """)
            cur.execute("""
                CREATE INDEX IF NOT EXISTS messages_sender_idx
                ON messages (lower(sender), sent_at)
            """)
            cur.execute("""
                CREATE INDEX IF NOT EXISTS messages_recipient_idx
                ON messages (lower(recipient), sent_at)
            """)
            cur.execute("""
                CREATE TABLE IF NOT EXISTS chat_hides (
                    user_key TEXT NOT NULL,
                    chat_key TEXT NOT NULL,
                    cleared_at TIMESTAMPTZ NOT NULL,
                    PRIMARY KEY (user_key, chat_key)
                )
            """)
            cur.execute("""
                CREATE TABLE IF NOT EXISTS app_migrations (
                    migration_key TEXT PRIMARY KEY,
                    completed_at TIMESTAMPTZ NOT NULL DEFAULT CURRENT_TIMESTAMP
                )
            """)
        _migrate_legacy(conn)


def get_user(username_key):
    with connect() as conn, conn.cursor() as cur:
        cur.execute(
            "SELECT name, salt, password_hash AS hash FROM users WHERE username_key = %s",
            (username_key,)
        )
        return cur.fetchone()


def add_user(username_key, name, salt, password_hash):
    with connect() as conn, conn.cursor() as cur:
        cur.execute(
            """INSERT INTO users (username_key, name, salt, password_hash)
               VALUES (%s, %s, %s, %s)
               ON CONFLICT (username_key) DO NOTHING
               RETURNING username_key""",
            (username_key, name, salt, password_hash)
        )
        return cur.fetchone() is not None


def search_users(query, current_user_key, limit=8):
    with connect() as conn, conn.cursor() as cur:
        cur.execute(
            """SELECT name FROM users
               WHERE username_key <> %s AND position(%s in username_key) > 0
               ORDER BY username_key LIMIT %s""",
            (current_user_key, query, limit)
        )
        return [row["name"] for row in cur.fetchall()]


def _conversation_key(message):
    if message.get("chat_type") == "group":
        return f"group:{str(message.get('group_id') or message.get('recipient') or 'unknown').casefold()}"
    participants = sorted({str(message.get("sender", "")).casefold(),
                           str(message.get("recipient", "")).casefold()})
    return "dm:" + "|".join(participants)


def read_messages_for_user(username):
    user_key = username.casefold()
    with connect() as conn, conn.cursor() as cur:
        cur.execute(
            """SELECT id, sender, recipient, chat_type, group_id,
                      time_label AS time, body AS text, file_name AS file, sent_at
               FROM messages
               WHERE deleted = FALSE
                 AND (lower(sender) = %s OR lower(recipient) = %s)
               ORDER BY sent_at, id""",
            (user_key, user_key)
        )
        rows = cur.fetchall()
        cur.execute("SELECT chat_key, cleared_at FROM chat_hides WHERE user_key = %s",
                    (user_key,))
        hides = {row["chat_key"]: row["cleared_at"] for row in cur.fetchall()}

    visible = []
    for row in rows:
        cutoff = hides.get(_conversation_key(row))
        if cutoff is not None and row["sent_at"] <= cutoff:
            continue
        row.pop("sent_at", None)
        visible.append(row)
    return visible


def save_message(message):
    is_group = message.get("chat_type") == "group"
    with connect() as conn, conn.cursor() as cur:
        cur.execute(
            """INSERT INTO messages
               (id, sender, recipient, chat_type, group_id, time_label, body, file_name)
               VALUES (%s, %s, %s, %s, %s, %s, %s, %s)""",
            (str(message.get("id") or uuid.uuid4().hex), message["sender"],
             message["recipient"], "group" if is_group else "direct",
             message.get("group_id"), message.get("time", ""),
             message.get("text", ""), message.get("file"))
        )


def delete_message(message_id, requester):
    user_key = requester.casefold()
    with connect() as conn, conn.cursor() as cur:
        cur.execute(
            """UPDATE messages SET deleted = TRUE
               WHERE id = %s AND deleted = FALSE
                 AND ((chat_type = 'group' AND lower(sender) = %s)
                   OR (chat_type <> 'group'
                       AND (lower(sender) = %s OR lower(recipient) = %s)))
               RETURNING id""",
            (str(message_id), user_key, user_key, user_key)
        )
        return cur.fetchone() is not None


def delete_chat(username, peer):
    user_key = username.casefold()
    chat_key = "dm:" + "|".join(sorted({user_key, peer.casefold()}))
    with connect() as conn, conn.cursor() as cur:
        cur.execute(
            """INSERT INTO chat_hides (user_key, chat_key, cleared_at)
               VALUES (%s, %s, clock_timestamp())
               ON CONFLICT (user_key, chat_key)
               DO UPDATE SET cleared_at = clock_timestamp()""",
            (user_key, chat_key)
        )


def delete_user_chat_key(username, chat_key):
    """Set a per-user cutoff for a chat key, including future group support."""
    with connect() as conn, conn.cursor() as cur:
        cur.execute(
            """INSERT INTO chat_hides (user_key, chat_key, cleared_at)
               VALUES (%s, %s, clock_timestamp())
               ON CONFLICT (user_key, chat_key)
               DO UPDATE SET cleared_at = clock_timestamp()""",
            (username.casefold(), chat_key)
        )
