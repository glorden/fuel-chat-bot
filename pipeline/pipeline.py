import asyncio
import logging
import sqlite3
from dataclasses import dataclass
from datetime import datetime, timezone

from config import LLM_ENABLED
from db import repo
from pipeline.extract import ExtractResult, extract
from pipeline.prefilter import is_on_topic
from pipeline.qa import answer_brand_limit_question, answer_question
from pipeline.resolve_station import resolve_station

log = logging.getLogger("vk_bot")


@dataclass
class PipelineOutcome:
    label: str
    reply_text: str | None = None


async def _analyze(
    text: str,
    *,
    own_text: str,
    quoted_context: str | None = None,
    previous_message: str | None = None,
) -> tuple[ExtractResult, str | None]:
    """Классификация+извлечение+резолв станции. Пробует LLM (если включён),
    при любом сбое или если LLM выключен — текущий rule-based путь.

    `text` — уже склеенный (цитата+свой текст) блок, его получает rule-based:
    простой конкатенации достаточно, regex не путает "контекст" с "текущим
    сообщением". LLM-ветка получает `own_text` отдельно от `quoted_context`/
    `previous_message`, каждый со своей меткой (см. llm/prompts.py::
    build_user_content) — плоская склейка без разметки заставляла модель
    иногда путать "вопрос где-то в тексте" с "текущее сообщение — вопрос"
    (живая находка 2026-08-11: короткие подтверждения вроде "Да"/"Есть."
    в ответ на процитированный вопрос повторно определялись как question)."""
    if LLM_ENABLED:
        from llm.client import analyze as llm_analyze

        # Единственное место во всей обработке, которое блокирует надолго:
        # 1.8 с в норме, ~10 с при сбое провайдера. Раньше оно держало весь
        # цикл событий, и всплеск из пяти вопросов обрабатывался строго
        # по очереди — первый спросивший ждал не своё время, а время всего
        # всплеска (находки H1/H2). Всё остальное (БД, шаблоны) занимает
        # миллисекунды и остаётся в цикле событий: соединение SQLite
        # привязано к потоку, в котором создано (H5), и выносить его в
        # рабочий поток не требуется.
        llm_result = await asyncio.to_thread(
            llm_analyze, own_text, previous_message=previous_message, quoted_context=quoted_context
        )
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


async def process_message(
    conn: sqlite3.Connection,
    *,
    text: str,
    peer_id: int,
    conversation_message_id: int,
    author_id: int,
    own_text: str | None = None,
    quoted_context: str | None = None,
    previous_message: str | None = None,
    reported_at: datetime | None = None,
) -> PipelineOutcome:
    """Run one message through the pipeline. `text` may be enriched with
    quoted reply/forward context (see vk_handlers.py); `own_text` — what the
    author actually typed themselves, defaults to `text` when not given.
    `quoted_context` — the same reply/forward text, passed separately so the
    LLM branch can label it instead of seeing it baked into `text` unmarked.
    `previous_message` — the same author's previous on-topic message, used
    only by the LLM path to resolve implicit references (see _analyze).
    `reported_at` — время самого сообщения (`message.date` из VK), а не
    время обработки: при очереди факт иначе получает время на несколько
    секунд позже, чем человек его написал, и именно это время потом решает,
    какой отчёт свежее (находка H6)."""
    if own_text is None:
        own_text = text
    if reported_at is None:
        reported_at = datetime.now(timezone.utc)

    if not is_on_topic(text):
        with conn:
            repo.mark_processed(conn, peer_id, conversation_message_id)
        return PipelineOutcome("off_topic")

    # Анализ (в т.ч. вызов LLM) — ДО транзакции: держать открытую запись
    # секундами ради сетевого вызова нельзя, тем более что дальше обработка
    # станет конкурентной.
    result, station_id = await _analyze(
        text, own_text=own_text, quoted_context=quoted_context, previous_message=previous_message
    )

    # Одна транзакция на сообщение: либо легли все факты и отметка
    # "обработано", либо не легло ничего (находки D5, G1). Отметка внутри
    # пайплайна, а не в vk_handlers, именно поэтому — иначе её нельзя
    # положить в ту же транзакцию, не растягивая транзакцию на вызов LLM.
    with conn:
        outcome = _record(
            conn,
            result,
            station_id,
            text=text,
            own_text=own_text,
            peer_id=peer_id,
            conversation_message_id=conversation_message_id,
            author_id=author_id,
            reported_at=reported_at,
        )
        repo.mark_processed(conn, peer_id, conversation_message_id)
    return outcome


def _record(
    conn: sqlite3.Connection,
    result: ExtractResult,
    station_id: str | None,
    *,
    text: str,
    own_text: str,
    peer_id: int,
    conversation_message_id: int,
    author_id: int,
    reported_at: datetime,
) -> PipelineOutcome:
    """Всё, что пишется по итогам разбора. Вызывается внутри транзакции —
    сам не коммитит и не открывает свою."""
    if result.message_type == "irrelevant":
        return PipelineOutcome("irrelevant")

    if result.message_type == "question":
        reply_text = answer_question(conn, station_id=station_id, grades=result.question_grades)
        if reply_text is None and station_id is None:
            # Бренд назван (РН/ТН/Лукойл), но конкретная точка не резолвится
            # (несколько точек у бренда) — лимит общий на весь бренд, так что
            # можно ответить и без резолва станции (см. qa.py).
            reply_text = answer_brand_limit_question(text)
        return PipelineOutcome("question", reply_text=reply_text)

    if text != own_text and own_text and extract(own_text).message_type != "report":
        # Репорт всплыл только благодаря подклеенной цитате (форвард/реплай),
        # а свой текст автора сам по себе ничего не сообщает — не пишем в
        # БД, иначе случайное "спасибо"-реплаем искусственно освежит старый
        # факт под новым timestamp.
        return PipelineOutcome("report_suppressed_quote_only")

    if station_id is None:
        repo.insert_unresolved_mention(
            conn,
            peer_id=peer_id,
            conversation_message_id=conversation_message_id,
            author_id=author_id,
            seen_at=reported_at,
            raw_text=text,
        )
        return PipelineOutcome("unresolved")

    for report in result.reports:
        repo.insert_fuel_report(
            conn,
            station_id=station_id,
            report=report,
            queue=result.queue,
            peer_id=peer_id,
            conversation_message_id=conversation_message_id,
            author_id=author_id,
            reported_at=reported_at,
            raw_text=text,
        )

    if result.break_info is not None:
        repo.insert_station_break(
            conn,
            station_id=station_id,
            break_info=result.break_info,
            peer_id=peer_id,
            conversation_message_id=conversation_message_id,
            author_id=author_id,
            reported_at=reported_at,
            raw_text=text,
        )

    if result.limit_info is not None:
        repo.insert_fuel_limit(
            conn,
            station_id=station_id,
            limit_info=result.limit_info,
            peer_id=peer_id,
            conversation_message_id=conversation_message_id,
            author_id=author_id,
            reported_at=reported_at,
            raw_text=text,
        )

    return PipelineOutcome(f"report:{station_id}")
