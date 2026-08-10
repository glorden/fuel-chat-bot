from llm.client import _parse_arguments
from pipeline.extract import ReportItem


def test_parse_valid_report():
    raw = {
        "message_type": "report",
        "station_id": "rosneft_lesnoy_79",
        "reports": [{"grade": "95", "status": "available"}],
        "question_grades": [],
        "queue_note": "есть очередь",
    }
    result = _parse_arguments(raw)
    assert result is not None
    assert result.station_id == "rosneft_lesnoy_79"
    assert result.extract_result.message_type == "report"
    assert result.extract_result.reports == [ReportItem("95", "available")]
    assert result.extract_result.queue_note == "есть очередь"


def test_parse_valid_question_with_null_station():
    raw = {
        "message_type": "question",
        "station_id": None,
        "reports": [],
        "question_grades": ["92"],
        "queue_note": None,
    }
    result = _parse_arguments(raw)
    assert result is not None
    assert result.station_id is None
    assert result.extract_result.question_grades == ["92"]


def test_parse_rejects_unknown_message_type():
    raw = {"message_type": "spam", "station_id": None, "reports": [], "question_grades": [], "queue_note": None}
    assert _parse_arguments(raw) is None


def test_parse_drops_invalid_grade_and_status_but_keeps_valid_ones():
    # Модель может "нафантазировать" марку/статус, которых нет в схеме —
    # такие записи молча отбрасываются, а не ломают весь разбор.
    raw = {
        "message_type": "report",
        "station_id": "gazprom",
        "reports": [
            {"grade": "95", "status": "available"},
            {"grade": "80", "status": "available"},  # несуществующая марка
            {"grade": "92", "status": "maybe"},  # несуществующий статус
        ],
        "question_grades": [],
        "queue_note": None,
    }
    result = _parse_arguments(raw)
    assert result is not None
    assert result.extract_result.reports == [ReportItem("95", "available")]


def test_parse_treats_empty_or_wrong_typed_station_id_as_none():
    for bad_station_id in ("", 123, [], {}):
        raw = {
            "message_type": "irrelevant",
            "station_id": bad_station_id,
            "reports": [],
            "question_grades": [],
            "queue_note": None,
        }
        result = _parse_arguments(raw)
        assert result is not None
        assert result.station_id is None


def test_parse_rejects_missing_message_type():
    assert _parse_arguments({}) is None


def test_parse_treats_unknown_station_id_as_none():
    # Модель может "нафантазировать" station_id, которого нет в газетире —
    # такой id не должен долетать дальше как реальный (KeyError в
    # get_station_name / мусор в fuel_report), а должен трактоваться как
    # нераспознанная станция, ровно как rule-based резолвер.
    raw = {
        "message_type": "question",
        "station_id": "lukoil_nesuschestvuyuschaya",
        "reports": [],
        "question_grades": ["92"],
        "queue_note": None,
    }
    result = _parse_arguments(raw)
    assert result is not None
    assert result.station_id is None
