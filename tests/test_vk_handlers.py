import asyncio
import logging
import time

import pytest
from vkbottle.bot import Bot, Message

import pipeline.pipeline as pipeline_module
import vk_handlers
from db.schema import get_connection
from pipeline.qa import answer_question
from vk_handlers import (
    _is_allowed_peer,
    _is_limit_list_command,
    _parse_admin_command,
    _quoted_context_text,
    _recent_author_message,
    register_handlers,
)


class _FakeForeign:
    def __init__(self, text):
        self.text = text


class _FakeMessage:
    def __init__(self, reply_message=None, fwd_messages=None):
        self.reply_message = reply_message
        self.fwd_messages = fwd_messages or []


def test_quoted_context_text_empty_when_no_reply_no_fwd():
    assert _quoted_context_text(_FakeMessage()) == ""


def test_quoted_context_text_includes_reply_message():
    msg = _FakeMessage(reply_message=_FakeForeign("95 нет на Лукойле"))
    assert _quoted_context_text(msg) == "95 нет на Лукойле"


def test_quoted_context_text_includes_multiple_fwd_messages_joined():
    msg = _FakeMessage(fwd_messages=[_FakeForeign("первое сообщение"), _FakeForeign("второе сообщение")])
    assert _quoted_context_text(msg) == "первое сообщение\nвторое сообщение"


def test_quoted_context_text_combines_fwd_and_reply():
    msg = _FakeMessage(
        fwd_messages=[_FakeForeign("форвард")],
        reply_message=_FakeForeign("реплай"),
    )
    assert _quoted_context_text(msg) == "форвард\nреплай"


def test_quoted_context_text_skips_empty_texts():
    msg = _FakeMessage(
        fwd_messages=[_FakeForeign(""), _FakeForeign("   ")],
        reply_message=_FakeForeign(None),
    )
    assert _quoted_context_text(msg) == ""


def test_recent_author_message_returns_none_when_no_entry():
    assert _recent_author_message((999999, 888888)) is None


def test_recent_author_message_returns_fresh_entry():
    key = (999999, 888889)
    vk_handlers._last_author_message[key] = ("95 есть на Газпроме?", time.monotonic())
    try:
        assert _recent_author_message(key) == "95 есть на Газпроме?"
    finally:
        del vk_handlers._last_author_message[key]


def test_recent_author_message_expires_after_ttl():
    key = (999999, 888890)
    expired_at = time.monotonic() - vk_handlers._AUTHOR_CONTEXT_TTL_SECONDS - 1
    vk_handlers._last_author_message[key] = ("95 есть на Газпроме?", expired_at)
    try:
        assert _recent_author_message(key) is None
    finally:
        del vk_handlers._last_author_message[key]


def test_parse_admin_command_recognizes_on_and_off():
    assert _parse_admin_command("!вкл") is True
    assert _parse_admin_command("!выкл") is False


def test_parse_admin_command_case_insensitive_and_trims_whitespace():
    assert _parse_admin_command("  !ВКЛ  ") is True
    assert _parse_admin_command("!Выкл") is False


def test_parse_admin_command_none_for_regular_text():
    assert _parse_admin_command("Есть 95 на Роснефти?") is None
    assert _parse_admin_command("!вкл, пожалуйста") is None  # не голая команда
    assert _parse_admin_command("") is None


def test_is_limit_list_command_recognizes_singular_and_plural():
    assert _is_limit_list_command("!лимит") is True
    assert _is_limit_list_command("!лимиты") is True
    assert _is_limit_list_command("  !ЛИМИТЫ  ") is True


def test_is_limit_list_command_false_for_regular_text():
    assert _is_limit_list_command("Какой лимит на Роснефти?") is False
    assert _is_limit_list_command("!лимиты пожалуйста") is False
    assert _is_limit_list_command("") is False


# --- Граница беседы: allowlist из двух бесед (F2, ARCH_DECISIONS.md Р4) ---
#
# Раньше peer_id не проверялся нигде: отчёты из одной беседы формировали
# ответы в другой, а сообщество, добавленное в любой чужой чат, и читало
# накопленную базу, и писало в неё. Тесты ниже гоняют настоящий обработчик,
# а не только хелпер — тело on_message до сих пор не было покрыто вовсе.


