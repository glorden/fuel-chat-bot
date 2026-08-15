import re
from collections import Counter
from pathlib import Path

import yaml

_GAZETTEER_PATH = Path(__file__).resolve().parent.parent / "gazetteer.yaml"


def _load_gazetteer() -> dict:
    return yaml.safe_load(_GAZETTEER_PATH.read_text(encoding="utf-8"))


def _compile(stems: list[str]) -> re.Pattern | None:
    if not stems:
        return None
    alternation = "|".join(re.escape(s) for s in stems)
    return re.compile(rf"(?i)\b(?:{alternation})\w*\b")


_GAZETTEER = _load_gazetteer()
_STATIONS = _GAZETTEER["stations"]
for _station in _STATIONS:
    _station["_brand_re"] = _compile(_station["brand_aliases"])
    _station["_location_re"] = _compile(_station["location_aliases"])
_BRAND_COUNTS = Counter(s["brand"] for s in _STATIONS)
_STATIONS_BY_ID = {s["id"]: s for s in _STATIONS}
_DEFAULT_STATION_BY_BRAND = {
    s["brand"]: s["id"] for s in _STATIONS if s.get("default_for_brand")
}
_BRAND_FUEL_LIMITS: dict[str, int] = _GAZETTEER.get("brand_fuel_limits", {})


def get_station_name(station_id: str) -> str:
    return _STATIONS_BY_ID[station_id]["name"]


def is_known_station(station_id: str) -> bool:
    return station_id in _STATIONS_BY_ID


def get_brand_fuel_limit(brand: str) -> int | None:
    """Статичный лимит отпуска (литры) на весь бренд, из gazetteer.yaml
    (brand_fuel_limits) — None, если для бренда лимит не известен."""
    return _BRAND_FUEL_LIMITS.get(brand)


def resolve_brand(text: str) -> str | None:
    """Бренд, если в тексте однозначно упомянут ровно один — даже когда
    resolve_station() не смог резолвить конкретную станцию (несколько точек
    бренда, локация не названа). Нужно для вопросов про лимит: он общий на
    весь бренд (см. get_brand_fuel_limit), резолвить конкретную точку для
    ответа не требуется."""
    matched = {s["brand"] for s in _STATIONS if s["_brand_re"] and s["_brand_re"].search(text)}
    return next(iter(matched)) if len(matched) == 1 else None


def resolve_station(text: str) -> str | None:
    """Матчит станцию по бренду; если у бренда несколько точек — ещё и по локации.

    Голое упоминание бренда с несколькими точками (например "РН" без адреса,
    когда у Роснефти их четыре) намеренно не резолвится ни в одну из них —
    кроме брендов с назначенной default_for_brand станцией (сейчас только
    Газпром): если ни одна точка бренда не подошла по локации, а сам бренд
    упомянут и однозначен (не смешан с другим брендом в том же сообщении),
    возвращаем станцию по умолчанию вместо null.

    Если бренд вообще не упомянут (например "заправился на суоярвском" без
    слова "роснефть"/"рн"), пробуем резолвить по локации одной — если её
    алиас уникально указывает на одну-единственную станцию среди всех.
    """
    qualified = []
    matched_brands = set()
    for station in _STATIONS:
        if not station["_brand_re"] or not station["_brand_re"].search(text):
            continue
        matched_brands.add(station["brand"])
        if _BRAND_COUNTS[station["brand"]] == 1 or (
            station["_location_re"] and station["_location_re"].search(text)
        ):
            qualified.append(station["id"])

    if matched_brands:
        if len(qualified) == 1:
            return qualified[0]
        if not qualified and len(matched_brands) == 1:
            return _DEFAULT_STATION_BY_BRAND.get(next(iter(matched_brands)))
        return None

    location_only = [s["id"] for s in _STATIONS if s["_location_re"] and s["_location_re"].search(text)]
    return location_only[0] if len(location_only) == 1 else None
