from dataclasses import dataclass
from datetime import datetime, timedelta, timezone

MOSCOW_TZ = timezone(timedelta(hours=3))


@dataclass
class StationFact:
    grade: str
    status: str  # "available" | "unavailable"
    queue_note: str | None
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