def _make_message(*, peer_id, text, from_id=111, conversation_message_id=1):
    return Message(
        id=1, date=0, version=0, out=0, peer_id=peer_id, from_id=from_id,
        text=text, conversation_message_id=conversation_message_id,
    )


def _registered_handler():
    bot = Bot(token="dummy-token-for-tests")
    register_handlers(bot)
    return bot.labeler.views()["message"].handlers[0].handler


@pytest.fixture
def handler_env(monkeypatch):
    """Настоящий обработчик на пустой in-memory базе. Подмена `_conn`
    обязательна: модуль открывает боевую bot.db прямо на импорте (находка
    A4), и без неё тест писал бы в неё. LLM выключен — только rule-based,
    без живых вызовов."""
    conn = get_connection(":memory:")
    monkeypatch.setattr(vk_handlers, "_conn", conn)
    monkeypatch.setattr(vk_handlers, "ALLOWED_PEER_IDS", frozenset({2000000001, 2000000002}))
    monkeypatch.setattr(vk_handlers, "_unknown_peers_logged", set())
    monkeypatch.setattr(pipeline_module, "LLM_ENABLED", False)
    return conn, _registered_handler()


def test_is_allowed_peer_checks_the_allowlist(monkeypatch):
    monkeypatch.setattr(vk_handlers, "ALLOWED_PEER_IDS", frozenset({2000000001, 2000000002}))
    monkeypatch.setattr(vk_handlers, "_unknown_peers_logged", set())
    assert _is_allowed_peer(2000000001) is True
    assert _is_allowed_peer(2000000002) is True
    assert _is_allowed_peer(2000000003) is False
    assert _is_allowed_peer(111) is False  # личка тоже не обслуживается


def test_message_from_foreign_conversation_is_ignored_entirely(handler_env):
    conn, handler = handler_env
    asyncio.run(handler(_make_message(peer_id=2000000999, text="РН лесной, 95 есть на 4,5 колонке.")))
    # Ни факта, ни даже отметки о том, что сообщение видели: из чужой
    # беседы не читаем и не пишем ничего.
    assert conn.execute("SELECT COUNT(*) FROM fuel_report").fetchone()[0] == 0
    assert conn.execute("SELECT COUNT(*) FROM processed_message").fetchone()[0] == 0


def test_same_message_from_allowed_conversation_is_recorded(handler_env):
    # Контроль к предыдущему тесту: дело именно в беседе, а не в том, что
    # сообщение почему-то не разбирается.
    conn, handler = handler_env
    asyncio.run(handler(_make_message(peer_id=2000000001, text="РН лесной, 95 есть на 4,5 колонке.")))
    assert conn.execute("SELECT station_id, fuel_grade FROM fuel_report").fetchone() == (
        "rosneft_lesnoy_79", "95",
    )
    assert conn.execute("SELECT COUNT(*) FROM processed_message").fetchone()[0] == 1


def test_both_allowed_conversations_share_one_fact_base(handler_env):
    # Прямое решение владельца (Р4): беседы ровно две, база у них общая —
    # отчёт из одной отвечает на вопрос в другой.
    conn, handler = handler_env
    asyncio.run(handler(_make_message(peer_id=2000000001, text="РН лесной, 95 есть на 4,5 колонке.")))
    answer = answer_question(conn, station_id="rosneft_lesnoy_79", grades=["95"])
    assert answer is not None and "95 - Есть" in answer


def test_foreign_conversation_is_logged_once_not_on_every_message(handler_env, caplog):
    _, handler = handler_env
    with caplog.at_level(logging.WARNING, logger="vk_bot"):
        asyncio.run(handler(_make_message(peer_id=2000000999, text="95 есть")))
        asyncio.run(handler(_make_message(peer_id=2000000999, text="92 есть", conversation_message_id=2)))
        asyncio.run(handler(_make_message(peer_id=2000000999, text="дт есть", conversation_message_id=3)))
    assert len([r for r in caplog.records if "allowlist" in r.getMessage()]) == 1
