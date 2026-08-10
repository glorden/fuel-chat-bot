import sqlite3

import pipeline.pipeline as pipeline_module
from db.schema import get_connection
from pipeline.extract import ReportItem, extract
from pipeline.pipeline import process_message
from pipeline.prefilter import is_on_topic
from pipeline.resolve_station import resolve_station
from tests.fixtures import (
    GENERIC_AVAILABILITY_NOT_CAPTURED_BY_RULES,
    OFF_TOPIC_OR_NO_SIGNAL,
    QUESTIONS,
    REPORTS,
    REPORTS_WITH_UNKNOWN_STATION,
)


def test_prefilter_flags_real_reports_and_questions_as_on_topic():
    for text in REPORTS + QUESTIONS:
        assert is_on_topic(text), text


def test_prefilter_catches_grade_with_ordinal_suffix_without_hyphen():
    # Реальный баг, найден 2026-08-10: "95й"/"98е" без дефиса не матчили
    # цифровую ветку паттерна (не было \w* после 9[258]/100, в отличие от
    # всех остальных веток) — "92-й" с дефисом работал только случайно,
    # т.к. сам дефис уже не-\w и даёт границу слова.
    assert is_on_topic("На 1й и 2й только 95е топливо")
    assert is_on_topic("95й есть")
    assert is_on_topic("100й пропал")


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
        "Татнефть на объездной, есть 92-95?": ["92", "95"],
        "Подскажите пожалуйста Лукойл на Заводской есть 92?": ["92"],
        "Роснефть у Республиканской больницы есть 95? Какие колонки?": ["95"],
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

    # Очередь в машинах, а не в минутах — отдельный формат, не "мин".
    result = extract("Татнефть есть 95й\nВозле силикатного кольца\nОчередь 6 машин")
    assert result.reports == [ReportItem("95", "available")]
    assert result.queue_note == "~6 машин"

    result = extract("Лесной 79 Роснефть - 92, 95, дт. Очередь - 3-4 машины на Лесном перед заездом.")
    grades_status = {r.grade: r.status for r in result.reports}
    assert grades_status == {"92": "available", "95": "available", "ДТ": "available"}
    assert result.queue_note == "~3-4 машин"


def test_generic_availability_without_explicit_grade_is_a_known_gap():
    # Документируем текущее (неполное) поведение как маркер регрессии: если
    # это когда-нибудь починим на rule-based стороне, тест подскажет об этом.
    # LLM-путь (llm/client.py) такие сообщения уже разбирает правильно.
    for text in GENERIC_AVAILABILITY_NOT_CAPTURED_BY_RULES:
        assert extract(text).message_type == "irrelevant", text


def test_resolve_station_handles_typos_and_landmark_aliases():
    # Опечатка "лоссосинское" (реальная, из лога) всё равно резолвится.
    assert resolve_station("На РН лоссосинское около РБ 95 есть") == "rosneft_lososinskoe"
    # "РБ" как самостоятельный ориентир рядом с этой же станцией.
    assert resolve_station("Добрый вечер. У РБ на РН 95 на каких колонках есть?") == "rosneft_lososinskoe"
    assert resolve_station("Лукойл на лососинском, 95 есть") == "lukoil_lososinskoe"
    assert resolve_station("РН на лыжной-95 на 1 и 2 колонке") == "rosneft_lyzhnaya"
    # "Республиканская больница" полностью, не только сокращение "РБ".
    assert resolve_station("Роснефть у Республиканской больницы есть 95?") == "rosneft_lososinskoe"


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
    # Подтверждено: "объездная" — локальное название той же АЗС, что и "Лесной 79".
    assert resolve_station("РН на объездной, дт есть") == "rosneft_lesnoy_79"
    # "суоярвск" с 2026-08-10 — алиас СРАЗУ у двух станций разных брендов
    # (rosneft_lesnoy_79 и tatneft_silikatny, подтверждено пользователем) —
    # голое "на суоярвском" без бренда теперь реально неоднозначно, а с
    # явным брендом резолвится однозначно в свою станцию.
    assert resolve_station("Заправился на суоярвском, 92 есть") is None
    assert resolve_station("РН на суоярвском, 92 есть") == "rosneft_lesnoy_79"
    assert resolve_station("Татнефть на суоярвском, 92 есть") == "tatneft_silikatny"
    # rosneft_lesnoy_40 — отдельная станция на той же улице, резолвится
    # только по номеру дома (40/38) БЕЗ слова "лесной" рядом — само слово
    # "лесной"/"лесном" всегда независимо матчит и rosneft_lesnoy_79 тоже
    # (у неё алиас "лесн" не убирали), так что фразы вида "на лесном 40"
    # уходят в None (см. следующий тест), а не в новую станцию. Известное
    # ограничение резолвера (алиас matches "где угодно в тексте", не по
    # близости к номеру), не баг конкретно этой записи.
    assert resolve_station("РН 40, есть 95?") == "rosneft_lesnoy_40"
    assert resolve_station("Роснефть 38, есть дт?") == "rosneft_lesnoy_40"
    assert resolve_station("На лесном 40 рн перерыв?") is None


