from dataclasses import dataclass

from pipeline.extract import ExtractResult, ReportItem
from pipeline.resolve_station import is_known_station

VALID_GRADES = {"92", "95", "98", "100", "ДТ"}
MESSAGE_TYPES = {"report", "question", "irrelevant"}

TOOL_NAME = "record_analysis"
TOOL_DESCRIPTION = "Записать разбор сообщения из чата про заправки топливом."
PARAMETERS_SCHEMA = {
    "type": "object",
    "properties": {
        "message_type": {
            "type": "string",
            "enum": sorted(MESSAGE_TYPES),
        },
        "station_id": {
            "type": ["string", "null"],
            "description": "id станции из списка известных АЗС, или null, если не уверен.",
        },
        "reports": {
            "type": "array",
            "description": "Только для report: марки топлива и их статус.",
            "items": {
                "type": "object",
                "properties": {
                    "grade": {"type": "string", "enum": sorted(VALID_GRADES)},
                    "status": {"type": "string", "enum": ["available", "unavailable"]},
                },
                "required": ["grade", "status"],
            },
        },
        "question_grades": {
            "type": "array",
            "description": "Только для question: марки, о которых спрашивают.",
            "items": {"type": "string", "enum": sorted(VALID_GRADES)},
        },
        "queue_note": {
            "type": ["string", "null"],
            "description": "Короткая заметка про очередь на русском, или null.",
        },
    },
    "required": ["message_type", "station_id", "reports", "question_grades", "queue_note"],
}


@dataclass
class LLMAnalysis:
    extract_result: ExtractResult
    station_id: str | None


def _parse_arguments(raw: dict) -> LLMAnalysis | None:
    message_type = raw.get("message_type")
    if message_type not in MESSAGE_TYPES:
        return None

    station_id = raw.get("station_id")
    if not isinstance(station_id, str) or not station_id or not is_known_station(station_id):
        station_id = None

    queue_note = raw.get("queue_note")
    if not isinstance(queue_note, str) or not queue_note:
        queue_note = None

    reports = []
    for item in raw.get("reports") or []:
        if not isinstance(item, dict):
            continue
        grade, status = item.get("grade"), item.get("status")
        if grade in VALID_GRADES and status in ("available", "unavailable"):
            reports.append(ReportItem(grade=grade, status=status))

    question_grades = [g for g in (raw.get("question_grades") or []) if g in VALID_GRADES]

    extract_result = ExtractResult(
        message_type=message_type,
        reports=reports,
        question_grades=question_grades,
        queue_note=queue_note,
    )
    return LLMAnalysis(extract_result=extract_result, station_id=station_id)
