import logging
import sqlite3
from dataclasses import dataclass

from config import LLM_ENABLED
from db import repo
from pipeline.extract import ExtractResult, extract
from pipeline.prefilter import is_on_topic
from pipeline.qa import answer_question
from pipeline.resolve_station import resolve_station

log = logging.getLogger("vk_bot")


@dataclass
class PipelineOutcome:
    label: str
    reply_text: str | None = None


def _analyze(text: str) -> tuple[ExtractResult, str | None]:
    """Классификация+извлечение+резолв станции. Пробует LLM (если включён),
    при любом сбое или если LLM выключен — текущий rule-based путь."""
    if LLM_ENABLED:
        from llm.client import analyze as llm_analyze

        llm_result = llm_analyze(text)
        if llm_result is not None:
            log.info(
                "path=llm message_type=%s station_id=%s",
                llm_result.extract_result.message_type,
                llm_result.station_id,
            )
            return llm_result.extract_result, llm_result.station_id
        log.info("path=rule_based reason=llm_fallback")

    result = extract(text)
    station_id = resolve_station(text) if result.message_type != "irrelevant" else None
    return result, station_id


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

    result, station_id = _analyze(text)

    if result.message_type == "irrelevant":
        return PipelineOutcome("irrelevant")

    if result.message_type == "question":
        reply_text = answer_question(conn, station_id=station_id, grades=result.question_grades)
        return PipelineOutcome("question", reply_text=reply_text)

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
