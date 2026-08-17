import sqlite3
from datetime import datetime, timezone

from pipeline.extract import BreakInfo, LimitInfo, ReportItem
from pipeline.facts import QueueInfo


# Записи, относящиеся к одному входящему сообщению (факты + отметка
# "обработано"), должны ложиться ОДНОЙ транзакцией: иначе сбой между ними
# оставляет либо половину факта (находка D5), либо сообщение, помеченное
# обработанным без единой записанной строки (находка G1). Поэтому функции
# ниже не коммитят сами — транзакцией владеет вызывающий, см.
# pipeline/pipeline.py::process_message и его блок `with conn:`.
# Исключение — set_auto_reply_enabled: она вызывается вне пайплайна и
# коммитит сама.


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


def insert_fuel_report(
    conn: sqlite3.Connection,
    *,
    station_id: str,
    report: ReportItem,
    queue: QueueInfo | None,
    peer_id: int,
    conversation_message_id: int,
    author_hash: str,
    reported_at: datetime,
    source: str,
    raw_text: str,
) -> None:
    """Легаси-колонка `queue_note` намеренно не заполняется: свободного
    текста про очередь больше не существует (см. db/schema.py)."""
    conn.execute(
        "INSERT INTO fuel_report "
        "(station_id, fuel_grade, status, queue_status, queue_minutes, queue_cars_from, "
        " queue_cars_to, peer_id, conversation_message_id, "
        " author_id, author_hash, reported_at, raw_text, source) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, 0, ?, ?, ?, ?)",
        (
            station_id,
            report.grade,
            report.status,
            queue.status if queue else None,
            queue.minutes if queue else None,
            queue.cars_from if queue else None,
            queue.cars_to if queue else None,
            peer_id,
            conversation_message_id,
            author_hash,
            reported_at.isoformat(),
            raw_text,
            source,
        ),
    )


def insert_station_break(
    conn: sqlite3.Connection,
    *,
    station_id: str,
    break_info: BreakInfo,
    peer_id: int,
    conversation_message_id: int,
    author_hash: str,
    reported_at: datetime,
    source: str,
    raw_text: str,
) -> None:
    conn.execute(
        "INSERT INTO station_break "
        "(station_id, kind, until, duration_note, peer_id, conversation_message_id, "
        " author_id, author_hash, reported_at, raw_text, source) VALUES (?, ?, ?, ?, ?, ?, 0, ?, ?, ?, ?)",
        (
            station_id,
            break_info.kind,
            break_info.until,
            break_info.duration_note,
            peer_id,
            conversation_message_id,
            author_hash,
            reported_at.isoformat(),
            raw_text,
            source,
        ),
    )


def insert_fuel_limit(
    conn: sqlite3.Connection,
    *,
    station_id: str,
    limit_info: LimitInfo,
    peer_id: int,
    conversation_message_id: int,
    author_hash: str,
    reported_at: datetime,
    source: str,
    raw_text: str,
) -> None:
    conn.execute(
        "INSERT INTO fuel_limit "
        "(station_id, status, liters, peer_id, conversation_message_id, "
        " author_id, author_hash, reported_at, raw_text, source) VALUES (?, ?, ?, ?, ?, 0, ?, ?, ?, ?)",
        (
            station_id,
            limit_info.status,
            limit_info.liters,
            peer_id,
            conversation_message_id,
            author_hash,
            reported_at.isoformat(),
            raw_text,
            source,
        ),
    )


def set_auto_reply_enabled(conn: sqlite3.Connection, *, enabled: bool, changed_by: int) -> None:
    conn.execute(
        "INSERT INTO auto_reply_setting (enabled, changed_by, changed_at) VALUES (?, ?, ?)",
        (int(enabled), changed_by, datetime.now(timezone.utc).isoformat()),
    )
    conn.commit()


def get_auto_reply_enabled(conn: sqlite3.Connection, *, default: bool) -> bool:
    """Последняя строка побеждает (append-only, как station_break/fuel_limit).
    default — значение из .env (AUTO_REPLY_ON_QUESTION), используется, пока
    !вкл/!выкл не сказали ни разу с момента создания этой таблицы."""
    row = conn.execute(
        "SELECT enabled FROM auto_reply_setting ORDER BY id DESC LIMIT 1"
    ).fetchone()
    return bool(row[0]) if row is not None else default


def set_moderation_enabled(conn: sqlite3.Connection, *, enabled: bool, changed_by: int) -> None:
    """Как set_auto_reply_enabled: вне пайплайна, поэтому коммитит сама."""
    conn.execute(
        "INSERT INTO moderation_setting (enabled, changed_by, changed_at) VALUES (?, ?, ?)",
        (int(enabled), changed_by, datetime.now(timezone.utc).isoformat()),
    )
    conn.commit()


def get_moderation_enabled(conn: sqlite3.Connection, *, default: bool) -> bool:
    """Последняя строка побеждает. default — MODERATION_ON_REPLY из .env,
    пока командой !модерация не воспользовались ни разу."""
    row = conn.execute(
        "SELECT enabled FROM moderation_setting ORDER BY id DESC LIMIT 1"
    ).fetchone()
    return bool(row[0]) if row is not None else default


def insert_unresolved_mention(
    conn: sqlite3.Connection,
    *,
    peer_id: int,
    conversation_message_id: int,
    author_hash: str,
    seen_at: datetime,
    raw_text: str,
) -> None:
    conn.execute(
        "INSERT INTO unresolved_mention "
        "(peer_id, conversation_message_id, author_id, author_hash, seen_at, raw_text) VALUES (?, ?, 0, ?, ?, ?)",
        (peer_id, conversation_message_id, author_hash, seen_at.isoformat(), raw_text),
    )
