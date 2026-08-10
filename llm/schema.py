import re
from dataclasses import dataclass
from datetime import datetime, timezone

from pipeline.extract import BreakInfo, ExtractResult, ReportItem, resolve_clock_time_to_iso
from pipeline.resolve_station import is_known_station

VALID_GRADES = {"92", "95", "98", "100", "ДТ"}
MESSAGE_TYPES = {"report", "question", "irrelevant"}
VALID_BREAK_KINDS = {"слив", "отстой", "перерыв"}

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
        "break_info": {
            "type": ["object", "null"],
            "description": (
                "Технический перерыв/слив бензовоза на станции — только если это ОТЧЁТ "
                "(сообщение утверждает факт), не вопрос. Для вопросов и обычных отчётов "
                "без перерыва — null."
            ),
            "properties": {
                "kind": {
                    "type": ["string", "null"],
                    "description": (
                        "'слив' (слив бензовоза), 'отстой' (отстой топлива), "
                        "'перерыв' (другой/неуточнённый технический перерыв), или null."
                    ),
                },
                "until": {
                    "type": ["string", "null"],
                    "description": (
                        "Время окончания перерыва в формате ЧЧ:ММ по местному времени "
                        "(не ISO, без даты), если названо явно; иначе null."
                    ),
                },
                "duration_note": {
                    "type": ["string", "null"],
                    "description": (
                        "Свободный текст об ожидаемой длительности, если точное время "
                        "окончания не названо; иначе null."
                    ),
                },
            },
            "required": ["kind", "until", "duration_note"],
        },
    },
    "required": ["message_type", "station_id", "reports", "question_grades", "queue_note", "break_info"],
}


@dataclass
class LLMAnalysis:
    extract_result: ExtractResult
    station_id: str | None


_CLOCK_TIME_RE = re.compile(r"^([01]?\d|2[0-3]):([0-5]\d)$")


def _parse_break_info(raw) -> BreakInfo | None:
    if not isinstance(raw, dict):
        return None

    kind = raw.get("kind")
    if kind not in VALID_BREAK_KINDS:
        kind = None

    duration_note = raw.get("duration_note")
    if not isinstance(duration_note, str) or not duration_note:
        duration_note = None

    until = None
    until_raw = raw.get("until")
    if isinstance(until_raw, str) and (m := _CLOCK_TIME_RE.match(until_raw.strip())):
        until = resolve_clock_time_to_iso(int(m.group(1)), int(m.group(2)), now=datetime.now(timezone.utc))

    if kind is None and until is None and duration_note is None:
        return None
    return BreakInfo(kind=kind, until=until, duration_note=duration_note)


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

    # break_info гейтится через message_type, не отдельным флагом внутри
    # самого break_info — единственный источник истины "это вопрос или
    # отчёт", без риска противоречия между двумя полями.
    break_info = _parse_break_info(raw.get("break_info")) if message_type == "report" else None

    extract_result = ExtractResult(
        message_type=message_type,
        reports=reports,
        question_grades=question_grades,
        queue_note=queue_note,
        break_info=break_info,
    )
    return LLMAnalysis(extract_result=extract_result, station_id=station_id)
