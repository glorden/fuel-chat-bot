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


# Строка "не опровергнуто" для WHERE. Имя таблицы подставляется буквально,
# а не параметром: значения приходят только отсюда, из фиксированного
# набора, а SQLite не разрешает параметр внутри подзапроса на месте имени.
def _not_retracted(fact_table: str) -> str:
    return (
        "AND id NOT IN (SELECT fact_id FROM fact_retraction "
        f"WHERE fact_table = '{fact_table}')"
    )


def latest_report_ids(conn: sqlite3.Connection, *, station_id: str, grades: list[str]) -> list[tuple[int, str]]:
    """Строки, которые прямо сейчас формируют ответ по станции — по одной,
    самой свежей, на марку. Именно их и опровергает !ошибка: то, что бот
    сказал, а не всю историю станции."""
    rows = conn.execute(
        "SELECT id, fuel_grade FROM fuel_report WHERE station_id = ? "
        f"{_not_retracted('fuel_report')} ORDER BY reported_at DESC",
        (station_id,),
    ).fetchall()
    seen: dict[str, int] = {}
    for row_id, grade in rows:
        if grade in grades and grade not in seen:
            seen[grade] = row_id
    return [(row_id, grade) for grade, row_id in seen.items()]


def latest_break_id(conn: sqlite3.Connection, *, station_id: str) -> int | None:
    row = conn.execute(
        "SELECT id FROM station_break WHERE station_id = ? "
        f"{_not_retracted('station_break')} ORDER BY reported_at DESC LIMIT 1",
        (station_id,),
    ).fetchone()
    return row[0] if row else None


def latest_limit_id(conn: sqlite3.Connection, *, station_id: str) -> int | None:
    row = conn.execute(
        "SELECT id FROM fuel_limit WHERE station_id = ? "
        f"{_not_retracted('fuel_limit')} ORDER BY reported_at DESC LIMIT 1",
        (station_id,),
    ).fetchone()
    return row[0] if row else None


def insert_retraction(
    conn: sqlite3.Connection, *, fact_table: str, fact_id: int, station_id: str, retracted_by: int
) -> None:
    """Коммитит сама — как set_auto_reply_enabled: команда из лички идёт вне
    пайплайна, своей транзакции на сообщение у неё нет."""
    conn.execute(
        "INSERT INTO fact_retraction (fact_table, fact_id, station_id, retracted_by, retracted_at) "
        "VALUES (?, ?, ?, ?, ?)",
        (fact_table, fact_id, station_id, retracted_by, datetime.now(timezone.utc).isoformat()),
    )
    conn.commit()


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
