"""Канал команд в личке владельца.

Личка — канал управления, а не третий источник данных: отсюда не пишется ни
одного факта, и решение Р4 (ровно две беседы) этим не размывается. Ветка
стоит до гейта по allowlist, поэтому здесь же проверяется, что сам гейт для
бесед не сломан.

Важно (находка A4): импорт vk_handlers открывает боевую bot.db, поэтому
фикстура обязана подменить _conn — и нельзя пользоваться monkeypatch.undo(),
он откатывает и эту подмену тоже.
"""

import asyncio

import pytest

import pipeline.pipeline as pipeline_module
import vk_handlers
from db import repo
from db.schema import get_connection
from tests.vk_fakes import FakeBot, FakeVkMessage

ADMIN = 3022748
CHAT = 2000000001


@pytest.fixture
def env(monkeypatch):
    conn = get_connection(":memory:")
    monkeypatch.setattr(vk_handlers, "_conn", conn)
    monkeypatch.setattr(vk_handlers, "ALLOWED_PEER_IDS", frozenset({CHAT}))
    monkeypatch.setattr(vk_handlers, "ADMIN_ID", ADMIN)
    monkeypatch.setattr(vk_handlers, "_unknown_peers_logged", set())
    monkeypatch.setattr(vk_handlers, "_foreign_private_logged", set())
    monkeypatch.setattr(vk_handlers, "_last_reply_at", {})
    monkeypatch.setattr(vk_handlers, "_recent_chat_replies", {})
    monkeypatch.setattr(pipeline_module, "LLM_ENABLED", False)
    return conn, FakeBot()


def _private(text, *, cmid=1, from_id=ADMIN, peer_id=ADMIN):
    """Личное сообщение: у него peer_id равен id отправителя — проверено
    живьём на Long Poll, заглушка повторяет именно это."""
    return FakeVkMessage(peer_id=peer_id, text=text, from_id=from_id, cmid=cmid, is_mentioned=False)


def _run(bot, message):
    asyncio.run(vk_handlers.handle_message(bot, message))


def _sent(bot):
    return [call["message"] for call in bot.api.sent]


def test_enable_command_in_private_switches_auto_reply_on(env):
    conn, bot = env
    repo.set_auto_reply_enabled(conn, enabled=False, changed_by=ADMIN)

    _run(bot, _private("!вкл"))

    assert repo.get_auto_reply_enabled(conn, default=False) is True
    assert _sent(bot) == ["Автоответ на вопросы включён."]


def test_disable_command_in_private_switches_auto_reply_off(env):
    conn, bot = env
    repo.set_auto_reply_enabled(conn, enabled=True, changed_by=ADMIN)

    _run(bot, _private("!выкл"))

    assert repo.get_auto_reply_enabled(conn, default=True) is False
    assert _sent(bot) == ["Автоответ на вопросы выключен."]


def test_command_tolerates_case_and_spaces(env):
    conn, bot = env

    _run(bot, _private("  !ВКЛ  "))

    assert repo.get_auto_reply_enabled(conn, default=False) is True


def test_unknown_command_gets_the_list_of_commands(env):
    _conn, bot = env

    _run(bot, _private("!ошибка роснефть лыжная"))

    assert len(_sent(bot)) == 1
    assert "Не знаю команду «!ошибка»" in _sent(bot)[0]
    assert "!помощь" in _sent(bot)[0]


def test_plain_text_in_private_gets_the_list_of_commands(env):
    _conn, bot = env

    _run(bot, _private("привет"))

    assert _sent(bot) == [vk_handlers._render_private_help()]


def test_help_lists_every_command_and_the_current_state(env):
    conn, bot = env
    repo.set_auto_reply_enabled(conn, enabled=True, changed_by=ADMIN)

    _run(bot, _private("!помощь"))

    help_text = _sent(bot)[0]
    for command in ("!вкл", "!выкл", "!модерация", "+7", "-7", "!помощь"):
        assert command in help_text
    assert "автоответ включён" in help_text
    assert "модерация выключена" in help_text


