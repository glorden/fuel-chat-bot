from pipeline.facts import StationFact

_FRESHNESS_NOTE = {
    "fresh": "",
    "stale": " (данные не совсем свежие)",
    "very_stale": " (данные старые, могло измениться)",
}


def render_station_answer(station_name: str, facts: list[StationFact]) -> str:
    if not facts:
        return f"По «{station_name}» пока нет данных в чате — никто не сообщал."

    lines = [f"информация по {station_name}."]
    for fact in sorted(facts, key=lambda f: f.grade):
        lines.append(_render_fact_line(fact))
    return "\n".join(lines)


def _render_fact_line(fact: StationFact) -> str:
    status_word = "есть" if fact.status == "available" else "нет"
    queue = f", {fact.queue_note}" if fact.queue_note else ""
    conflict = " (но по более раннему сообщению было иначе — обстановка нестабильна)" if fact.conflicting else ""
    age = _format_age(fact.age_minutes)
    freshness = _FRESHNESS_NOTE[fact.tier]
    return f"{fact.grade}: {status_word}{queue} (данные {age} назад{freshness}){conflict}"


def _format_age(age_minutes: float) -> str:
    if age_minutes < 1:
        return "меньше минуты"
    if age_minutes < 60:
        return f"{int(age_minutes)} мин"
    hours = age_minutes / 60
    if hours < 24:
        return f"{f'{hours:.1f}'.rstrip('0').rstrip('.')} ч"
    return f"{int(hours / 24)} дн"
