import re
import sqlite3
from datetime import datetime, timedelta, timezone

from db.schema import get_connection
from pipeline.facts import MOSCOW_TZ, QueueInfo
from pipeline.qa import answer_brand_limit_question, answer_question


def _insert_report(
    conn: sqlite3.Connection,
    *,
    station_id: str,
    grade: str,
    status: str,
    queue: QueueInfo | None,
    minutes_ago: float,
) -> None:
    reported_at = (datetime.now(timezone.utc) - timedelta(minutes=minutes_ago)).isoformat()
    conn.execute(
        "INSERT INTO fuel_report "
        "(station_id, fuel_grade, status, queue_status, queue_minutes, queue_cars_from, "
        " queue_cars_to, peer_id, conversation_message_id, "
        " author_id, reported_at, raw_text) VALUES (?, ?, ?, ?, ?, ?, ?, 1, 1, 1, ?, 'test')",
        (
            station_id,
            grade,
            status,
            queue.status if queue else None,
            queue.minutes if queue else None,
            queue.cars_from if queue else None,
            queue.cars_to if queue else None,
            reported_at,
        ),
    )
    conn.commit()


def _insert_break(
    conn: sqlite3.Connection,
    *,
    station_id: str,
    kind: str | None,
    until: str | None,
    duration_note: str | None,
    minutes_ago: float,
) -> None:
    reported_at = (datetime.now(timezone.utc) - timedelta(minutes=minutes_ago)).isoformat()
    conn.execute(
        "INSERT INTO station_break "
        "(station_id, kind, until, duration_note, peer_id, conversation_message_id, "
        " author_id, reported_at, raw_text) VALUES (?, ?, ?, ?, 1, 1, 1, ?, 'test')",
        (station_id, kind, until, duration_note, reported_at),
    )
    conn.commit()


