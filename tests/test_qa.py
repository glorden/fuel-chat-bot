import re
import sqlite3
from datetime import datetime, timedelta, timezone

from db.schema import get_connection
from pipeline.facts import MOSCOW_TZ
from pipeline.qa import answer_question


def _insert_report(
    conn: sqlite3.Connection,
    *,
    station_id: str,
    grade: str,
    status: str,
    queue_note: str | None,
    minutes_ago: float,
) -> None:
    reported_at = (datetime.now(timezone.utc) - timedelta(minutes=minutes_ago)).isoformat()
    conn.execute(
        "INSERT INTO fuel_report "
        "(station_id, fuel_grade, status, queue_note, peer_id, conversation_message_id, "
        " author_id, reported_at, raw_text) VALUES (?, ?, ?, ?, 1, 1, 1, ?, 'test')",
        (station_id, grade, status, queue_note, reported_at),
    )
    conn.commit()


def test_answer_question_unknown_station():
    # Станция не распознана — бот молчит (None), не пишет "не понял" в чат.
    conn = get_connection(":memory:")
    assert answer_question(conn, station_id=None, grades=["95"]) is None


def test_answer_question_no_data_for_station():
    # Станция известна, но по ней ещё нет отчётов — бот молчит (None),
    # а не пишет "нет данных" в чат.
    conn = get_connection(":memory:")
    assert answer_question(conn, station_id="tatneft_silikatny", grades=["95"]) is None


def test_answer_question_fresh_fact_has_no_staleness_caveat():
    conn = get_connection(":memory:")
    _insert_report(conn, station_id="lukoil_vilga", grade="92", status="available", queue_note="без очереди", minutes_ago=5)
    text = answer_question(conn, station_id="lukoil_vilga", grades=["92"])
    assert re.search(r"на \d{2}:\d{2}, без очереди\.", text)
    assert "92 - Есть" in text
    assert "устарело" not in text
    assert "старые" not in text


def test_answer_question_stale_and_very_stale_fact_show_caveats():
    # Пороги настроены под реальную динамику этого чата: свежо — до 4 часов,
    # устарело — 4-8 часов, старое — дальше (см. FRESH_MINUTES/STALE_MINUTES).
    conn = get_connection(":memory:")
    _insert_report(conn, station_id="lukoil_vilga", grade="92", status="available", queue_note=None, minutes_ago=300)
    text = answer_question(conn, station_id="lukoil_vilga", grades=["92"])
    assert "устарело" in text

    conn2 = get_connection(":memory:")
    _insert_report(conn2, station_id="lukoil_vilga", grade="92", status="available", queue_note=None, minutes_ago=500)
    text2 = answer_question(conn2, station_id="lukoil_vilga", grades=["92"])
    assert "старые" in text2


def test_answer_question_flags_conflicting_recent_reports():
    conn = get_connection(":memory:")
    _insert_report(conn, station_id="tatneft_silikatny", grade="95", status="unavailable", queue_note=None, minutes_ago=30)
    _insert_report(conn, station_id="tatneft_silikatny", grade="95", status="available", queue_note=None, minutes_ago=5)
    text = answer_question(conn, station_id="tatneft_silikatny", grades=["95"])
    assert "95 - Есть" in text
    assert "было иначе" in text


def test_answer_question_old_conflicting_report_not_flagged():
    conn = get_connection(":memory:")
    _insert_report(conn, station_id="tatneft_silikatny", grade="95", status="unavailable", queue_note=None, minutes_ago=999)
    _insert_report(conn, station_id="tatneft_silikatny", grade="95", status="available", queue_note=None, minutes_ago=5)
    text = answer_question(conn, station_id="tatneft_silikatny", grades=["95"])
    assert "было иначе" not in text


def test_answer_question_groups_same_status_grades_on_one_line():
    conn = get_connection(":memory:")
    _insert_report(conn, station_id="lukoil_vilga", grade="92", status="available", queue_note=None, minutes_ago=5)
    _insert_report(conn, station_id="lukoil_vilga", grade="95", status="available", queue_note=None, minutes_ago=5)
    _insert_report(conn, station_id="lukoil_vilga", grade="98", status="unavailable", queue_note=None, minutes_ago=5)
    text = answer_question(conn, station_id="lukoil_vilga", grades=["92", "95", "98"])
    assert "92, 95 - Есть" in text
    assert "98 - Нет" in text


def test_answer_question_header_time_matches_freshest_report():
    conn = get_connection(":memory:")
    _insert_report(conn, station_id="lukoil_vilga", grade="92", status="available", queue_note=None, minutes_ago=120)
    _insert_report(conn, station_id="lukoil_vilga", grade="95", status="available", queue_note=None, minutes_ago=5)
    text = answer_question(conn, station_id="lukoil_vilga", grades=["92", "95"])
    expected_time = (datetime.now(timezone.utc) - timedelta(minutes=5)).astimezone(MOSCOW_TZ).strftime("%H:%M")
    assert expected_time in text


def test_answer_question_differing_queue_notes_shown_per_group():
    conn = get_connection(":memory:")
    _insert_report(conn, station_id="lukoil_vilga", grade="92", status="available", queue_note="без очереди", minutes_ago=5)
    _insert_report(conn, station_id="lukoil_vilga", grade="95", status="available", queue_note="~15 мин", minutes_ago=5)
    text = answer_question(conn, station_id="lukoil_vilga", grades=["92", "95"])
    assert "92 - Есть, без очереди" in text
    assert "95 - Есть, ~15 мин" in text