def test_resolve_station_handles_lukoil_outside_petrozavodsk():
    # Кондопога/Янишполе — одна и та же АЗС Лукойл вне города, подтверждено
    # пользователем 2026-08-10.
    assert resolve_station("Лукойл в Кондопоге, 95 есть") == "lukoil_yanishpole"
    assert resolve_station("В Янишполе на Лукойле 92 есть") == "lukoil_yanishpole"


def test_resolve_station_gazprom_defaults_to_petrozavodsk():
    # Газпром с 2026-08-10 — две точки (Петрозаводск и Березовка вне города).
    # Голое упоминание бренда без адреса ни одной точки резолвится в
    # станцию "по умолчанию" (Петрозаводск), а не в null, как было бы у
    # обычного многоточечного бренда — подтверждено пользователем.
    assert resolve_station("Газпром есть 95?") == "gazprom"
    assert resolve_station("на газпроме 92 есть") == "gazprom"
    assert resolve_station("Газпром Березовка, есть 95?") == "gazprom_berezovka"


def test_resolve_station_distinguishes_different_brands_on_same_road():
    assert resolve_station("Татнефть на Шуйском шоссе, 95 есть") == "tatneft_shuyskoe"
    assert resolve_station("Лукойл на Шуйском шоссе, 95 есть") == "lukoil_shuyskoe"
    assert resolve_station("Опти Шуйское, 95 есть") == "opti_shuyskoe"
    # ТН на Шуйском шоссе — это другая точка, не Татнефть Ключевая.
    assert resolve_station("На ТН на Шуйском шоссе есть 95?") == "tatneft_shuyskoe"
    # "объездная" — алиас tatneft_silikatny (она же tatneft_obiezdnaya,
    # объединены 2026-08-10), уже занят rosneft_lesnoy_79 и lukoil_lesnoy
    # (другой бренд — не конфликт).
    assert resolve_station("Татнефть на объездной, есть 92-95?") == "tatneft_silikatny"
    # "заводская" — алиас lukoil_shuyskoe (она же lukoil_zavodskaya,
    # объединены 2026-08-10).
    assert resolve_station("Подскажите пожалуйста Лукойл на Заводской есть 92?") == "lukoil_shuyskoe"


def test_opti_has_two_stations_bare_brand_no_longer_resolves():
    # С добавлением второй точки "опти" без локации однозначно не резолвится.
    assert resolve_station("Опти Шуйское, 95 есть") == "opti_shuyskoe"
    assert resolve_station("Опти Казарменская  92 ДТ ни кого.") == "opti_kazarmenskaya"
    assert resolve_station("опти на суоярвском 92-й без очереди и ограничений") is None


def test_resolve_station_does_not_guess_ambiguous_or_unknown_locations():
    # Голое упоминание бренда с несколькими точками без локации — не угадываем.
    assert resolve_station("На РН везде вроде") is None
    # Пряжа — посёлок, а не конкретная АЗС из газетира (Янишполе с
    # 2026-08-10 больше не такой пример — см. lukoil_yanishpole).
    assert resolve_station("В Пряже появился 95-й бензин") is None


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
    assert outcome.label == "report:rosneft_lesnoy_79"
    row = conn.execute("SELECT station_id, fuel_grade, status, queue_note FROM fuel_report").fetchone()
    assert row == ("rosneft_lesnoy_79", "95", "available", "есть очередь")

    outcome = process_message(
        conn, text="Заправился в Пряже, 92 есть",
        peer_id=2000000001, conversation_message_id=2, author_id=112,
    )
    assert outcome.label == "unresolved"
    row = conn.execute("SELECT raw_text FROM unresolved_mention").fetchone()
    assert row[0].startswith("Заправился в Пряже")

    # Вопрос по станции, для которой только что записали свежий отчёт —
    # должен вернуться содержательный reply_text, а не просто ярлык.
    outcome = process_message(
        conn, text="РН лесной 95 есть?",
        peer_id=2000000001, conversation_message_id=3, author_id=113,
    )
    assert outcome.label == "question"
    assert "95" in outcome.reply_text
    assert "есть" in outcome.reply_text

    outcome = process_message(
        conn, text="Спасибо",
        peer_id=2000000001, conversation_message_id=4, author_id=111,
    )
    assert outcome.label == "off_topic"


