import re
from dataclasses import dataclass, field

_GRADE_TOKEN = re.compile(
    r"(?i)\b(92|95|98|100)(?:-?(?:й|го|м|ый))?\b|\b(дт|дизел\w*|солярк\w*)\b"
)
_NEGATION_WORDS = ("нет", "закончил", "кончил", "пуст", "не осталось")

_QUESTION_MARKERS = re.compile(
    r"(?i)\bгде\b|\bкогда\b|\bподскаж\w*|\bкто\s+знает\b|\bкто\s+в\s+курсе\b|\bесть\s+ли\b|\?"
)

_NO_QUEUE = re.compile(r"(?i)очеред\w*\s+нет|нет\s+очеред\w*|без\s+очеред\w*")
_QUEUE_MINUTES = re.compile(
    r"(?i)(?:заправ\w*|отсто\w*|прожда\w*|ожида\w*|очеред\w*)\D{0,15}?(\d{1,3})\s*мин"
)
_QUEUE_PRESENT = re.compile(r"(?i)\bочеред\w*")


def _normalize_grade(raw: str) -> str:
    raw_low = raw.lower()
    if raw_low.startswith(("дт", "дизел", "соляр")):
        return "ДТ"
    digits = re.match(r"\d+", raw_low)
    return digits.group(0) if digits else raw_low


@dataclass
class ReportItem:
    grade: str
    status: str  # "available" | "unavailable"


@dataclass
class ExtractResult:
    message_type: str  # "report" | "question" | "irrelevant"
    reports: list[ReportItem] = field(default_factory=list)
    question_grades: list[str] = field(default_factory=list)
    queue_note: str | None = None


def _extract_queue_note(text: str) -> str | None:
    if _NO_QUEUE.search(text):
        return "без очереди"
    m = _QUEUE_MINUTES.search(text)
    if m:
        return f"~{m.group(1)} мин"
    if _QUEUE_PRESENT.search(text):
        return "есть очередь"
    return None


def _extract_grades_with_status(text: str) -> list[ReportItem]:
    items: list[ReportItem] = []
    for clause in re.split(r"[.\n!]+", text):
        clause = clause.strip()
        if not clause:
            continue
        for match in _GRADE_TOKEN.finditer(clause):
            grade = _normalize_grade(match.group(0))
            before = clause[: match.start()].lower()
            negated = any(neg in before[-20:] for neg in _NEGATION_WORDS)
            items.append(ReportItem(grade=grade, status="unavailable" if negated else "available"))
    return items


def extract(text: str) -> ExtractResult:
    if _QUESTION_MARKERS.search(text):
        grades = sorted({_normalize_grade(m.group(0)) for m in _GRADE_TOKEN.finditer(text)})
        return ExtractResult(message_type="question", question_grades=grades)

    reports = _extract_grades_with_status(text)
    if reports:
        return ExtractResult(message_type="report", reports=reports, queue_note=_extract_queue_note(text))

    return ExtractResult(message_type="irrelevant")
