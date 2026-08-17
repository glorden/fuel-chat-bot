import re
from dataclasses import dataclass, field
from datetime import datetime, timezone

from pipeline.facts import MOSCOW_TZ, QueueInfo

# 98 и 100 сюда больше не входят — см. TRACKED_GRADES. Из префильтра их при
# этом НЕ убирали: сообщение «98 нет, зато 95 есть» должно дойти до разбора
# целиком, иначе вместе с ненужной маркой потеряется нужная.
_GRADE_TOKEN = re.compile(
    r"(?i)\b(92|95)(?:-?(?:й|го|м|ый))?\b|\b(дт|дизел\w*|солярк\w*)\b"
)
_NEGATION_WORDS = ("нет", "закончил", "кончил", "пуст", "не осталось")

_QUESTION_MARKERS = re.compile(
    r"(?i)\bгде\b|\bкогда\b|\bподскаж\w*|\bкто\s+знает\b|\bкто\s+в\s+курсе\b|\bесть\s+ли\b|\?"
)

_NO_QUEUE = re.compile(r"(?i)очеред\w*\s+нет|нет\s+очеред\w*|без\s+очеред\w*")
_QUEUE_MINUTES = re.compile(
    r"(?i)(?:заправ\w*|отсто\w*|прожда\w*|ожида\w*|очеред\w*)\D{0,15}?(\d{1,3})\s*мин"
)
# Две группы, а не одна: нижняя и (необязательная) верхняя граница
# диапазона — "3-4 машины" должно доехать до ответа как "~3-4 машин", а
# структурированное хранение (см. QueueInfo) требует чисел, не строки.
_QUEUE_CARS = re.compile(r"(?i)(\d{1,3})(?:\s*[-–]\s*(\d{1,3}))?\s*машин\w*")
_QUEUE_PRESENT = re.compile(r"(?i)\bочеред\w*")

_BREAK_KIND_WORDS = {
    "слив": re.compile(r"(?i)\bслив\w*"),
    "отстой": re.compile(r"(?i)\bотсто\w*"),
    "перерыв": re.compile(r"(?i)\bтех\w*\s+перерыв\w*|\bперерыв\w*"),
}
_UNTIL_CLOCK_RE = re.compile(r"(?i)\bдо\s+(\d{1,2})[:.](\d{2})\b")
_UNTIL_HOUR_RE = re.compile(r"(?i)\bдо\s+(\d{1,2})\s*(?:ч\b|час\w*)")
# "ч"/"час" на конце обязателен (не "?") — иначе диапазон марок топлива вида
# "92-95" (без единиц времени) ложно матчился бы как диапазон времени.
_RANGE_RE = re.compile(r"(?i)\b(\d{1,2})(?::(\d{2}))?\s*[-–]\s*(\d{1,2})(?::(\d{2}))?\s*(?:ч\b|час\w*)")
_DURATION_HINT_RE = re.compile(r"(?i)(?:час|мин)\w*\s*(?:на\s+)?\d+|\d+\s*(?:час|мин)\w*")

_LIMIT_KEYWORDS = re.compile(r"(?i)\bлимит\w*|\bограничен\w*|\bв\s+одни\s+руки\b|\bзаливают\b|\bотпускают\b")
# "Полный бак"/"до полного" — реальная находка 2026-08-15 ("На стк полный
# бак дают + в канистру"): в этом чате означает "лимита нет", а не просто
# личный рассказ о своей заправке. Требуем рядом слово-разрешение (дают/
# наливают/заливают/можно/разрешают) в любом порядке — без него бесхозное
# "залил полный бак, спасибо" ошибочно писало бы факт про политику станции.
_UNLIMITED_RE = re.compile(
    r"(?i)без\s+(?:огранич\w*|лимит\w*)|(?:огранич\w*|лимит\w*)\s+нет|не\s+огранич\w*"
    r"|(?:дают|наливают|заливают|можно|разрешают)\D{0,15}(?:полн\w*\s+бак\w*|до\s+полн\w*)"
    r"|(?:полн\w*\s+бак\w*|до\s+полн\w*)\D{0,15}(?:дают|наливают|заливают|можно|разрешают)"
)
_LITERS_RE = re.compile(r"(?i)(\d{1,3})\s*л(?:итр\w*)?\b")


def _normalize_grade(raw: str) -> str:
    raw_low = raw.lower()
    if raw_low.startswith(("дт", "дизел", "соляр")):
        return "ДТ"
    digits = re.match(r"\d+", raw_low)
    return digits.group(0) if digits else raw_low


def resolve_clock_time_to_iso(hour: int, minute: int, *, now: datetime) -> str | None:
    """HH:MM по МСК -> ISO UTC для того же календарного дня, что и `now`.
    None, если hour/minute вне допустимого диапазона (защита от ложного
    совпадения регекса или невалидного значения от LLM). Переносы через
    полночь не наблюдались в реальных сообщениях этого чата — не
    поддержаны сознательно, не по недосмотру."""
    if not (0 <= hour <= 23 and 0 <= minute <= 59):
        return None
    local_now = now.astimezone(MOSCOW_TZ)
    candidate = local_now.replace(hour=hour, minute=minute, second=0, microsecond=0)
    return candidate.astimezone(timezone.utc).isoformat()


