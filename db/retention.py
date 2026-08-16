"""Сроки жизни данных участников (решение Р7, находки D7/F4/K1).

Что здесь важно понимать про append-only. Принцип проекта — не переписывать
факты, а копить их; он защищает *факты*. Истечение срока у сырого текста и
идентификаторов — другое: сам факт (станция, марка, статус, время) остаётся
нетронутым навсегда, стирается только то, по чему можно узнать человека.
Это записанное исключение, а не тихое нарушение принципа.
"""

import logging
import sqlite3
from datetime import datetime, timedelta, timezone

from config import PROCESSED_MESSAGE_TTL_DAYS, RAW_TEXT_TTL_DAYS

log = logging.getLogger("vk_bot")

# Таблица -> колонка со временем записи.
_FACT_TABLES = (
    ("fuel_report", "reported_at"),
    ("station_break", "reported_at"),
    ("fuel_limit", "reported_at"),
    ("unresolved_mention", "seen_at"),
)


def apply_retention(conn: sqlite3.Connection, *, now: datetime | None = None) -> dict[str, int]:
    """Стирает всё, чему вышел срок. Возвращает счётчики — их печатает
    вызывающий, чтобы работа была видна в логе, а не происходила молча."""
    now = now or datetime.now(timezone.utc)
    text_cutoff = (now - timedelta(days=RAW_TEXT_TTL_DAYS)).isoformat()
    dedup_cutoff = (now - timedelta(days=PROCESSED_MESSAGE_TTL_DAYS)).isoformat()

    stats = {"dedup_deleted": 0, "texts_cleared": 0, "authors_cleared": 0}
    with conn:
        # Дедуп нужен только для недавних сообщений: единственная таблица,
        # где хранение старых строк не имеет даже теоретического смысла.
        stats["dedup_deleted"] = conn.execute(
            "DELETE FROM processed_message WHERE processed_at < ?", (dedup_cutoff,)
        ).rowcount

        for table, time_column in _FACT_TABLES:
            # raw_text объявлен NOT NULL, поэтому пустая строка, а не NULL.
            stats["texts_cleared"] += conn.execute(
                f"UPDATE {table} SET raw_text = '' WHERE raw_text != '' AND {time_column} < ?",
                (text_cutoff,),
            ).rowcount
            # Исторические строки с числовым VK-идентификатором: новые
            # пишутся уже с нулём и отпечатком в author_hash (см. db/repo.py),
            # а эти остались с прошлых версий.
            stats["authors_cleared"] += conn.execute(
                f"UPDATE {table} SET author_id = 0 WHERE author_id != 0 AND {time_column} < ?",
                (text_cutoff,),
            ).rowcount

    if any(stats.values()):
        log.info(
            "Ретенция: удалено записей дедупа %s, очищено текстов %s, обезличено авторов %s",
            stats["dedup_deleted"], stats["texts_cleared"], stats["authors_cleared"],
        )
    return stats
