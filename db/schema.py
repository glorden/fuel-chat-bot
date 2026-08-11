import sqlite3
from pathlib import Path

DB_PATH = Path(__file__).resolve().parent.parent / "bot.db"

_SCHEMA = """
CREATE TABLE IF NOT EXISTS processed_message (
    peer_id INTEGER NOT NULL,
    conversation_message_id INTEGER NOT NULL,
    processed_at TEXT NOT NULL,
    PRIMARY KEY (peer_id, conversation_message_id)
);

CREATE TABLE IF NOT EXISTS fuel_report (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    station_id TEXT NOT NULL,
    fuel_grade TEXT NOT NULL,
    status TEXT NOT NULL,
    queue_note TEXT,
    peer_id INTEGER NOT NULL,
    conversation_message_id INTEGER NOT NULL,
    author_id INTEGER NOT NULL,
    reported_at TEXT NOT NULL,
    raw_text TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS unresolved_mention (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    peer_id INTEGER NOT NULL,
    conversation_message_id INTEGER NOT NULL,
    author_id INTEGER NOT NULL,
    seen_at TEXT NOT NULL,
    raw_text TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS station_break (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    station_id TEXT NOT NULL,
    kind TEXT,
    until TEXT,
    duration_note TEXT,
    peer_id INTEGER NOT NULL,
    conversation_message_id INTEGER NOT NULL,
    author_id INTEGER NOT NULL,
    reported_at TEXT NOT NULL,
    raw_text TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS fuel_limit (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    station_id TEXT NOT NULL,
    status TEXT NOT NULL,
    liters INTEGER,
    peer_id INTEGER NOT NULL,
    conversation_message_id INTEGER NOT NULL,
    author_id INTEGER NOT NULL,
    reported_at TEXT NOT NULL,
    raw_text TEXT NOT NULL
);
"""


def get_connection(path: Path = DB_PATH) -> sqlite3.Connection:
    conn = sqlite3.connect(path)
    conn.execute("PRAGMA journal_mode=WAL")
    conn.executescript(_SCHEMA)
    return conn
