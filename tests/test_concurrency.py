"""Развязка обработки и цикла событий, дебаунс и время факта
(H1, H2, H6, F3; ARCH_DECISIONS.md, решение Р3).
"""

import asyncio
import time
from datetime import datetime, timedelta, timezone

import pytest

import pipeline.pipeline as pipeline_module
import vk_handlers
from db.schema import get_connection
from tests.vk_fakes import FakeBot, FakeVkMessage

_SLOW_CALL_SECONDS = 0.3


@pytest.fixture
def env(monkeypatch):
    conn = get_connection(":memory:")
    monkeypatch.setattr(vk_handlers, "_conn", conn)
    monkeypatch.setattr(vk_handlers, "ALLOWED_PEER_IDS", frozenset({2000000001}))
    monkeypatch.setattr(vk_handlers, "_unknown_peers_logged", set())
    monkeypatch.setattr(vk_handlers, "_last_reply_at", {})
    monkeypatch.setattr(vk_handlers, "_recent_chat_replies", {})
    monkeypatch.setattr(vk_handlers, "_last_author_message", {})
    monkeypatch.setattr(pipeline_module, "LLM_ENABLED", False)
    return conn, FakeBot()


def _make_provider_slow(monkeypatch):
    """Блокирующий вызов провайдера — ровно то, что делает синхронный HTTP.
    Отвечает успешно (иначе сработал бы гвард Р1 и факты не записались бы):
    измеряется здесь не качество разбора, а то, занимает ли вызов цикл
    событий."""
    import llm.client
    from llm.client import LLMAnalysis
    from pipeline.extract import ExtractResult, ReportItem

    def slow_analyze(*args, **kwargs):
        time.sleep(_SLOW_CALL_SECONDS)
        return LLMAnalysis(
            extract_result=ExtractResult(message_type="report", reports=[ReportItem("92", "available")]),
            station_id="lukoil_vilga",
        )

    monkeypatch.setattr(llm.client, "analyze", slow_analyze)
    monkeypatch.setattr(pipeline_module, "LLM_ENABLED", True)


def test_burst_is_processed_concurrently_not_one_after_another(env, monkeypatch):
    # H1: замер аудита — 5 сообщений по 1 с обрабатывались 5 с, и все ответы
    # уходили в самом конце: первый спросивший ждал не своё время, а время
    # всего всплеска. Теперь блокирует только поток провайдера.
    conn, bot = env
    _make_provider_slow(monkeypatch)

    messages = [
        FakeVkMessage(text=f"Лукойл Вилга только 92 на табло, {i}", cmid=100 + i, from_id=200 + i)
        for i in range(5)
    ]

    async def run_all():
        await asyncio.gather(*(vk_handlers.handle_message(bot, m) for m in messages))

    started = time.monotonic()
    asyncio.run(run_all())
    elapsed = time.monotonic() - started

    sequential = _SLOW_CALL_SECONDS * len(messages)
    assert elapsed < sequential / 2, f"{elapsed:.2f} с — похоже, обработка снова последовательная"
    assert conn.execute("SELECT COUNT(*) FROM fuel_report").fetchone()[0] == 5


def test_five_different_people_all_get_an_answer(env):
    # H2: дебаунс был один на беседу, и при всплеске отвечали одному из
    # пяти — ровно тогда, когда чат активен и бот полезнее всего.
    conn, bot = env
    asyncio.run(vk_handlers.handle_message(bot, FakeVkMessage(text="Лукойл Вилга только 92 на табло", cmid=1)))

    questions = [FakeVkMessage(text="Есть 92 в вилге?", cmid=10 + i, from_id=300 + i) for i in range(5)]
    for q in questions:
        asyncio.run(vk_handlers.handle_message(bot, q))

    answered = [q for q in questions if q.replies]
    assert len(answered) == 5


def test_same_person_asking_twice_in_a_row_is_still_debounced(env):
    conn, bot = env
    asyncio.run(vk_handlers.handle_message(bot, FakeVkMessage(text="Лукойл Вилга только 92 на табло", cmid=1)))

    first = FakeVkMessage(text="Есть 92 в вилге?", cmid=2, from_id=777)
    second = FakeVkMessage(text="Есть 92 в вилге?", cmid=3, from_id=777)
    asyncio.run(vk_handlers.handle_message(bot, first))
    asyncio.run(vk_handlers.handle_message(bot, second))

    assert len(first.replies) == 1
    assert second.replies == []


