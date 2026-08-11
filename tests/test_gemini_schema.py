from llm.gemini_client import _to_gemini_schema
from llm.schema import PARAMETERS_SCHEMA


def test_nullable_string_becomes_single_type_plus_flag():
    result = _to_gemini_schema({"type": ["string", "null"], "description": "x"})
    assert result == {"type": "string", "nullable": True, "description": "x"}


def test_non_union_type_passes_through_unchanged():
    result = _to_gemini_schema({"type": "string", "enum": ["a", "b"]})
    assert result == {"type": "string", "enum": ["a", "b"]}


def test_recurses_into_properties_and_items():
    result = _to_gemini_schema(PARAMETERS_SCHEMA)
    assert result["properties"]["station_id"] == {
        "type": "string",
        "nullable": True,
        "description": "id станции из списка известных АЗС, или null, если не уверен.",
    }
    assert result["properties"]["queue_note"]["nullable"] is True
    report_item_schema = result["properties"]["reports"]["items"]
    assert report_item_schema["properties"]["grade"]["type"] == "string"
    assert "nullable" not in report_item_schema["properties"]["grade"]


def test_does_not_mutate_original_schema():
    original_station_id_type = PARAMETERS_SCHEMA["properties"]["station_id"]["type"]
    _to_gemini_schema(PARAMETERS_SCHEMA)
    assert PARAMETERS_SCHEMA["properties"]["station_id"]["type"] == original_station_id_type


def test_recurses_into_nested_nullable_object_break_info():
    # break_info — первый вложенный object-тип в схеме (не просто nullable
    # строка и не items внутри array) — конвертер должен раскрыть nullable
    # и на сам объект, и на каждое его вложенное поле.
    result = _to_gemini_schema(PARAMETERS_SCHEMA)
    break_info_schema = result["properties"]["break_info"]
    assert break_info_schema["type"] == "object"
    assert break_info_schema["nullable"] is True
    assert break_info_schema["properties"]["kind"]["type"] == "string"
    assert break_info_schema["properties"]["kind"]["nullable"] is True
    assert break_info_schema["properties"]["until"]["nullable"] is True
    assert break_info_schema["properties"]["duration_note"]["nullable"] is True


def test_non_nullable_enum_survives_inside_nullable_object():
    # limit_info.status — первый случай НЕ-nullable enum-поля внутри
    # nullable родительского объекта: сам limit_info может быть null, но
    # если он есть — status обязателен (не null). Конвертер не должен
    # ошибочно проставить nullable туда, где его не было в исходной схеме.
    result = _to_gemini_schema(PARAMETERS_SCHEMA)
    limit_info_schema = result["properties"]["limit_info"]
    assert limit_info_schema["type"] == "object"
    assert limit_info_schema["nullable"] is True
    status_schema = limit_info_schema["properties"]["status"]
    assert status_schema["type"] == "string"
    assert "nullable" not in status_schema
    assert status_schema["enum"] == ["limited", "unlimited"]
    assert limit_info_schema["properties"]["liters"]["nullable"] is True
