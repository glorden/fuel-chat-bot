import sqlite3
from datetime import datetime, timezone

from pipeline.extract import ReportItem


def already_processed(conn: sqlite3.Connection, peer_id: int, conversation_message_id: int) -> bool:
    row = conn.execute(
        "SELECT 1 FROM processed_message WHERE peer_id = ? AND conversation_message_id = ?",
        (peer_id, conversation_message_id),
    ).fetchone()
    return row is not None


def mark_processed(conn: sqlite3.Connection, peer_id: int, conversation_message_id: int) -> None:
    conn.execute(
        "INSERT OR IGNORE INTO processed_message (peer_id, conversation_message_id, processed_at) "
        "VALUES (?, ?, ?)",
        (peer_id, conversation_message_id, datetime.now(timezone.utc).isoformat()),
    )
    conn.commit()


def insert_fuel_report(
    conn: sqlite3.Connection,
    *,
    station_id: str,
    report: ReportItem,
    queue_note: str | None,
    peer_id: int,
    conversation_message_id: int,
    author_id: int,
    raw_text: str,
) -> None:
    conn.execute(
        "INSERT INTO fuel_report "
        "(station_id, fuel_grade, status, queue_note, peer_id, conversation_message_id, "
        " author_id, reported_at, raw_text) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
        (
            station_id,
            report.grade,
            report.status,
            queue_note,
            peer_id,
            conversation_message_id,
            author_id,
            datetime.now(timezone.utc).isoformat(),
            raw_text,
        ),
    )
    conn.commit()


def insert_unresolved_mention(
    conn: sqlite3.Connection,
    *,
    peer_id: int,
    conversation_message_id: int,
    author_id: int,
    raw_text: str,
) -> None:
    conn.execute(
        "INSERT INTO unresolved_mention "
        "(peer_id, conversation_message_id, author_id, seen_at, raw_text) VALUES (?, ?, ?, ?, ?)",
        (peer_id, conversation_message_id, author_id, datetime.now(timezone.utc).isoformat(), raw_text),
    )
    conn.commit()