def test_process_message_writes_report_from_pure_quote_when_own_text_empty(monkeypatch):
    # Форвард/реплай без своего комментария — весь факт из цитаты, own_text
    # пуст. Это ровно тот случай, который фича должна закрыть: пишем как
    # обычно, raw_text = обогащённый текст. LLM_ENABLED форсированно выключен
    # (в .env этой машины он включён) — гвард проверяем детерминированно,
    # через rule-based, а не через живой LLM-ответ.
    monkeypatch.setattr(pipeline_module, "LLM_ENABLED", False)
    conn: sqlite3.Connection = get_connection(":memory:")
    outcome = process_message(
        conn, text="РН лесной, 95 есть на 4,5 колонке. Очередь.", own_text="",
        peer_id=2000000001, conversation_message_id=1, author_id=111,
    )
    assert outcome.label == "report:rosneft_lesnoy_79"
    row = conn.execute("SELECT raw_text FROM fuel_report").fetchone()
    assert row[0] == "РН лесной, 95 есть на 4,5 колонке. Очередь."


def test_process_message_suppresses_report_when_own_text_has_no_signal(monkeypatch):
    # Реплай с текстом, который сам по себе ничего не сообщает ("спасибо") —
    # факт всплыл только из подклеенной цитаты. Не пишем: иначе случайный
    # реплай искусственно освежил бы старый факт под новым timestamp.
    monkeypatch.setattr(pipeline_module, "LLM_ENABLED", False)
    conn: sqlite3.Connection = get_connection(":memory:")
    outcome = process_message(
        conn,
        text="РН лесной, 95 есть на 4,5 колонке. Очередь.\nСпасибо",
        own_text="Спасибо",
        peer_id=2000000001, conversation_message_id=1, author_id=111,
    )
    assert outcome.label == "report_suppressed_quote_only"
    assert conn.execute("SELECT COUNT(*) FROM fuel_report").fetchone()[0] == 0
    assert conn.execute("SELECT COUNT(*) FROM unresolved_mention").fetchone()[0] == 0


def test_process_message_writes_report_when_own_text_has_its_own_signal(monkeypatch):
    # Свой текст сам по себе уже report (называет марку) — пишем как обычно,
    # даже если он пришёл вместе с цитатой. Цитата намеренно без брендов и
    # марок (просто заполнитель), чтобы не создавать случайную неоднозначность
    # со станцией из own_text.
    monkeypatch.setattr(pipeline_module, "LLM_ENABLED", False)
    conn: sqlite3.Connection = get_connection(":memory:")
    outcome = process_message(
        conn,
        text="кто-то писал в чат\nРН лесной, 95 есть на 4,5 колонке. Очередь.",
        own_text="РН лесной, 95 есть на 4,5 колонке. Очередь.",
        peer_id=2000000001, conversation_message_id=1, author_id=111,
    )
    assert outcome.label == "report:rosneft_lesnoy_79"
    row = conn.execute("SELECT fuel_grade, status, queue_note FROM fuel_report").fetchone()
    assert row == ("95", "available", "есть очередь")


def test_process_message_question_resolves_station_from_quoted_report(monkeypatch):
    # Реплай-вопрос без станции в своём тексте — станция и марка резолвятся
    # из подклеенной цитаты; вопрос никогда не пишется в БД, гвард тут ни
    # при чём (сработал бы только для message_type == "report").
    monkeypatch.setattr(pipeline_module, "LLM_ENABLED", False)
    conn: sqlite3.Connection = get_connection(":memory:")
    _seed = process_message(
        conn, text="95 нет на Лукойле Вилга", peer_id=2000000001, conversation_message_id=1, author_id=111,
    )
    assert _seed.label.startswith("report:")

    outcome = process_message(
        conn,
        text="95 нет на Лукойле Вилга\nа очередь есть?",
        own_text="а очередь есть?",
        peer_id=2000000001, conversation_message_id=2, author_id=222,
    )
    assert outcome.label == "question"
    assert outcome.reply_text is not None
    assert "95" in outcome.reply_text
