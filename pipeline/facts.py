from dataclasses import dataclass
from datetime import datetime, timedelta, timezone

MOSCOW_TZ = timezone(timedelta(hours=3))

# Марки, которые бот вообще замечает. 98 и 100 убраны с Этапа 42 по прямому
# решению владельца: в городе их нет больше месяца, и когда появятся —
# неизвестно. Это не косметика. Общая фраза («есть бенз», «есть всё»)
# разворачивалась моделью на все отслеживаемые марки — приём, записанный в
# Этапах 2 и 4 как главный довод в пользу LLM, — и на живых данных он
# насочинял 19 записей «98 доступен» из сообщений, где 98 никто не называл
# (включая «Газпром РБ есть 95, G95?»). Сузив список, мы убираем сам
# механизм, а не подчищаем последствия.
TRACKED_GRADES = ("92", "95", "ДТ")

# Марки, о которых бот говорит в ответе. ДТ отслеживаем, но не отвечаем:
# чат про бензин, дизель есть почти везде и про него подскажут люди
# (решение владельца, Этап 42). Факты по ДТ при этом копятся — выбрасывать
# уже собранные данные пришлось бы отдельным решением.
ANSWERED_GRADES = ("92", "95")


@dataclass(frozen=True)
class QueueInfo:
    """Очередь на станции в структурированном виде. Живёт здесь, а не в
    pipeline/extract.py рядом с LimitInfo/BreakInfo: одна и та же форма
    нужна и на записи, и на чтении, а extract.py уже импортирует facts.py
    (обратный импорт дал бы цикл).

    Свободного текста тут нет сознательно — это и есть закрытие F1 (см.
    ARCH_DECISIONS.md, Р2): текст ответа целиком собирает templates.py из
    фиксированного словаря, модель может повлиять только на статус и числа.
    frozen — чтобы объект был хешируемым: templates.py группирует марки по
    ключу, куда входит и очередь."""

    status: str  # "none" (очереди нет) | "present" (есть)
    minutes: int | None = None
    cars_from: int | None = None
    cars_to: int | None = None  # верхняя граница диапазона ("3-4 машины")


@dataclass
class StationFact:
    grade: str
    status: str  # "available" | "unavailable"
    queue: QueueInfo | None
    reported_at: datetime
    age_minutes: float
    tier: str  # "fresh" | "stale" | "very_stale"
    conflicting: bool


@dataclass
class StationBreak:
    kind: str | None  # "слив" | "отстой" | "перерыв" | None
    until: datetime | None
    duration_note: str | None
    reported_minutes_ago: float


@dataclass
class StationLimit:
    status: str  # "limited" | "unlimited"
    liters: int | None
    reported_minutes_ago: float
