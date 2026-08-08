from pathlib import Path

import yaml
from rapidfuzz import fuzz

_GAZETTEER_PATH = Path(__file__).resolve().parent.parent / "gazetteer.yaml"
_FUZZY_THRESHOLD = 90


def _load_stations() -> list[dict]:
    data = yaml.safe_load(_GAZETTEER_PATH.read_text(encoding="utf-8"))
    return data["stations"]


_STATIONS = _load_stations()


def resolve_station(text: str) -> str | None:
    text_low = text.lower()

    substring_matches = [
        (len(alias), station["id"])
        for station in _STATIONS
        for alias in station["aliases"]
        if alias in text_low
    ]
    if substring_matches:
        return max(substring_matches)[1]

    best_id, best_score = None, 0
    for station in _STATIONS:
        for alias in station["aliases"]:
            score = fuzz.partial_ratio(alias, text_low)
            if score > best_score:
                best_score, best_id = score, station["id"]
    return best_id if best_score >= _FUZZY_THRESHOLD else None
