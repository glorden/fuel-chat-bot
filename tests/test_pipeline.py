import sqlite3

from db.schema import get_connection
from pipeline.extract import ReportItem, extract
from pipeline.pipeline import process_message
from pipeline.prefilter import is_on_topic
from pipeline.resolve_station import resolve_station
from tests.fixtures import (
    OFF_TOPIC_OR_NO_SIGNAL,
    QUESTIONS,
    REPORTS,
    REPORTS_WITH_UNKNOWN_STATION,
)


def test_prefilter_flags_real_reports_and_questions_as_on_topic():
    for text in REPORTS + QUESTIONS:
        assert is_on_topic(text), text


def test_questions_classified_and_grades_extracted():
    expected_grades = {
        "Сейчас 95 где есть?": ["95"],
        "У суоярвского или другая?": [],
        "Не подскажите РН на Комсомольском есть 95?": ["95"],
        "Добрый вечер! Подскажите, пожалуйста, по наличию 92 Медгора - Пудож": ["92"],
        "Где в городе есть 95": ["95"],
        "На ТН на Шуйском шоссе есть 95?": ["95"],
        "Здравствуйте. Газпром есть 95? Может знает кто-нибудь \U0001F64F": ["95"],
        "Подскажите сколько лимит на Роснефти": [],
        "Скиньте инфу когда появится 95 в Вилге пжл.": ["95"],
        "Добрый вечер. У РБ на РН 95 на каких колонках есть?": ["95"],
        "Будет тех перерыв на РН на Лососинском с 21-22 ч?": [],
        "Здравствуйте всем! Подскажите есть ли на Роснефть на Лесном, 79 (возле поворота на объездную) 92-й?": ["92"],
        "Есть еще 95 на РН на Лососинском и нет ли тех перерыва?": ["95"],
    }
    for text, grades in expected_grades.items():
        result = extract(text)
        assert result.message_type == "question", text
        assert result.question_grades == grades, text


def test_reports_classified_with_grade_status_and_queue():
    result = extract("РН лесной, 95 есть на 4,5 колонке. Очередь.")
    assert result.message_type == "report"
    assert result.reports == [ReportItem("95", "available")]
    assert result.queue_note == "есть очередь"

    result = extract("В Янишполе появился 95-й бензин заправилась за 12 минут работают все колонки")
    assert result.reports[0].grade == "95"
    assert result.reports[0].status == "available"
    assert result.queue_note == "~12 мин"

    result = extract("Лукойл Вилга только 92 на табло, очереди нет")
    assert result.reports[0].grade == "92"
    assert result.reports[0].status == "available"
    assert result.queue_note == "без очереди"

    result = extract("Татнефть силикатный только дт")
    assert result.reports[0].grade == "ДТ"
    assert result.reports[0].status == "available"
    assert result.queue_note is None

    result = extract("Нет 95го в янишполе\n92 и ДТ")
    grades_status = {r.grade: r.status for r in result.reports}
    assert grades_status == {"95": "unavailable", "92": "available", "ДТ": "available"}

    result = extract("РН на лыжной-95 на 1 и 2 колонке")
    assert result.reports == [ReportItem("95", "available")]
    assert result.queue_note is None

    result = extract("опти на суоярвском 92-й без очереди и ограничений")
    assert result.reports == [ReportItem("92", "available")]
    assert result.queue_note == "без очереди"

    # Многоклаузное сообщение про две марки на одной АЗС. Обе марки и статусы
    # извлекаются верно; queue_note берётся по первому совпавшему сигналу на
    # весь текст ("нет очереди" из клаузы про 92) — известное упрощение:
    # per-клаузного разбора очереди по каждой марке отдельно пока нет.
    result = extract(
        "На РН лоссосинское около РБ 95 только на 1 и 2 колонке . "
        "92 нет очереди на остальных колонках . Очередь медленно идёт"
    )
    grades_status = {r.grade: r.status for r in result.reports}
    assert grades_status == {"95": "available", "92": "available"}
    assert result.queue_note == "без очереди"