def test_public_limit_command_no_longer_silences_other_people(env):
    # F3: !лимит доступна всем и делила одно окно с содержательными
    # ответами — любой участник мог держать бота в состоянии, где реальные
    # вопросы остаются без ответа.
    conn, bot = env
    asyncio.run(vk_handlers.handle_message(bot, FakeVkMessage(text="Лукойл Вилга только 92 на табло", cmid=1)))

    spammer = FakeVkMessage(text="!лимит", cmid=2, from_id=555)
    asyncio.run(vk_handlers.handle_message(bot, spammer))
    assert len(spammer.replies) == 1

    other = FakeVkMessage(text="Есть 92 в вилге?", cmid=3, from_id=556)
    asyncio.run(vk_handlers.handle_message(bot, other))
    assert len(other.replies) == 1


def test_chat_wide_cap_stops_a_pathological_burst(env, caplog):
    conn, bot = env
    asyncio.run(vk_handlers.handle_message(bot, FakeVkMessage(text="Лукойл Вилга только 92 на табло", cmid=1)))

    # Каждый вопрос от своего автора — авторский дебаунс не мешает, упереться
    # можно только в потолок на беседу.
    asked = []
    for i in range(vk_handlers._CHAT_REPLY_CAP + 3):
        q = FakeVkMessage(text="Есть 92 в вилге?", cmid=20 + i, from_id=400 + i)
        asyncio.run(vk_handlers.handle_message(bot, q))
        asked.append(q)

    answered = [q for q in asked if q.replies]
    assert len(answered) == vk_handlers._CHAT_REPLY_CAP


def test_fact_time_comes_from_the_message_not_from_processing(env):
    # H6: время факта было временем записи. При очереди факт получал время
    # на несколько секунд позже, чем человек его написал, а именно оно потом
    # решает, какой отчёт свежее и не устарел ли он.
    conn, bot = env
    sent_at = datetime.now(timezone.utc) - timedelta(minutes=45)
    asyncio.run(vk_handlers.handle_message(
        bot, FakeVkMessage(text="Лукойл Вилга только 92 на табло", cmid=1, date=sent_at)
    ))

    stored = conn.execute("SELECT reported_at FROM fuel_report").fetchone()[0]
    assert datetime.fromisoformat(stored) == sent_at


def test_implausible_message_time_falls_back_to_processing_time(env):
    # Дата из будущего никогда бы не устарела, эпоха-ноль — наоборот,
    # устарела бы навсегда. Оба случая лечатся временем обработки.
    conn, bot = env
    before = datetime.now(timezone.utc)
    for cmid, date in ((1, datetime(1970, 1, 1, tzinfo=timezone.utc)),
                       (2, datetime.now(timezone.utc) + timedelta(hours=3))):
        asyncio.run(vk_handlers.handle_message(
            bot, FakeVkMessage(text="Лукойл Вилга только 92 на табло", cmid=cmid, date=date)
        ))

    for (stored,) in conn.execute("SELECT reported_at FROM fuel_report"):
        assert before <= datetime.fromisoformat(stored) <= datetime.now(timezone.utc)


def test_naive_message_time_is_stored_with_a_timezone(env):
    # D3: одна naive-строка в БД навсегда ломает ответы по станции —
    # сравнение с aware-временем кидает TypeError на каждый вопрос.
    conn, bot = env
    naive = datetime.now(timezone.utc).replace(tzinfo=None)
    asyncio.run(vk_handlers.handle_message(
        bot, FakeVkMessage(text="Лукойл Вилга только 92 на табло", cmid=1, date=naive)
    ))

    stored = conn.execute("SELECT reported_at FROM fuel_report").fetchone()[0]
    assert datetime.fromisoformat(stored).tzinfo is not None

    from pipeline.qa import answer_question

    assert answer_question(conn, station_id="lukoil_vilga", grades=["92"]) is not None
