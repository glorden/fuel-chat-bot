import sqlite3
from datetime import datetime, timezone

from config import FRESH_MINUTES, STALE_MINUTES
from pipeline.facts import StationBreak, StationFact, StationLimit
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
        reported_dt = datetime.fromisoformat(reported_at)
        age_minutes = (now - reported_dt).total_seconds() / 60

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
                reported_at=reported_dt,
                age_minutes=age_minutes,
                tier=_tier_for_age(age_minutes),
                conflicting=conflicting,
            )
        )
    return facts


def _latest_break(conn: sqlite3.Connection, station_id: str) -> StationBreak | None:
    """Последняя запись о перерыве по станции — без окна активности (по
    прямому решению пользователя): показывается всегда, вместе с возрастом,
    человек сам решает, актуально ли ещё."""
    row = conn.execute(
        "SELECT kind, until, duration_note, reported_at FROM station_break "
        "WHERE station_id = ? ORDER BY reported_at DESC LIMIT 1",
        (station_id,),
    ).fetchone()
    if row is None:
        return None
    kind, until_raw, duration_note, reported_at = row
    reported_dt = datetime.fromisoformat(reported_at)
    age_minutes = (datetime.now(timezone.utc) - reported_dt).total_seconds() / 60
    until_dt = datetime.fromisoformat(until_raw) if until_raw else None
    return StationBreak(kind=kind, until=until_dt, duration_note=duration_note, reported_minutes_ago=age_minutes)


def _latest_limit(conn: sqlite3.Connection, station_id: str) -> StationLimit | None:
    """Последняя запись о лимите отпуска по станции — без окна активности,
    та же логика, что у _latest_break: лимит показывается всегда, вместе
    с возрастом, человек сам решает, актуален ли ещё."""
    row = conn.execute(
        "SELECT status, liters, reported_at FROM fuel_limit "
        "WHERE station_id = ? ORDER BY reported_at DESC LIMIT 1",
        (station_id,),
    ).fetchone()
    if row is None:
        return None
    status, liters, reported_at = row
    age_minutes = (datetime.now(timezone.utc) - datetime.fromisoformat(reported_at)).total_seconds() / 60
    return StationLimit(status=status, liters=liters, reported_minutes_ago=age_minutes)


def answer_question(conn: sqlite3.Connection, *, station_id: str | None, grades: list[str]) -> str | None:
    """None означает "нечего ответить" — станция не распознана, или по ней
    нет ни отчётов по маркам, ни перерывов, ни лимита. В этом случае бот
    молчит, а не пишет в чат "не понял" или "нет данных"."""
    if station_id is None:
        return None
    facts = _latest_facts(conn, station_id, grades or None)
    break_info = _latest_break(conn, station_id)
    limit_info = _latest_limit(conn, station_id)
    if not facts and break_info is None and limit_info is None:
        return None
    return render_station_answer(get_station_name(station_id), facts, break_info, limit_info)
