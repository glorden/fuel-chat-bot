from pathlib import Path

import yaml

_GAZETTEER_PATH = Path(__file__).resolve().parent.parent / "gazetteer.yaml"


def _stations_brief() -> str:
    data = yaml.safe_load(_GAZETTEER_PATH.read_text(encoding="utf-8"))
    lines = []
    for s in data["stations"]:
        aliases = ", ".join(s["brand_aliases"] + s["location_aliases"]) or "—"
        lines.append(
            f"- id={s['id']}: {s['name']}, бренд «{s['brand']}», адрес: {s['address'] or '—'}, "
            f"локальные названия/алиасы: {aliases}"
        )
    return "\n".join(lines)


def build_system_prompt() -> str:
    return f"""Ты разбираешь сообщения из тематического VK-чата про заправку топливом во время дефицита бензина в городе.

Раздели сообщение на:
- report — отчёт о наличии топлива и/или очереди на конкретной АЗС
- question — вопрос о наличии топлива/очереди (вопросительный знак не обязателен, "Подскажите, есть ли 92" тоже вопрос)
- irrelevant — не по теме, либо по теме, но без конкретного факта о марке/АЗС (благодарности, вопросы про цену/лимит литров, реплики не по делу)

Список известных АЗС:
{_stations_brief()}

Правила:
- station_id — только id из списка выше, если уверен; если бренд упомянут без адреса, а у бренда несколько точек в списке, или станции вообще нет в списке — верни null, не угадывай.
- Опечатки и склонения бренда/адреса не должны мешать — сопоставляй по смыслу (например "газпропе" это опечатка "газпроме").
- В report извлекай КАЖДУЮ упомянутую марку топлива со статусом: отрицание ПЕРЕД маркой ("нет 95-го", "закончился 92") — unavailable, обычное упоминание/наличие ("95 есть", "только 92 на табло") — available. Осторожно с порядком слов: "92 нет очереди" — это про очередь ("нет" относится к очереди, а не к марке), 92 при этом available, а не unavailable.
- В question извлекай марки, о которых спрашивают (question_grades), список может быть пустым, если вопрос без привязки к марке.
- queue_note — короткая заметка про очередь на русском ("без очереди", "~12 мин", "есть очередь") или null, если не упоминалась.

Примеры разбора:
1. "РН лесной, 95 есть на 4,5 колонке. Очередь." → report, station_id=rosneft_lesnoy_79, reports=[{{"grade":"95","status":"available"}}], queue_note="есть очередь"
2. "Нет 95го в янишполе\\n92 и ДТ" → report, station_id=null (Янишполе — посёлок, не АЗС из списка), reports=[{{"grade":"95","status":"unavailable"}},{{"grade":"92","status":"available"}},{{"grade":"ДТ","status":"available"}}], queue_note=null
3. "на газпропе есть 95?" → question, station_id=gazprom (опечатка "газпроме", бренд единственный в списке), question_grades=["95"]
4. "Спасибо, заправилась)" → irrelevant, station_id=null, reports=[], question_grades=[], queue_note=null
"""
