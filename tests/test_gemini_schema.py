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
