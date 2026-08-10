import sqlite3
from datetime import datetime, timezone

from config import FRESH_MINUTES, STALE_MINUTES
from pipeline.facts import StationFact
from pipeline.resolve_station import get_station_name
from templates import render_station_answer


def _tier_for_age(age_minutes: float) -> str:
    if age_minutes <= FRESH_MINUTES:
        return "fresh"
    if age_minutes <= STALE_MINUTES:
        return "stale"
    return "very_stale"


def _latest_facts(conn: sqlite3.Connection, station_id: str, grades: list[str] | None) -> list[StationFact]:
    query = "SELECT fuel_grade, status, queue_note, reported_at FROM fuel_report WHERE station_id = ?"
    params: list = [station_id]
    if grades:
        query += f" AND fuel_grade IN ({','.join('?' for _ in grades)})"
        params.extend(grades)
    query += " ORDER BY reported_at DESC"

    # Берём максимум 2 последних отчёта на марку: самый свежий — источник
    # факта, предыдущий — только чтобы понять, не противоречат ли они друг другу.
    by_grade: dict[str, list[tuple]] = {}
    for row in conn.execute(query, params).fetchall():
        bucket = by_grade.setdefault(row[0], [])
        if len(bucket) < 2:
            bucket.append(row)

    now = datetime.now(timezone.utc)
    facts = []
    for grade, rows in by_grade.items():
        _, status, queue_note, reported_at = rows[0]
        age_minutes = (now - datetime.fromisoformat(reported_at)).total_seconds() / 60

        conflicting = False
        if len(rows) == 2:
            _, prev_status, _, prev_reported_at = rows[1]
            prev_age = (now - datetime.fromisoformat(prev_reported_at)).total_seconds() / 60
            conflicting = prev_status != status and prev_age <= STALE_MINUTES

        facts.append(
            StationFact(
                grade=grade,
                status=status,
                queue_note=queue_note,
                age_minutes=age_minutes,
                tier=_tier_for_age(age_minutes),
                conflicting=conflicting,
            )
        )
    return facts


def answer_question(conn: sqlite3.Connection, *, station_id: str | None, grades: list[str]) -> str | None:
    """None означает "нечего ответить" — станция не распознана или по ней
    вообще нет отчётов. В этом случае бот молчит, а не пишет в чат "не понял"
    или "нет данных"."""
    if station_id is None:
        return None
    facts = _latest_facts(conn, station_id, grades or None)
    if not facts:
        return None
    return render_station_answer(get_station_name(station_id), facts)
