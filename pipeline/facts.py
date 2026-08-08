from dataclasses import dataclass


@dataclass
class StationFact:
    grade: str
    status: str  # "available" | "unavailable"
    queue_note: str | None
    age_minutes: float
    tier: str  # "fresh" | "stale" | "very_stale"
    conflicting: bool
