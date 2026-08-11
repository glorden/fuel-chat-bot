from datetime import datetime

from llm.client import _parse_arguments
from pipeline.extract import BreakInfo, LimitInfo, ReportItem
from pipeline.facts import MOSCOW_TZ


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


def test_parse_valid_break_info():
    raw = {
        "message_type": "report",
        "station_id": "lukoil_vilga",
        "reports": [],
        "question_grades": [],
        "queue_note": None,
        "break_info": {"kind": "слив", "until": None, "duration_note": "минут 40"},
    }
    result = _parse_arguments(raw)
    assert result is not None
    assert result.extract_result.break_info == BreakInfo(kind="слив", until=None, duration_note="минут 40")


def test_parse_break_info_with_valid_until_converts_to_clock_time():
    raw = {
        "message_type": "report",
        "station_id": "gazprom",
        "reports": [],
        "question_grades": [],
        "queue_note": None,
        "break_info": {"kind": "перерыв", "until": "22:00", "duration_note": None},
    }
    result = _parse_arguments(raw)
    assert result.extract_result.break_info.kind == "перерыв"
    until_dt = datetime.fromisoformat(result.extract_result.break_info.until)
    assert until_dt.astimezone(MOSCOW_TZ).strftime("%H:%M") == "22:00"


def test_parse_break_info_drops_invalid_kind_but_keeps_rest():
    raw = {
        "message_type": "report",
        "station_id": "gazprom",
        "reports": [],
        "question_grades": [],
        "queue_note": None,
        "break_info": {"kind": "апокалипсис", "until": None, "duration_note": "минут 40"},
    }
    result = _parse_arguments(raw)
    assert result.extract_result.break_info.kind is None
    assert result.extract_result.break_info.duration_note == "минут 40"


def test_parse_break_info_drops_malformed_until_without_raising():
    for bad_until in ("25:99", "не время", "22", 1234, [], {}):
        raw = {
            "message_type": "report",
            "station_id": "gazprom",
            "reports": [],
            "question_grades": [],
            "queue_note": None,
            "break_info": {"kind": "перерыв", "until": bad_until, "duration_note": None},
        }
        result = _parse_arguments(raw)
        assert result is not None
        assert result.extract_result.break_info.until is None, bad_until


def test_parse_break_info_collapses_all_null_fields_to_none():
    raw = {
        "message_type": "report",
        "station_id": "gazprom",
        "reports": [{"grade": "95", "status": "available"}],
        "question_grades": [],
        "queue_note": None,
        "break_info": {"kind": None, "until": None, "duration_note": None},
    }
    result = _parse_arguments(raw)
    assert result.extract_result.break_info is None


def test_parse_ignores_break_info_when_message_type_is_not_report():
    # break_info гейтится через message_type — даже если модель ошибочно
    # прислала непустой break_info на вопрос, он не должен долететь дальше.
    raw = {
        "message_type": "question",
        "station_id": "gazprom",
        "reports": [],
        "question_grades": [],
        "queue_note": None,
        "break_info": {"kind": "слив", "until": None, "duration_note": "минут 40"},
    }
    result = _parse_arguments(raw)
    assert result.extract_result.break_info is None


def test_parse_missing_break_info_key_is_treated_as_none():
    # Совместимость с raw-словарями без ключа break_info вообще (как во
    # всех тестах выше, написанных до Этапа 12).
    raw = {
        "message_type": "report",
        "station_id": "gazprom",
        "reports": [{"grade": "95", "status": "available"}],
        "question_grades": [],
        "queue_note": None,
    }
    result = _parse_arguments(raw)
    assert result is not None
    assert result.extract_result.break_info is None


def test_parse_valid_limited_info():
    raw = {
        "message_type": "report",
        "station_id": "gazprom",
        "reports": [],
        "question_grades": [],
        "queue_note": None,
        "limit_info": {"status": "limited", "liters": 30},
    }
    result = _parse_arguments(raw)
    assert result is not None
    assert result.extract_result.limit_info == LimitInfo(status="limited", liters=30)


def test_parse_valid_unlimited_info():
    raw = {
        "message_type": "report",
        "station_id": "gazprom",
        "reports": [],
        "question_grades": [],
        "queue_note": None,
        "limit_info": {"status": "unlimited", "liters": None},
    }
    result = _parse_arguments(raw)
    assert result.extract_result.limit_info == LimitInfo(status="unlimited", liters=None)


def test_parse_unlimited_ignores_stray_liters_value():
    # Модель не должна одновременно говорить "лимита нет" и давать число —
    # liters принудительно null при status=unlimited, независимо от того,
    # что реально прислала модель.
    raw = {
        "message_type": "report",
        "station_id": "gazprom",
        "reports": [],
        "question_grades": [],
        "queue_note": None,
        "limit_info": {"status": "unlimited", "liters": 30},
    }
    result = _parse_arguments(raw)
    assert result.extract_result.limit_info == LimitInfo(status="unlimited", liters=None)


def test_parse_limit_info_drops_invalid_status():
    raw = {
        "message_type": "report",
        "station_id": "gazprom",
        "reports": [],
        "question_grades": [],
        "queue_note": None,
        "limit_info": {"status": "maybe", "liters": 30},
    }
    result = _parse_arguments(raw)
    assert result.extract_result.limit_info is None


def test_parse_limit_info_drops_malformed_liters_without_raising():
    for bad_liters in ("30", -5, 0, True, [], {}, None):
        raw = {
            "message_type": "report",
            "station_id": "gazprom",
            "reports": [],
            "question_grades": [],
            "queue_note": None,
            "limit_info": {"status": "limited", "liters": bad_liters},
        }
        result = _parse_arguments(raw)
        assert result is not None
        assert result.extract_result.limit_info.status == "limited", bad_liters
        assert result.extract_result.limit_info.liters is None, bad_liters


def test_parse_ignores_limit_info_when_message_type_is_not_report():
    raw = {
        "message_type": "question",
        "station_id": "gazprom",
        "reports": [],
        "question_grades": [],
        "queue_note": None,
        "limit_info": {"status": "limited", "liters": 30},
    }
    result = _parse_arguments(raw)
    assert result.extract_result.limit_info is None


def test_parse_missing_limit_info_key_is_treated_as_none():
    raw = {
        "message_type": "report",
        "station_id": "gazprom",
        "reports": [{"grade": "95", "status": "available"}],
        "question_grades": [],
        "queue_note": None,
    }
    result = _parse_arguments(raw)
    assert result is not None
    assert result.extract_result.limit_info is None
