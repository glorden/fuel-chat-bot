"""Сроки жизни данных участников и обезличивание (D7, F4, K1;
ARCH_DECISIONS.md, решение Р7)."""

import asyncio
import logging
from datetime import datetime, timedelta, timezone

import pytest

import pipeline.pipeline as pipeline_module
import vk_handlers
from config import PROCESSED_MESSAGE_TTL_DAYS, RAW_TEXT_TTL_DAYS
from db.retention import apply_retention
from db.schema import get_connection
from privacy import author_fingerprint
from tests.vk_fakes import FakeBot, FakeVkMessage


@pytest.fixture
def env(monkeypatch):
    conn = get_connection(":memory:")
    monkeypatch.setattr(vk_handlers, "_conn", conn)
    monkeypatch.setattr(vk_handlers, "ALLOWED_PEER_IDS", frozenset({2000000001}))
    monkeypatch.setattr(vk_handlers, "_unknown_peers_logged", set())
    monkeypatch.setattr(vk_handlers, "_last_reply_at", {})
    monkeypatch.setattr(vk_handlers, "_recent_chat_replies", {})
    monkeypatch.setattr(vk_handlers, "_last_retention_at", float("-inf"))
    monkeypatch.setattr(pipeline_module, "LLM_ENABLED", False)
    return conn, FakeBot()


def _insert_report(conn, *, days_ago: float, author_id: int = 56734207, raw_text: str = "Вилга 92 есть"):
    reported_at = (datetime.now(timezone.utc) - timedelta(days=days_ago)).isoformat()
    conn.execute(
        "INSERT INTO fuel_report (station_id, fuel_grade, status, peer_id, conversation_message_id, "
        "author_id, reported_at, raw_text) VALUES ('lukoil_vilga', '92', 'available', 1, 1, ?, ?, ?)",
        (author_id, reported_at, raw_text),
    )
    conn.commit()


# --- Отпечаток автора вместо идентификатора ---


def test_fingerprint_is_stable_and_distinguishes_authors():
    assert author_fingerprint(56734207) == author_fingerprint(56734207)
    assert author_fingerprint(56734207) != author_fingerprint(56734208)


def test_fingerprint_does_not_contain_the_identifier():
    assert "56734207" not in author_fingerprint(56734207)


def test_new_facts_store_a_fingerprint_and_no_vk_identifier(env):
    conn, bot = env
    asyncio.run(vk_handlers.handle_message(
        bot, FakeVkMessage(text="Лукойл Вилга только 92 на табло", from_id=56734207, cmid=1)
    ))

    author_id, author_hash = conn.execute("SELECT author_id, author_hash FROM fuel_report").fetchone()
    assert author_id == 0
    assert author_hash == author_fingerprint(56734207)


# --- Ретенция ---


def test_old_raw_text_is_cleared_but_the_fact_survives(env):
    conn, _ = env
    _insert_report(conn, days_ago=RAW_TEXT_TTL_DAYS + 1)

    stats = apply_retention(conn)

    assert stats["texts_cleared"] == 1
    row = conn.execute("SELECT station_id, fuel_grade, status, raw_text FROM fuel_report").fetchone()
    assert row == ("lukoil_vilga", "92", "available", "")


def test_recent_raw_text_is_left_alone(env):
    conn, _ = env
    _insert_report(conn, days_ago=1)

    stats = apply_retention(conn)

    assert stats["texts_cleared"] == 0
    assert conn.execute("SELECT raw_text FROM fuel_report").fetchone()[0] == "Вилга 92 есть"


def test_historical_vk_identifiers_are_wiped(env):
    conn, _ = env
    _insert_report(conn, days_ago=RAW_TEXT_TTL_DAYS + 1, author_id=56734207)

    stats = apply_retention(conn)

    assert stats["authors_cleared"] == 1
    assert conn.execute("SELECT author_id FROM fuel_report").fetchone()[0] == 0


def test_old_dedup_rows_are_deleted(env):
    conn, _ = env
    old = (datetime.now(timezone.utc) - timedelta(days=PROCESSED_MESSAGE_TTL_DAYS + 1)).isoformat()
    fresh = datetime.now(timezone.utc).isoformat()
    conn.execute("INSERT INTO processed_message VALUES (1, 1, ?)", (old,))
    conn.execute("INSERT INTO processed_message VALUES (1, 2, ?)", (fresh,))
    conn.commit()

    stats = apply_retention(conn)

    assert stats["dedup_deleted"] == 1
    assert [r[0] for r in conn.execute("SELECT conversation_message_id FROM processed_message")] == [2]


def test_retention_is_idempotent(env):
    conn, _ = env
    _insert_report(conn, days_ago=RAW_TEXT_TTL_DAYS + 1)

    apply_retention(conn)
    second = apply_retention(conn)

    # Второй прогон не находит работы — иначе он бы каждый час трогал одни
    # и те же строки без причины.
    assert second == {"dedup_deleted": 0, "texts_cleared": 0, "authors_cleared": 0}


def test_retention_runs_at_most_once_per_interval(env, monkeypatch):
    conn, bot = env
    calls = []
    monkeypatch.setattr(vk_handlers, "apply_retention", lambda c: calls.append(1))

    for cmid in (1, 2, 3):
        asyncio.run(vk_handlers.handle_message(bot, FakeVkMessage(text="всем доброе утро", cmid=cmid)))

    assert len(calls) == 1


def test_retention_failure_does_not_break_message_handling(env, monkeypatch):
    conn, bot = env

    def boom(_conn):
        raise RuntimeError("диск кончился")

    monkeypatch.setattr(vk_handlers, "apply_retention", boom)
    asyncio.run(vk_handlers.handle_message(
        bot, FakeVkMessage(text="Лукойл Вилга только 92 на табло", cmid=1)
    ))
    assert conn.execute("SELECT COUNT(*) FROM fuel_report").fetchone()[0] == 1


# --- Гигиена логов ---


def test_handler_log_line_carries_no_message_text_and_no_vk_id(env, caplog):
    conn, bot = env
    with caplog.at_level(logging.INFO, logger="vk_bot"):
        asyncio.run(vk_handlers.handle_message(
            bot, FakeVkMessage(text="Лукойл Вилга только 92 на табло", from_id=56734207, cmid=1)
        ))

    lines = "\n".join(r.getMessage() for r in caplog.records)
    assert "outcome=" in lines  # строка про обработку вообще написана
    assert "Лукойл Вилга только 92 на табло" not in lines
    assert "56734207" not in lines