@dataclass
class ReportItem:
    grade: str
    status: str  # "available" | "unavailable"


@dataclass
class BreakInfo:
    kind: str | None  # "слив" | "отстой" | "перерыв" | None
    until: str | None  # ISO UTC строка, или None
    duration_note: str | None


@dataclass
class LimitInfo:
    status: str  # "limited" | "unlimited"
    liters: int | None  # только при status == "limited" и явно названном числе


@dataclass
class ExtractResult:
    message_type: str  # "report" | "question" | "irrelevant"
    reports: list[ReportItem] = field(default_factory=list)
    question_grades: list[str] = field(default_factory=list)
    queue: QueueInfo | None = None
    break_info: BreakInfo | None = None
    limit_info: LimitInfo | None = None


def _extract_queue_info(text: str) -> QueueInfo | None:
    if _NO_QUEUE.search(text):
        return QueueInfo(status="none")
    if m := _QUEUE_MINUTES.search(text):
        return QueueInfo(status="present", minutes=int(m.group(1)))
    if m := _QUEUE_CARS.search(text):
        cars_to = int(m.group(2)) if m.group(2) else None
        return QueueInfo(status="present", cars_from=int(m.group(1)), cars_to=cars_to)
    if _QUEUE_PRESENT.search(text):
        return QueueInfo(status="present")
    return None


def _extract_grades_with_status(text: str) -> list[ReportItem]:
    items: list[ReportItem] = []
    for clause in re.split(r"[.\n!]+", text):
        clause = clause.strip()
        if not clause:
            continue
        for match in _GRADE_TOKEN.finditer(clause):
            after = clause[match.end() : match.end() + 15].lower()
            if re.match(r"\s*л(?:итр\w*)?\b", after):
                # "92 л"/"95 литров" — объём в литрах (сколько залили или
                # какой лимит), не марка топлива. Правило появилось из-за
                # АИ-100, которую убрали из TRACKED_GRADES, но осталось
                # нужным: "заправил 95 л" не должно стать фактом про 95-й.
                continue
            grade = _normalize_grade(match.group(0))
            before = clause[: match.start()].lower()
            negated = any(neg in before[-20:] for neg in _NEGATION_WORDS)
            items.append(ReportItem(grade=grade, status="unavailable" if negated else "available"))
    return items


def _extract_break_until(text: str) -> str | None:
    now = datetime.now(timezone.utc)
    if (m := _UNTIL_CLOCK_RE.search(text)) and (
        until := resolve_clock_time_to_iso(int(m.group(1)), int(m.group(2)), now=now)
    ):
        return until
    if (m := _UNTIL_HOUR_RE.search(text)) and (
        until := resolve_clock_time_to_iso(int(m.group(1)), 0, now=now)
    ):
        return until
    if m := _RANGE_RE.search(text):
        end_hour = int(m.group(3))
        end_minute = int(m.group(4)) if m.group(4) else 0
        if until := resolve_clock_time_to_iso(end_hour, end_minute, now=now):
            return until
    return None


def _extract_break_info(text: str) -> BreakInfo | None:
    kind = next((name for name, pattern in _BREAK_KIND_WORDS.items() if pattern.search(text)), None)
    if kind is None:
        return None
    until = _extract_break_until(text)
    duration_note = None
    if until is None and (m := _DURATION_HINT_RE.search(text)):
        duration_note = m.group(0).strip()
    return BreakInfo(kind=kind, until=until, duration_note=duration_note)


def _extract_limit_info(text: str) -> LimitInfo | None:
    if _UNLIMITED_RE.search(text):
        return LimitInfo(status="unlimited", liters=None)
    if not _LIMIT_KEYWORDS.search(text):
        return None
    if m := _LITERS_RE.search(text):
        return LimitInfo(status="limited", liters=int(m.group(1)))
    # Ключевое слово про лимит есть, но чёткого числа нет — не угадываем.
    return None


def extract(text: str) -> ExtractResult:
    if _QUESTION_MARKERS.search(text):
        grades = sorted({_normalize_grade(m.group(0)) for m in _GRADE_TOKEN.finditer(text)})
        return ExtractResult(message_type="question", question_grades=grades)

    break_info = _extract_break_info(text)
    limit_info = _extract_limit_info(text)
    reports = _extract_grades_with_status(text)
    if reports or break_info is not None or limit_info is not None:
        return ExtractResult(
            message_type="report",
            reports=reports,
            queue=_extract_queue_info(text),
            break_info=break_info,
            limit_info=limit_info,
        )

    return ExtractResult(message_type="irrelevant")