def test_report_sent_in_private_never_becomes_a_fact(env):
    """Главное свойство ветки: личка не источник данных. Тот же текст в
    беседе дал бы отчёт (см. тест ниже), здесь — только список команд."""
    conn, bot = env

    _run(bot, _private("Лукойл Вилга только 92 на табло"))

    assert conn.execute("SELECT COUNT(*) FROM fuel_report").fetchone()[0] == 0
    assert _sent(bot) == [vk_handlers._render_private_help()]


def test_the_same_text_in_the_chat_does_become_a_fact(env):
    """Контроль к предыдущему тесту: дело именно в личке, а не в тексте."""
    conn, bot = env

    _run(bot, FakeVkMessage(peer_id=CHAT, text="Лукойл Вилга только 92 на табло", cmid=7))

    assert conn.execute("SELECT COUNT(*) FROM fuel_report").fetchone()[0] == 1


def test_private_message_from_a_stranger_is_ignored_entirely(env):
    conn, bot = env
    stranger = 999001

    _run(bot, _private("!вкл", from_id=stranger, peer_id=stranger))

    assert _sent(bot) == []
    assert conn.execute("SELECT COUNT(*) FROM auto_reply_setting").fetchone()[0] == 0
    assert conn.execute("SELECT COUNT(*) FROM fuel_report").fetchone()[0] == 0


def test_stranger_in_private_is_logged_once_not_per_message(env, caplog):
    _conn, bot = env
    stranger = 999002

    with caplog.at_level("WARNING", logger="vk_bot"):
        _run(bot, _private("раз", from_id=stranger, peer_id=stranger, cmid=1))
        _run(bot, _private("два", from_id=stranger, peer_id=stranger, cmid=2))

    lines = [r.getMessage() for r in caplog.records if "не от владельца" in r.getMessage()]
    assert len(lines) == 1
    # В логе отпечаток, а не VK-id: в личке peer_id и from_id — это и есть
    # идентификатор человека (решение Р7, находки K1/F4).
    assert str(stranger) not in lines[0]


def test_own_message_in_private_is_not_parsed_as_a_command(env):
    """Иначе ответ бота на команду вернулся бы к нему же как новая команда."""
    conn, bot = env

    _run(bot, _private("!вкл", from_id=-vk_handlers.GROUP_ID))

    assert _sent(bot) == []
    assert conn.execute("SELECT COUNT(*) FROM auto_reply_setting").fetchone()[0] == 0


def test_repeated_delivery_of_the_same_command_answers_once(env):
    conn, bot = env

    _run(bot, _private("!вкл", cmid=5))
    _run(bot, _private("!вкл", cmid=5))

    assert len(_sent(bot)) == 1
    assert conn.execute("SELECT COUNT(*) FROM auto_reply_setting").fetchone()[0] == 1


def test_commands_in_a_row_are_not_debounced(env):
    """Дебаунс и потолок ответов — про беседу и живых людей; управлять ботом
    очередью из команд подряд ничто мешать не должно."""
    _conn, bot = env

    _run(bot, _private("!вкл", cmid=1))
    _run(bot, _private("!выкл", cmid=2))
    _run(bot, _private("!помощь", cmid=3))

    assert len(_sent(bot)) == 3


def test_chat_commands_still_work(env):
    """Регрессия: переезд команд в личку ничего не убрал из беседы."""
    conn, bot = env
    message = FakeVkMessage(peer_id=CHAT, text="!вкл", from_id=ADMIN, cmid=9)

    _run(bot, message)

    assert repo.get_auto_reply_enabled(conn, default=False) is True
    assert message.replies == ["Автоответ на вопросы включён."]


def test_foreign_chat_is_still_ignored(env):
    """Регрессия на решение Р4: ветка лички не должна была открыть беседы."""
    conn, bot = env
    message = FakeVkMessage(peer_id=2000000009, text="Лукойл Вилга только 92 на табло", cmid=3)

    _run(bot, message)

    assert message.replies == []
    assert conn.execute("SELECT COUNT(*) FROM fuel_report").fetchone()[0] == 0
