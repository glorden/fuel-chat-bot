import json
import logging
from dataclasses import dataclass

from groq import Groq

from config import GROQ_API_KEY, LLM_MODEL, LLM_TIMEOUT_SECONDS
from llm.prompts import build_system_prompt
from pipeline.extract import ExtractResult, ReportItem

log = logging.getLogger("vk_bot")

_VALID_GRADES = {"92", "95", "98", "100", "ДТ"}
_MESSAGE_TYPES = {"report", "question", "irrelevant"}

_TOOL_NAME = "record_analysis"
_TOOL_SCHEMA = {
    "type": "function",
    "function": {
        "name": _TOOL_NAME,
        "description": "Записать разбор сообщения из чата про заправки топливом.",
        "parameters": {
            "type": "object",
            "properties": {
                "message_type": {
                    "type": "string",
                    "enum": sorted(_MESSAGE_TYPES),
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
                            "grade": {"type": "string", "enum": sorted(_VALID_GRADES)},
                            "status": {"type": "string", "enum": ["available", "unavailable"]},
                        },
                        "required": ["grade", "status"],
                    },
                },
                "question_grades": {
                    "type": "array",
                    "description": "Только для question: марки, о которых спрашивают.",
                    "items": {"type": "string", "enum": sorted(_VALID_GRADES)},
                },
                "queue_note": {
                    "type": ["string", "null"],
                    "description": "Короткая заметка про очередь на русском, или null.",
                },
            },
            "required": ["message_type", "station_id", "reports", "question_grades", "queue_note"],
        },
    },
}

_SYSTEM_PROMPT = build_system_prompt()
_client = Groq(api_key=GROQ_API_KEY) if GROQ_API_KEY else None


@dataclass
class LLMAnalysis:
    extract_result: ExtractResult
    station_id: str | None


def _parse_arguments(raw: dict) -> LLMAnalysis | None:
    message_type = raw.get("message_type")
    if message_type not in _MESSAGE_TYPES:
        return None

    station_id = raw.get("station_id")
    if not isinstance(station_id, str) or not station_id:
        station_id = None

    queue_note = raw.get("queue_note")
    if not isinstance(queue_note, str) or not queue_note:
        queue_note = None

    reports = []
    for item in raw.get("reports") or []:
        if not isinstance(item, dict):
            continue
        grade, status = item.get("grade"), item.get("status")
        if grade in _VALID_GRADES and status in ("available", "unavailable"):
            reports.append(ReportItem(grade=grade, status=status))

    question_grades = [g for g in (raw.get("question_grades") or []) if g in _VALID_GRADES]

    extract_result = ExtractResult(
        message_type=message_type,
        reports=reports,
        question_grades=question_grades,
        queue_note=queue_note,
    )
    return LLMAnalysis(extract_result=extract_result, station_id=station_id)


def analyze(text: str) -> LLMAnalysis | None:
    """Разбор сообщения через Groq. Возвращает None при любом сбое или
    неожиданном ответе — сигнал пайплайну откатиться на rule-based."""
    if _client is None:
        return None

    try:
        response = _client.chat.completions.create(
            model=LLM_MODEL,
            messages=[
                {"role": "system", "content": _SYSTEM_PROMPT},
                {"role": "user", "content": text},
            ],
            tools=[_TOOL_SCHEMA],
            tool_choice={"type": "function", "function": {"name": _TOOL_NAME}},
            timeout=LLM_TIMEOUT_SECONDS,
        )
        tool_calls = response.choices[0].message.tool_calls
        if not tool_calls:
            log.warning("Groq: ответ без tool call, откат на rule-based. text=%r", text)
            return None

        raw = json.loads(tool_calls[0].function.arguments)
    except Exception:
        log.exception("Groq: сбой запроса, откат на rule-based. text=%r", text)
        return None

    parsed = _parse_arguments(raw)
    if parsed is None:
        log.warning("Groq: неожиданная форма ответа, откат на rule-based. raw=%r", raw)
    return parsed
