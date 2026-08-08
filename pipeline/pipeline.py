import sqlite3
from dataclasses import dataclass

from db import repo
from pipeline.extract import extract
from pipeline.prefilter import is_on_topic
from pipeline.qa import answer_question
from pipeline.resolve_station import resolve_station


@dataclass
class PipelineOutcome:
    label: str
    reply_text: str | None = None


def process_message(
    conn: sqlite3.Connection,
    *,
    text: str,
    peer_id: int,
    conversation_message_id: int,
    author_id: int,
) -> PipelineOutcome:
    """Run one message through the pipeline."""
    if not is_on_topic(text):
        return PipelineOutcome("off_topic")

    result = extract(text)

    if result.message_type == "irrelevant":
        return PipelineOutcome("irrelevant")

    if result.message_type == "question":
        station_id = resolve_station(text)
        reply_text = answer_question(conn, station_id=station_id, grades=result.question_grades)
        return PipelineOutcome("question", reply_text=reply_text)

    station_id = resolve_station(text)
    if station_id is None:
        repo.insert_unresolved_mention(
            conn,
            peer_id=peer_id,
            conversation_message_id=conversation_message_id,
            author_id=author_id,
            raw_text=text,
        )
        return PipelineOutcome("unresolved")

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
    return PipelineOutcome(f"report:{station_id}")
