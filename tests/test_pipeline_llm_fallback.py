import llm.client
import pipeline.pipeline as pipeline_module
from db.schema import get_connection
from llm.client import LLMAnalysis
from pipeline.extract import ExtractResult, ReportItem
from pipeline.pipeline import process_message


def test_uses_llm_result_when_available(monkeypatch):
    monkeypatch.setattr(pipeline_module, "LLM_ENABLED", True)
    monkeypatch.setattr(
        llm.client,
        "analyze",
        lambda text, previous_message=None, quoted_context=None: LLMAnalysis(
            extract_result=ExtractResult(message_type="report", reports=[ReportItem("95", "available")]),
            station_id="rosneft_lesnoy_79",
        ),
    )
    conn = get_connection(":memory:")

    # Текст проходит pre-filter (слово "азс"), но сам по себе rule-based
    # extract() не нашёл бы в нём ни одной марки топлива — если результат
    # всё равно попал в fuel_report, значит использован именно LLM-результат.
    outcome = process_message(
        conn, text="азс, всё как обычно", peer_id=1, conversation_message_id=1, author_id=1,
    )
    assert outcome.label == "report:rosneft_lesnoy_79"
    row = conn.execute("SELECT station_id, fuel_grade, status FROM fuel_report").fetchone()
    assert row == ("rosneft_lesnoy_79", "95", "available")


def test_falls_back_to_rule_based_when_llm_returns_none(monkeypatch):
    monkeypatch.setattr(pipeline_module, "LLM_ENABLED", True)
    monkeypatch.setattr(llm.client, "analyze", lambda text, previous_message=None, quoted_context=None: None)
    conn = get_connection(":memory:")

    outcome = process_message(
        conn, text="РН лесной, 95 есть на 4,5 колонке. Очередь.",
        peer_id=1, conversation_message_id=1, author_id=1,
    )
    assert outcome.label == "report:rosneft_lesnoy_79"


def test_llm_disabled_never_calls_llm_client(monkeypatch):
    monkeypatch.setattr(pipeline_module, "LLM_ENABLED", False)

    def _boom(text, previous_message=None, quoted_context=None):
        raise AssertionError("llm.client.analyze не должен вызываться при LLM_ENABLED=False")

    monkeypatch.setattr(llm.client, "analyze", _boom)
    conn = get_connection(":memory:")

    outcome = process_message(
        conn, text="Татнефть силикатный только дт",
        peer_id=1, conversation_message_id=1, author_id=1,
    )
    assert outcome.label == "report:tatneft_silikatny"


def test_previous_message_threaded_to_llm_analyze(monkeypatch):
    # rule-based (extract()) не имеет параметра previous_message вообще —
    # эта проверка именно про то, что LLM-ветка его реально получает.
    monkeypatch.setattr(pipeline_module, "LLM_ENABLED", True)
    received = {}

    def _fake_analyze(text, previous_message=None, quoted_context=None):
        received["text"] = text
        received["previous_message"] = previous_message
        received["quoted_context"] = quoted_context
        return LLMAnalysis(
            extract_result=ExtractResult(message_type="question", question_grades=[]),
            station_id="gazprom",
        )

    monkeypatch.setattr(llm.client, "analyze", _fake_analyze)
    conn = get_connection(":memory:")

    process_message(
        conn, text="большая очередь?", previous_message="95 есть на Газпроме?",
        peer_id=1, conversation_message_id=1, author_id=1,
    )
    assert received["text"] == "большая очередь?"
    assert received["previous_message"] == "95 есть на Газпроме?"
    assert received["quoted_context"] is None


def test_quoted_context_threaded_separately_from_combined_text(monkeypatch):
    # Живая находка 2026-08-11: LLM получал уже склеенный quoted+own текст
    # без разметки и иногда путал "?" из цитаты с текущим сообщением. Теперь
    # LLM-ветка должна получать own_text отдельно от quoted_context, а не
    # склеенный text — эта проверка именно про то, что они не смешиваются.
    monkeypatch.setattr(pipeline_module, "LLM_ENABLED", True)
    received = {}

    def _fake_analyze(text, previous_message=None, quoted_context=None):
        received["text"] = text
        received["quoted_context"] = quoted_context
        return LLMAnalysis(
            extract_result=ExtractResult(message_type="report", reports=[ReportItem("95", "available")]),
            station_id="tatneft_shuyskoe",
        )

    monkeypatch.setattr(llm.client, "analyze", _fake_analyze)
    conn = get_connection(":memory:")

    process_message(
        conn,
        text="А Татнефть на Шуйском есть 95?\nДа",
        own_text="Да",
        quoted_context="А Татнефть на Шуйском есть 95?",
        peer_id=1, conversation_message_id=1, author_id=1,
    )
    assert received["text"] == "Да"
    assert received["quoted_context"] == "А Татнефть на Шуйском есть 95?"


def test_rule_based_still_gets_combined_text_when_llm_disabled(monkeypatch):
    # Rule-based не должен ничего терять от этого рефакторинга — station
    # resolution по-прежнему видит цитату+свой текст склеенными, как раньше.
    monkeypatch.setattr(pipeline_module, "LLM_ENABLED", False)
    conn = get_connection(":memory:")

    outcome = process_message(
        conn,
        text="95 нет на Лукойле Вилга\nа очередь есть?",
        own_text="а очередь есть?",
        quoted_context="95 нет на Лукойле Вилга",
        peer_id=1, conversation_message_id=1, author_id=1,
    )
    assert outcome.label == "question"
