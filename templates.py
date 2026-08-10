from pipeline.facts import MOSCOW_TZ, StationBreak, StationFact

_FRESHNESS_NOTE = {
    "fresh": "",
    "stale": " (устарело)",
    "very_stale": " (данные старые)",
}

_BREAK_KIND_TEXT = {
    "слив": "слив бензовоза",
    "отстой": "отстой топлива",
    "перерыв": "технический перерыв",
}


def render_station_answer(
    station_name: str, facts: list[StationFact], break_info: StationBreak | None = None
) -> str:
    if not facts and break_info is None:
        return f"По «{station_name}» пока нет данных в чате — никто не сообщал."

    uniform_queue = _uniform_queue_note(facts)
    lines = [_render_header(station_name, facts, uniform_queue)]
    if break_info is not None:
        lines.append(_render_break_line(break_info))
    lines.extend(_render_grouped_fact_lines(facts, uniform_queue))
    return "\n".join(lines)


def _uniform_queue_note(facts: list[StationFact]) -> str | None:
    queue_notes = {f.queue_note for f in facts if f.queue_note}
    return next(iter(queue_notes)) if len(queue_notes) == 1 else None


def _render_header(station_name: str, facts: list[StationFact], uniform_queue: str | None) -> str:
    if not facts:
        return f"информация по {station_name}."
    freshest = max(facts, key=lambda f: f.reported_at)
    time_str = freshest.reported_at.astimezone(MOSCOW_TZ).strftime("%H:%M")
    header_queue = f", {uniform_queue}" if uniform_queue else ""
    return f"информация по {station_name} на {time_str}{header_queue}."


def _render_break_line(b: StationBreak) -> str:
    kind_text = _BREAK_KIND_TEXT.get(b.kind, "технический перерыв")
    if b.until is not None:
        when = f"ожидается до {b.until.astimezone(MOSCOW_TZ):%H:%M}"
    elif b.duration_note:
        when = f"заявленная длительность {b.duration_note}"
    else:
        when = f"сообщили {_format_age(b.reported_minutes_ago)} назад"
    return f"{kind_text}: {when}."


def _render_grouped_fact_lines(facts: list[StationFact], uniform_queue: str | None) -> list[str]:
    # Группируем марки с одинаковым (статус, свежесть, конфликт, очередь) в
    # одну строку — обычный случай (всё свежо, без конфликтов, очередь одна
    # на всех или её нет) даёт ровно 2 строки: "Есть"/"Нет". Марка, которая
    # чем-то отличается от общей картины, естественным образом попадает в
    # свою собственную группу — компактность не ломается, а расхождение не
    # прячется молча.
    groups: dict[tuple[str, str, bool, str | None], list[str]] = {}
    for fact in sorted(facts, key=lambda f: f.grade):
        queue_for_line = None if fact.queue_note == uniform_queue else fact.queue_note
        key = (fact.status, fact.tier, fact.conflicting, queue_for_line)
        groups.setdefault(key, []).append(fact.grade)

    lines = []
    for key in sorted(groups, key=lambda k: (k[0] != "available", k[1], k[2])):
        status, tier, conflicting, queue_for_line = key
        grades = groups[key]
        status_word = "Есть" if status == "available" else "Нет"
        suffix = _FRESHNESS_NOTE[tier]
        if queue_for_line:
            suffix += f", {queue_for_line}"
        if conflicting:
            suffix += " (было иначе)"
        lines.append(f"{', '.join(grades)} - {status_word}{suffix}")
    return lines


def _format_age(age_minutes: float) -> str:
    if age_minutes < 1:
        return "меньше минуты"
    if age_minutes < 60:
        return f"{int(age_minutes)} мин"
    hours = age_minutes / 60
    if hours < 24:
        return f"{f'{hours:.1f}'.rstrip('0').rstrip('.')} ч"
    return f"{int(hours / 24)} дн"
