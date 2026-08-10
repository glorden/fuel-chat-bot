import re
from collections import Counter
from pathlib import Path

import yaml

_GAZETTEER_PATH = Path(__file__).resolve().parent.parent / "gazetteer.yaml"


def _load_stations() -> list[dict]:
    data = yaml.safe_load(_GAZETTEER_PATH.read_text(encoding="utf-8"))
    return data["stations"]


def _compile(stems: list[str]) -> re.Pattern | None:
    if not stems:
        return None
    alternation = "|".join(re.escape(s) for s in stems)
    return re.compile(rf"(?i)\b(?:{alternation})\w*\b")


_STATIONS = _load_stations()
for _station in _STATIONS:
    _station["_brand_re"] = _compile(_station["brand_aliases"])
    _station["_location_re"] = _compile(_station["location_aliases"])
_BRAND_COUNTS = Counter(s["brand"] for s in _STATIONS)
_STATIONS_BY_ID = {s["id"]: s for s in _STATIONS}


def get_station_name(station_id: str) -> str:
    return _STATIONS_BY_ID[station_id]["name"]


def is_known_station(station_id: str) -> bool:
    return station_id in _STATIONS_BY_ID


def resolve_station(text: str) -> str | None:
    """Матчит станцию по бренду; если у бренда несколько точек — ещё и по локации.

    Голое упоминание бренда с несколькими точками (например "РН" без адреса,
    когда у Роснефти их четыре) намеренно не резолвится ни в одну из них.

    Если бренд вообще не упомянут (например "заправился на суоярвском" без
    слова "роснефть"/"рн"), пробуем резолвить по локации одной — если её
    алиас уникально указывает на одну-единственную станцию среди всех.
    """
    qualified = []
    brand_mentioned = False
    for station in _STATIONS:
        if not station["_brand_re"] or not station["_brand_re"].search(text):
            continue
        brand_mentioned = True
        if _BRAND_COUNTS[station["brand"]] == 1 or (
            station["_location_re"] and station["_location_re"].search(text)
        ):
            qualified.append(station["id"])

    if brand_mentioned:
        return qualified[0] if len(qualified) == 1 else None

    location_only = [s["id"] for s in _STATIONS if s["_location_re"] and s["_location_re"].search(text)]
    return location_only[0] if len(location_only) == 1 else None
