from pipeline.facts import MOSCOW_TZ, StationFact

_FRESHNESS_NOTE = {
    "fresh": "",
    "stale": " (устарело)",
    "very_stale": " (данные старые)",
}


def render_station_answer(station_name: str, facts: list[StationFact]) -> str:
    if not facts:
        return f"По «{station_name}» пока нет данных в чате — никто не сообщал."

    freshest = max(facts, key=lambda f: f.reported_at)
    time_str = freshest.reported_at.astimezone(MOSCOW_TZ).strftime("%H:%M")

    queue_notes = {f.queue_note for f in facts if f.queue_note}
    uniform_queue = next(iter(queue_notes)) if len(queue_notes) == 1 else None
    header_queue = f", {uniform_queue}" if uniform_queue else ""

    lines = [f"информация по {station_name} на {time_str}{header_queue}."]
    lines.extend(_render_grouped_fact_lines(facts, uniform_queue))
    return "\n".join(lines)


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