def test_resolve_station_handles_typos_and_landmark_aliases():
    # Опечатка "лоссосинское" (реальная, из лога) всё равно резолвится.
    assert resolve_station("На РН лоссосинское около РБ 95 есть") == "rosneft_lososinskoe"
    # "РБ" как самостоятельный ориентир рядом с этой же станцией.
    assert resolve_station("Добрый вечер. У РБ на РН 95 на каких колонках есть?") == "rosneft_lososinskoe"
    assert resolve_station("Лукойл на лососинском, 95 есть") == "lukoil_lososinskoe"
    assert resolve_station("РН на лыжной-95 на 1 и 2 колонке") == "rosneft_lyzhnaya"


def test_reports_with_unknown_station_stay_unresolved():
    for text in REPORTS_WITH_UNKNOWN_STATION:
        assert resolve_station(text) is None, text


def test_resolve_station_matches_known_aliases():
    assert resolve_station("РН лесной, 95 есть на 4,5 колонке. Очередь.") == "rosneft_lesnoy_79"
    assert resolve_station("Лукойл Вилга только 92 на табло, очереди нет") == "lukoil_vilga"
    assert resolve_station("Татнефть силикатный только дт") == "tatneft_silikatny"
    # Подтверждено пользователем: "РН на Комсомольском" = "РН у Ленты" — одна и та же точка.
    assert resolve_station("Не подскажите РН на Комсомольском есть 95?") == "rosneft_komsomolsky"
    assert resolve_station("РН у Ленты, 95 есть") == "rosneft_komsomolsky"
    # Подтверждено: "суоярвский" и "на объездной" — локальные названия той же АЗС, что и "Лесной 79".
    assert resolve_station("Заправился на суоярвском, 92 есть") == "rosneft_lesnoy_79"
    assert resolve_station("РН на объездной, дт есть") == "rosneft_lesnoy_79"


def test_resolve_station_distinguishes_different_brands_on_same_road():
    assert resolve_station("Татнефть на Шуйском шоссе, 95 есть") == "tatneft_shuyskoe"
    assert resolve_station("Лукойл на Шуйском шоссе, 95 есть") == "lukoil_shuyskoe"
    assert resolve_station("Опти Шуйское, 95 есть") == "opti_shuyskoe"
    # ТН на Шуйском шоссе — это другая точка, не Татнефть Ключевая.
    assert resolve_station("На ТН на Шуйском шоссе есть 95?") == "tatneft_shuyskoe"


def test_resolve_station_does_not_guess_ambiguous_or_unknown_locations():
    # Голое упоминание бренда с несколькими точками без локации — не угадываем.
    assert resolve_station("На РН везде вроде") is None
    # Янишполе — посёлок, а не конкретная АЗС из газетира.
    assert resolve_station("В Янишполе появился 95-й бензин") is None


def test_off_topic_and_no_signal_messages_produce_no_structured_data():
    for text in OFF_TOPIC_OR_NO_SIGNAL:
        if is_on_topic(text):
            assert extract(text).message_type == "irrelevant", text


def test_process_message_end_to_end_against_temp_db():
    conn: sqlite3.Connection = get_connection(":memory:")

    outcome = process_message(
        conn, text="РН лесной, 95 есть на 4,5 колонке. Очередь.",
        peer_id=2000000001, conversation_message_id=1, author_id=111,
    )
    assert outcome == "report:rosneft_lesnoy_79"
    row = conn.execute("SELECT station_id, fuel_grade, status, queue_note FROM fuel_report").fetchone()
    assert row == ("rosneft_lesnoy_79", "95", "available", "есть очередь")

    outcome = process_message(
        conn, text="В Янишполе появился 95-й бензин заправилась за 12 минут работают все колонки",
        peer_id=2000000001, conversation_message_id=2, author_id=112,
    )
    assert outcome == "unresolved"
    row = conn.execute("SELECT raw_text FROM unresolved_mention").fetchone()
    assert row[0].startswith("В Янишполе")

    outcome = process_message(
        conn, text="Сейчас 95 где есть?",
        peer_id=2000000001, conversation_message_id=3, author_id=113,
    )
    assert outcome == "question"

    outcome = process_message(
        conn, text="Спасибо",
        peer_id=2000000001, conversation_message_id=4, author_id=111,
    )
    assert outcome == "off_topic"
