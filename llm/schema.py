import re
from dataclasses import dataclass
from datetime import datetime, timezone

from pipeline.extract import BreakInfo, ExtractResult, LimitInfo, ReportItem, resolve_clock_time_to_iso
from pipeline.facts import TRACKED_GRADES, QueueInfo
from pipeline.resolve_station import is_known_station

# Один источник правды с rule-based-путём: enum в схеме инструмента — это
# то, что физически не даёт модели вернуть 98/100 (Groq отклоняет вызов на
# своей стороне). Промпт при этом отдельно просит их игнорировать, чтобы
# модель не пыталась и не нарывалась на отказ API.
VALID_GRADES = set(TRACKED_GRADES)
MESSAGE_TYPES = {"report", "question", "irrelevant"}
VALID_BREAK_KINDS = {"слив", "отстой", "перерыв"}
VALID_LIMIT_STATUSES = {"limited", "unlimited"}
VALID_QUEUE_STATUSES = {"none", "present"}
# Верхняя граница для чисел очереди: столько же, сколько допускает regex
# rule-based пути (\d{1,3}). Нужна не ради формата, а чтобы абсурдное
# число от модели не доехало до чата как факт.
MAX_QUEUE_VALUE = 999

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
        "queue": {
            "type": ["object", "null"],
            "description": (
                "Очередь на станции, если она упомянута; иначе null. Только "
                "статус и числа — свободного текста в этом поле нет, "
                "формулировку ответа выбирает бот, не ты."
            ),
            "properties": {
                "status": {
                    "type": "string",
                    "enum": sorted(VALID_QUEUE_STATUSES),
                    "description": "'none' — очереди нет, 'present' — очередь есть.",
                },
                "minutes": {
                    "type": ["integer", "null"],
                    "description": (
                        "Сколько минут ждать, если число названо явно; иначе null."
                    ),
                },
                "cars_from": {
                    "type": ["integer", "null"],
                    "description": (
                        "Сколько машин в очереди, если число названо явно "
                        "(нижняя граница, если назван диапазон); иначе null."
                    ),
                },
                "cars_to": {
                    "type": ["integer", "null"],
                    "description": (
                        "Верхняя граница диапазона машин ('3-4 машины'), "
                        "если назван именно диапазон; иначе null."
                    ),
                },
            },
            "required": ["status", "minutes", "cars_from", "cars_to"],
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
        "limit_info": {
            "type": ["object", "null"],
            "description": (
                "Лимит отпуска топлива в одни руки на станции — только если это ОТЧЁТ "
                "(сообщение утверждает факт), не вопрос. Для вопросов и отчётов без "
                "упоминания лимита — null."
            ),
            "properties": {
                "status": {
                    "type": "string",
                    "enum": sorted(VALID_LIMIT_STATUSES),
                    "description": "'limited' — лимит есть, 'unlimited' — лимита нет/сняли.",
                },
                "liters": {
                    "type": ["integer", "null"],
                    "description": (
                        "Число литров, если status='limited' и число названо явно; "
                        "иначе null (в т.ч. всегда null при status='unlimited')."
                    ),
                },
            },
            "required": ["status", "liters"],
        },
    },
    "required": [
        "message_type", "station_id", "reports", "question_grades", "queue", "break_info", "limit_info",
    ],
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


def _queue_number(raw) -> int | None:
    """Число из ответа модели или None. bool отсекается отдельно: в Python
    True — это int, и без проверки "очередь: 1 машина" получилась бы из
    queue={"cars_from": true}."""
    if not isinstance(raw, int) or isinstance(raw, bool):
        return None
    return raw if 1 <= raw <= MAX_QUEUE_VALUE else None


def _parse_queue_info(raw) -> QueueInfo | None:
    """Единственный путь, которым очередь от модели попадает в БД и потом
    в чат. Строка вместо объекта, неизвестный статус, лишние ключи с
    текстом — всё это даёт None или отбрасывается: свободному тексту тут
    просто некуда попасть (см. ARCH_DECISIONS.md, Р2)."""
    if not isinstance(raw, dict):
        return None

    status = raw.get("status")
    if status not in VALID_QUEUE_STATUSES:
        return None
    if status == "none":
        return QueueInfo(status="none")

    cars_from = _queue_number(raw.get("cars_from"))
    cars_to = _queue_number(raw.get("cars_to")) if cars_from is not None else None
    if cars_to is not None and cars_to <= cars_from:
        cars_to = None
    return QueueInfo(
        status="present",
        minutes=_queue_number(raw.get("minutes")),
        cars_from=cars_from,
        cars_to=cars_to,
    )


def _parse_limit_info(raw) -> LimitInfo | None:
    if not isinstance(raw, dict):
        return None

    status = raw.get("status")
    if status not in VALID_LIMIT_STATUSES:
        return None

    if status == "unlimited":
        return LimitInfo(status="unlimited", liters=None)

    liters = raw.get("liters")
    if not isinstance(liters, int) or isinstance(liters, bool) or liters <= 0:
        liters = None
    return LimitInfo(status="limited", liters=liters)


def _parse_arguments(raw: dict) -> LLMAnalysis | None:
    message_type = raw.get("message_type")
    if message_type not in MESSAGE_TYPES:
        return None

    station_id = raw.get("station_id")
    if not isinstance(station_id, str) or not station_id or not is_known_station(station_id):
        station_id = None

    queue = _parse_queue_info(raw.get("queue"))

    reports = []
    for item in raw.get("reports") or []:
        if not isinstance(item, dict):
            continue
        grade, status = item.get("grade"), item.get("status")
        if grade in VALID_GRADES and status in ("available", "unavailable"):
            reports.append(ReportItem(grade=grade, status=status))

    question_grades = [g for g in (raw.get("question_grades") or []) if g in VALID_GRADES]

    # break_info/limit_info гейтятся через message_type, не отдельным флагом
    # внутри них самих — единственный источник истины "это вопрос или
    # отчёт", без риска противоречия между полями.
    break_info = _parse_break_info(raw.get("break_info")) if message_type == "report" else None
    limit_info = _parse_limit_info(raw.get("limit_info")) if message_type == "report" else None

    extract_result = ExtractResult(
        message_type=message_type,
        reports=reports,
        question_grades=question_grades,
        queue=queue,
        break_info=break_info,
        limit_info=limit_info,
    )
    return LLMAnalysis(extract_result=extract_result, station_id=station_id)
