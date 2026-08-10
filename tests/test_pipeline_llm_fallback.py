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
        lambda text: LLMAnalysis(
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
    monkeypatch.setattr(llm.client, "analyze", lambda text: None)
    conn = get_connection(":memory:")

    outcome = process_message(
        conn, text="РН лесной, 95 есть на 4,5 колонке. Очередь.",
        peer_id=1, conversation_message_id=1, author_id=1,
    )
    assert outcome.label == "report:rosneft_lesnoy_79"


def test_llm_disabled_never_calls_llm_client(monkeypatch):
    monkeypatch.setattr(pipeline_module, "LLM_ENABLED", False)

    def _boom(text):
        raise AssertionError("llm.client.analyze не должен вызываться при LLM_ENABLED=False")

    monkeypatch.setattr(llm.client, "analyze", _boom)
    conn = get_connection(":memory:")

    outcome = process_message(
        conn, text="Татнефть силикатный только дт",
        peer_id=1, conversation_message_id=1, author_id=1,
    )
    assert outcome.label == "report:tatneft_silikatny"