def _insert_limit(
    conn: sqlite3.Connection,
    *,
    station_id: str,
    status: str,
    liters: int | None,
    minutes_ago: float,
) -> None:
    reported_at = (datetime.now(timezone.utc) - timedelta(minutes=minutes_ago)).isoformat()
    conn.execute(
        "INSERT INTO fuel_limit "
        "(station_id, status, liters, peer_id, conversation_message_id, "
        " author_id, reported_at, raw_text) VALUES (?, ?, ?, 1, 1, 1, ?, 'test')",
        (station_id, status, liters, reported_at),
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
    _insert_report(conn, station_id="lukoil_vilga", grade="92", status="available", queue=QueueInfo(status="none"), minutes_ago=5)
    text = answer_question(conn, station_id="lukoil_vilga", grades=["92"])
    assert re.search(r"^инфа на \d{2}-\d{2}\.", text)
    assert "92 - Есть" in text
    assert "устарело" not in text
    assert "старые" not in text
    # Про очередь не спрашивали — её в ответе нет, хотя в базе она есть.
    assert "очеред" not in text


def test_answer_question_stale_and_very_stale_fact_show_caveats():
    # Пороги настроены под реальную динамику этого чата: свежо — до 4 часов,
    # устарело — 4-8 часов, старое — дальше (см. FRESH_MINUTES/STALE_MINUTES).
    conn = get_connection(":memory:")
    _insert_report(conn, station_id="lukoil_vilga", grade="92", status="available", queue=None, minutes_ago=300)
    text = answer_question(conn, station_id="lukoil_vilga", grades=["92"])
    assert "устарело" in text

    conn2 = get_connection(":memory:")
    _insert_report(conn2, station_id="lukoil_vilga", grade="92", status="available", queue=None, minutes_ago=500)
    text2 = answer_question(conn2, station_id="lukoil_vilga", grades=["92"])
    assert "старые" in text2


def test_answer_question_flags_conflicting_recent_reports():
    conn = get_connection(":memory:")
    _insert_report(conn, station_id="tatneft_silikatny", grade="95", status="unavailable", queue=None, minutes_ago=30)
    _insert_report(conn, station_id="tatneft_silikatny", grade="95", status="available", queue=None, minutes_ago=5)
    text = answer_question(conn, station_id="tatneft_silikatny", grades=["95"])
    assert "95 - Есть" in text
    assert "было иначе" in text


def test_answer_question_old_conflicting_report_not_flagged():
    conn = get_connection(":memory:")
    _insert_report(conn, station_id="tatneft_silikatny", grade="95", status="unavailable", queue=None, minutes_ago=999)
    _insert_report(conn, station_id="tatneft_silikatny", grade="95", status="available", queue=None, minutes_ago=5)
    text = answer_question(conn, station_id="tatneft_silikatny", grades=["95"])
    assert "было иначе" not in text


def test_answer_question_groups_same_status_grades_on_one_line():
    conn = get_connection(":memory:")
    _insert_report(conn, station_id="lukoil_vilga", grade="92", status="available", queue=None, minutes_ago=5)
    _insert_report(conn, station_id="lukoil_vilga", grade="95", status="available", queue=None, minutes_ago=5)
    text = answer_question(conn, station_id="lukoil_vilga", grades=["92", "95"])
    assert "92, 95 - Есть" in text


def test_answer_question_splits_grades_with_different_statuses():
    conn = get_connection(":memory:")
    _insert_report(conn, station_id="lukoil_vilga", grade="92", status="available", queue=None, minutes_ago=5)
    _insert_report(conn, station_id="lukoil_vilga", grade="95", status="unavailable", queue=None, minutes_ago=5)
    text = answer_question(conn, station_id="lukoil_vilga", grades=["92", "95"])
    assert "92 - Есть" in text
    assert "95 - Нет" in text


def test_answer_question_header_time_matches_freshest_report():
    conn = get_connection(":memory:")
    _insert_report(conn, station_id="lukoil_vilga", grade="92", status="available", queue=None, minutes_ago=120)
    _insert_report(conn, station_id="lukoil_vilga", grade="95", status="available", queue=None, minutes_ago=5)
    text = answer_question(conn, station_id="lukoil_vilga", grades=["92", "95"])
    expected_time = (datetime.now(timezone.utc) - timedelta(minutes=5)).astimezone(MOSCOW_TZ).strftime("%H-%M")
    assert expected_time in text


def test_answer_question_differing_queue_notes_shown_per_group():
    conn = get_connection(":memory:")
    _insert_report(conn, station_id="lukoil_vilga", grade="92", status="available", queue=QueueInfo(status="none"), minutes_ago=5)
    _insert_report(conn, station_id="lukoil_vilga", grade="95", status="available", queue=QueueInfo(status="present", minutes=15), minutes_ago=5)
    text = answer_question(
        conn, station_id="lukoil_vilga", grades=["92", "95"], question_text="какая очередь?"
    )
    assert "92 - Есть, без очереди" in text
    assert "95 - Есть, ~15 мин" in text


def test_queue_is_absent_when_nobody_asked_about_it():
    """Раньше очередь всегда ехала в ответ и заодно дробила группы марок."""
    conn = get_connection(":memory:")
    _insert_report(conn, station_id="lukoil_vilga", grade="92", status="available", queue=QueueInfo(status="none"), minutes_ago=5)
    _insert_report(conn, station_id="lukoil_vilga", grade="95", status="available", queue=QueueInfo(status="present", minutes=15), minutes_ago=5)
    text = answer_question(conn, station_id="lukoil_vilga", grades=["92", "95"], question_text="95 есть?")
    assert "92, 95 - Есть" in text
    assert "очеред" not in text
    assert "мин" not in text


def test_answer_question_shows_break_with_no_fuel_facts_at_all():
    # Раньше "станция известна, но нет отчётов по маркам" всегда означало
    # молчание — теперь перерыв без единого отчёта по маркам тоже повод
    # содержательно ответить.
    conn = get_connection(":memory:")
    _insert_break(conn, station_id="lukoil_vilga", kind="слив", until=None, duration_note="минут 40", minutes_ago=10)
    text = answer_question(conn, station_id="lukoil_vilga", grades=[], question_text="есть перерыв?")
    assert text is not None
    assert "слив бензовоза" in text
    assert "минут 40" in text


def test_answer_question_shows_break_alongside_fuel_facts():
    conn = get_connection(":memory:")
    _insert_report(conn, station_id="lukoil_vilga", grade="92", status="available", queue=None, minutes_ago=5)
    _insert_break(conn, station_id="lukoil_vilga", kind="перерыв", until=None, duration_note=None, minutes_ago=5)
    text = answer_question(
        conn, station_id="lukoil_vilga", grades=["92"], question_text="92 есть, перерыв не начался?"
    )
    assert "технический перерыв" in text
    assert "92 - Есть" in text


def test_break_is_absent_when_nobody_asked_about_it():
    """Разворот прежнего решения «перерыв и марки показываем вместе»
    (Этап 42): спросили про 92 — отвечаем про 92."""
    conn = get_connection(":memory:")
    _insert_report(conn, station_id="lukoil_vilga", grade="92", status="available", queue=None, minutes_ago=5)
    _insert_break(conn, station_id="lukoil_vilga", kind="перерыв", until=None, duration_note=None, minutes_ago=5)
    text = answer_question(conn, station_id="lukoil_vilga", grades=["92"], question_text="92 есть?")
    assert "92 - Есть" in text
    assert "перерыв" not in text


def test_a_fuel_truck_question_counts_as_asking_about_a_break():
    conn = get_connection(":memory:")
    _insert_break(conn, station_id="lukoil_vilga", kind="слив", until=None, duration_note=None, minutes_ago=5)
    text = answer_question(conn, station_id="lukoil_vilga", grades=[], question_text="бензовоз приезжал?")
    assert text is not None
    assert "слив бензовоза" in text


def test_answer_question_break_shown_regardless_of_age_no_active_window():
    # Прямое следствие решения пользователя "без окна активности" — даже
    # 10-часовой давности перерыв, о завершении которого никто не написал,
    # всё ещё упоминается (с возрастом — человек сам решает, актуально ли).
    conn = get_connection(":memory:")
    _insert_break(conn, station_id="lukoil_vilga", kind="слив", until=None, duration_note=None, minutes_ago=600)
    text = answer_question(conn, station_id="lukoil_vilga", grades=[], question_text="перерыв есть?")
    assert text is not None
    assert "слив бензовоза" in text
    assert "10 ч" in text


def test_answer_question_break_with_until_shows_clock_time_not_age():
    conn = get_connection(":memory:")
    until = (datetime.now(timezone.utc) + timedelta(minutes=30)).isoformat()
    _insert_break(conn, station_id="gazprom", kind="перерыв", until=until, duration_note=None, minutes_ago=5)
    text = answer_question(conn, station_id="gazprom", grades=[], question_text="перерыв до скольки?")
    assert "ожидается до" in text


def test_answer_question_uses_latest_break_not_older_one():
    conn = get_connection(":memory:")
    _insert_break(conn, station_id="lukoil_vilga", kind="слив", until=None, duration_note="минут 20", minutes_ago=60)
    _insert_break(conn, station_id="lukoil_vilga", kind="отстой", until=None, duration_note="минут 40", minutes_ago=5)
    text = answer_question(conn, station_id="lukoil_vilga", grades=[], question_text="перерыв?")
    assert "отстой топлива" in text
    assert "слив бензовоза" not in text


def test_answer_question_shows_limit_with_no_fuel_facts_at_all():
    conn = get_connection(":memory:")
    _insert_limit(conn, station_id="lukoil_vilga", status="limited", liters=30, minutes_ago=10)
    text = answer_question(conn, station_id="lukoil_vilga", grades=[], question_text="какой лимит?")
    assert text is not None
    assert "лимит 30 л" in text


def test_answer_question_shows_unlimited_status():
    conn = get_connection(":memory:")
    _insert_limit(conn, station_id="gazprom", status="unlimited", liters=None, minutes_ago=10)
    text = answer_question(conn, station_id="gazprom", grades=[], question_text="есть ограничение?")
    assert "лимита нет" in text


def test_limit_is_absent_when_nobody_asked_about_it():
    """Живой повод (Этап 42): в базе лежал «лимит 8 л», вычитанный из «на 8
    часов только ДТ», и уезжал в каждый ответ по станции."""
    conn = get_connection(":memory:")
    _insert_report(conn, station_id="lukoil_vilga", grade="92", status="available", queue=None, minutes_ago=5)
    _insert_limit(conn, station_id="lukoil_vilga", status="limited", liters=8, minutes_ago=5)
    text = answer_question(conn, station_id="lukoil_vilga", grades=["92"], question_text="92 есть?")
    assert "92 - Есть" in text
    assert "лимит" not in text


def test_answer_question_shows_limit_alongside_fuel_facts_and_break():
    conn = get_connection(":memory:")
    _insert_report(conn, station_id="lukoil_vilga", grade="92", status="available", queue=None, minutes_ago=5)
    _insert_break(conn, station_id="lukoil_vilga", kind="перерыв", until=None, duration_note=None, minutes_ago=5)
    _insert_limit(conn, station_id="lukoil_vilga", status="limited", liters=20, minutes_ago=5)
    text = answer_question(
        conn, station_id="lukoil_vilga", grades=["92"], question_text="92 есть? перерыв? лимит какой?"
    )
    assert "92 - Есть" in text
    assert "технический перерыв" in text
    assert "лимит 20 л" in text


def test_answer_question_uses_latest_limit_not_older_one():
    conn = get_connection(":memory:")
    _insert_limit(conn, station_id="lukoil_vilga", status="limited", liters=20, minutes_ago=60)
    _insert_limit(conn, station_id="lukoil_vilga", status="unlimited", liters=None, minutes_ago=5)
    text = answer_question(conn, station_id="lukoil_vilga", grades=[], question_text="лимит какой?")
    assert "лимита нет" in text
    assert "лимит 20 л" not in text


def test_diesel_never_appears_in_an_answer():
    """ДТ отслеживаем, но не отвечаем: чат про бензин (решение Этапа 42)."""
    conn = get_connection(":memory:")
    _insert_report(conn, station_id="lukoil_vilga", grade="92", status="available", queue=None, minutes_ago=5)
    _insert_report(conn, station_id="lukoil_vilga", grade="ДТ", status="available", queue=None, minutes_ago=5)
    text = answer_question(conn, station_id="lukoil_vilga", grades=[], question_text="что есть?")
    assert "92 - Есть" in text
    assert "ДТ" not in text


def test_a_question_only_about_diesel_gets_silence():
    conn = get_connection(":memory:")
    _insert_report(conn, station_id="lukoil_vilga", grade="ДТ", status="available", queue=None, minutes_ago=5)
    assert answer_question(conn, station_id="lukoil_vilga", grades=["ДТ"], question_text="дт есть?") is None


def test_a_question_only_about_98_gets_silence():
    """98 и 100 убраны из отслеживания целиком (Этап 42). Старые строки в
    базе остались — append-only, — но в ответ не попадают.

    Ловушка, найденная при живом прогоне: раз 98 выпала из извлечения,
    вопрос «98 есть?» приходит с ПУСТЫМ question_grades, то есть выглядит
    как вопрос вообще без марки — и без отдельной проверки получал бы
    бодрый ответ про 92 и 95."""
    conn = get_connection(":memory:")
    _insert_report(conn, station_id="lukoil_vilga", grade="92", status="available", queue=None, minutes_ago=5)
    assert answer_question(conn, station_id="lukoil_vilga", grades=[], question_text="98 есть?") is None
    assert answer_question(conn, station_id="lukoil_vilga", grades=[], question_text="а 100 бенз?") is None


def test_a_tracked_grade_next_to_an_untracked_one_is_still_answered():
    conn = get_connection(":memory:")
    _insert_report(conn, station_id="lukoil_vilga", grade="95", status="available", queue=None, minutes_ago=5)
    text = answer_question(conn, station_id="lukoil_vilga", grades=["95"], question_text="95 и 98 есть?")
    assert "95 - Есть" in text


def test_a_break_question_mentioning_98_still_gets_the_break():
    """Молчим про марку, а не про станцию: вопрос про слив остаётся вопросом
    про слив, даже если рядом названа марка, которую мы не отслеживаем."""
    conn = get_connection(":memory:")
    _insert_break(conn, station_id="lukoil_vilga", kind="слив", until=None, duration_note=None, minutes_ago=5)
    text = answer_question(conn, station_id="lukoil_vilga", grades=[], question_text="98 после слива будет?")
    assert text is not None
    assert "слив бензовоза" in text


def test_old_98_rows_do_not_leak_into_a_general_question():
    conn = get_connection(":memory:")
    _insert_report(conn, station_id="lukoil_vilga", grade="98", status="available", queue=None, minutes_ago=5)
    _insert_report(conn, station_id="lukoil_vilga", grade="92", status="available", queue=None, minutes_ago=5)
    text = answer_question(conn, station_id="lukoil_vilga", grades=[], question_text="что есть?")
    assert "92 - Есть" in text
    assert "98" not in text


def test_answer_brand_limit_question_real_chat_phrasings():
    # "Подскажите сколько лимит на Роснефти" и "Татнефть сколько заливают?" —
    # обе реальные формулировки из чата (см. tests/fixtures.py, QUESTIONS и
    # комментарий к LIMIT_REPORTS).
    assert answer_brand_limit_question("Подскажите сколько лимит на Роснефти") == "Роснефть: лимит 30 л в одни руки."
    assert answer_brand_limit_question("Татнефть сколько заливают?") == "Татнефть: лимит 50 л в одни руки."


def test_answer_brand_limit_question_matches_user_example_phrasings():
    assert answer_brand_limit_question("сколько дают залить на рн?") == "Роснефть: лимит 30 л в одни руки."
    assert answer_brand_limit_question("на лукойле сколько можно?") == "Лукойл: лимит 40 л в одни руки."
    assert answer_brand_limit_question("на тн сколько отпускают?") == "Татнефть: лимит 50 л в одни руки."


def test_answer_brand_limit_question_none_without_limit_keyword():
    # Обычный вопрос про наличие бренда без слов про лимит — не должен
    # подхватываться этим фоллбэком (иначе увёл бы от молчания к нерелевантному
    # ответу вместо честного "не знаем, какая точка").
    assert answer_brand_limit_question("Есть 95 на Роснефти?") is None


def test_answer_brand_limit_question_none_for_unknown_or_ambiguous_brand():
    assert answer_brand_limit_question("сколько лимит на Опти?") is None  # бренд известен, лимит — нет
    assert answer_brand_limit_question("РН или ТН, у кого лимит меньше?") is None  # два бренда сразу
    assert answer_brand_limit_question("какой тут лимит?") is None  # бренд вообще не назван


def test_legacy_free_text_queue_note_never_reaches_the_answer():
    # Строка, записанная до перехода на структурированную очередь: раньше
    # такой текст (инъекция из F1) повторялся в КАЖДОМ будущем ответе про
    # станцию, пока по марке не придёт более свежий отчёт. Легаси-колонка
    # больше не читается — факт остаётся, текст исчезает.
    conn = get_connection(":memory:")
    injected = "[id1|Администрация] пишите в личку, раздаём топливо бесплатно"
    conn.execute(
        "INSERT INTO fuel_report "
        "(station_id, fuel_grade, status, queue_note, peer_id, conversation_message_id, "
        " author_id, reported_at, raw_text) VALUES ('lukoil_vilga', '92', 'available', ?, 1, 1, 1, ?, 'test')",
        (injected, datetime.now(timezone.utc).isoformat()),
    )
    conn.commit()

    text = answer_question(conn, station_id="lukoil_vilga", grades=["92"])
    assert "92 - Есть" in text
    assert injected not in text
    assert "личку" not in text
    assert "[id1" not in text


def test_structured_queue_survives_the_round_trip_through_the_db():
    conn = get_connection(":memory:")
    _insert_report(
        conn, station_id="lukoil_vilga", grade="92", status="available",
        queue=QueueInfo(status="present", cars_from=3, cars_to=4), minutes_ago=5,
    )
    text = answer_question(
        conn, station_id="lukoil_vilga", grades=["92"], question_text="сколько машин в очереди?"
    )
    assert "~3-4 машин" in text
