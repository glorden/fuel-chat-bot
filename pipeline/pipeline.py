import sqlite3

from db import repo
from pipeline.extract import extract
from pipeline.prefilter import is_on_topic
from pipeline.resolve_station import resolve_station


def process_message(
    conn: sqlite3.Connection,
    *,
    text: str,
    peer_id: int,
    conversation_message_id: int,
    author_id: int,
) -> str:
    """Run one message through the pipeline. Returns a short outcome label for logging/tests."""
    if not is_on_topic(text):
        return "off_topic"

    result = extract(text)

    if result.message_type == "irrelevant":
        return "irrelevant"

    if result.message_type == "question":
        # Ответы на вопросы — Stage 3 (qa.py). Здесь только классификация,
        # в БД для report/unresolved вопрос не пишем.
        return "question"

    station_id = resolve_station(text)
    if station_id is None:
        repo.insert_unresolved_mention(
            conn,
            peer_id=peer_id,
            conversation_message_id=conversation_message_id,
            author_id=author_id,
            raw_text=text,
        )
        return "unresolved"

    for report in result.reports:
        repo.insert_fuel_report(
            conn,
            station_id=station_id,
            report=report,
            queue_note=result.queue_note,
            peer_id=peer_id,
            conversation_message_id=conversation_message_id,
            author_id=author_id,
            raw_text=text,
        )
    return f"report:{station_id}"
