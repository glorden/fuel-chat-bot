"""Надёжность обработчика и алерт владельцу (G1, D5, E4, G3-G5;
ARCH_DECISIONS.md, решения Р6 и Р8).

Здесь нужен управляемый VK: подставные `message.reply`, `users.get`,
`messages.send`. Настоящий vkbottle-овский `Message` для этого не годится
(pydantic-модель), а `handle_message` вынесен из замыкания именно затем,
чтобы его можно было звать напрямую.
"""

import asyncio
from types import SimpleNamespace

import pytest
from vkbottle.bot import Bot

import pipeline.pipeline as pipeline_module
import vk_handlers
from db.schema import get_connection
from vk_handlers import register_handlers


class _FakeUsers:
    def __init__(self, api):
        self._api = api

    async def get(self, user_ids):
        if self._api.users_get_error is not None:
            raise self._api.users_get_error
        return [SimpleNamespace(first_name="Мария")]


class _FakeMessages:
    def __init__(self, api):
        self._api = api

    async def send(self, **kwargs):
        if self._api.send_error is not None:
            raise self._api.send_error
        self._api.sent.append(kwargs)
        return 1


class _FakeApi:
    def __init__(self):
        self.users_get_error = None
        self.send_error = None
        self.sent = []
        self.users = _FakeUsers(self)
        self.messages = _FakeMessages(self)


class _FakeBot:
    def __init__(self):
        self.api = _FakeApi()


class _FakeVkMessage:
    def __init__(self, *, peer_id=2000000001, text="", from_id=111, cmid=1, is_mentioned=True):
        self.peer_id = peer_id
        self.text = text
        self.from_id = from_id
        self.conversation_message_id = cmid
        self.is_mentioned = is_mentioned
        self.fwd_messages = []
        self.reply_message = None
        self.replies = []
        self.reply_failures = 0

    async def reply(self, text):
        if self.reply_failures > 0:
            self.reply_failures -= 1
            raise RuntimeError("VK недоступен")
        self.replies.append(text)


@pytest.fixture
def env(monkeypatch):
    conn = get_connection(":memory:")
    monkeypatch.setattr(vk_handlers, "_conn", conn)
    monkeypatch.setattr(vk_handlers, "ALLOWED_PEER_IDS", frozenset({2000000001}))
    monkeypatch.setattr(vk_handlers, "_unknown_peers_logged", set())
    monkeypatch.setattr(vk_handlers, "ADMIN_ID", 3022748)
    monkeypatch.setattr(vk_handlers, "_last_alert_at", float("-inf"))
    monkeypatch.setattr(vk_handlers, "_last_reply_at", {})
    monkeypatch.setattr(pipeline_module, "LLM_ENABLED", False)
    return conn, _FakeBot()


def _handler_with_catch(bot):
    """Обёртка `on_message` (с перехватом и алертом) поверх подставного API."""
    real_bot = Bot(token="dummy-token-for-tests")
    register_handlers(real_bot)
    inner = real_bot.labeler.views()["message"].handlers[0].handler
    real_bot.api = bot.api

    async def call(message):
        return await inner(message)

    return call


def _seed_fact(conn, bot):
    asyncio.run(vk_handlers.handle_message(bot, _FakeVkMessage(text="Лукойл Вилга только 92 на табло", cmid=1)))
    assert conn.execute("SELECT COUNT(*) FROM fuel_report").fetchone()[0] == 1


def _boom(*args, **kwargs):
    raise RuntimeError("сбой разбора")


def test_users_get_failure_does_not_destroy_the_computed_answer(env):
    # E4: ответ уже посчитан из БД, а падал он на декоративном теге имени —
    # и снаружи это выглядело как обычное молчание бота.
    conn, bot = env
    _seed_fact(conn, bot)
    bot.api.users_get_error = RuntimeError("VK API error 6")

    question = _FakeVkMessage(text="Есть 92 в вилге?", cmid=2)
    asyncio.run(vk_handlers.handle_message(bot, question))

    assert len(question.replies) == 1
    assert "92 - Есть" in question.replies[0]
    assert not question.replies[0].startswith("[id")  # тега нет, ответ есть


def test_reply_is_retried_once_before_giving_up(env):
    conn, bot = env
    _seed_fact(conn, bot)

    question = _FakeVkMessage(text="Есть 92 в вилге?", cmid=2)
    question.reply_failures = 1
    asyncio.run(vk_handlers.handle_message(bot, question))
    assert len(question.replies) == 1

    question2 = _FakeVkMessage(text="Есть 92 в вилге?", cmid=3)
    question2.reply_failures = 2  # обе попытки мимо
    asyncio.run(vk_handlers.handle_message(bot, question2))
    assert question2.replies == []


def test_failed_message_is_not_marked_processed(env, monkeypatch):
    # G1: раньше отметка ставилась ДО обработки, и любой сбой означал, что
    # сообщение не обработано и никогда не будет — повторную доставку
    # отбрасывал дедуп.
    conn, bot = env
    monkeypatch.setattr(vk_handlers, "process_message", _boom)
    with pytest.raises(RuntimeError):
        asyncio.run(vk_handlers.handle_message(bot, _FakeVkMessage(text="Вилга 92 есть", cmid=7)))

    assert conn.execute("SELECT COUNT(*) FROM processed_message").fetchone()[0] == 0
    assert vk_handlers.repo.already_processed(conn, 2000000001, 7) is False


def test_partial_write_is_rolled_back_whole(env, monkeypatch):
    # D5: сообщение писалось несколькими транзакциями, и сбой на второй
    # оставлял половину факта. Теперь всё сообщение — одна транзакция.
    conn, bot = env
    monkeypatch.setattr(vk_handlers.repo, "insert_station_break", _boom)
    with pytest.raises(RuntimeError):
        asyncio.run(vk_handlers.handle_message(
            bot, _FakeVkMessage(text="Слив бензовоза на Лукойле Вилга, 92 есть", cmid=8)
        ))

    # Ни отчёта по марке (он пишется раньше перерыва), ни перерыва, ни отметки.
    assert conn.execute("SELECT COUNT(*) FROM fuel_report").fetchone()[0] == 0
    assert conn.execute("SELECT COUNT(*) FROM station_break").fetchone()[0] == 0
    assert conn.execute("SELECT COUNT(*) FROM processed_message").fetchone()[0] == 0


def test_successful_message_is_marked_processed_and_deduped(env):
    conn, bot = env
    _seed_fact(conn, bot)
    assert conn.execute("SELECT COUNT(*) FROM processed_message").fetchone()[0] == 1

    # Повторная доставка того же сообщения не пишет второй факт.
    asyncio.run(vk_handlers.handle_message(bot, _FakeVkMessage(text="Лукойл Вилга только 92 на табло", cmid=1)))
    assert conn.execute("SELECT COUNT(*) FROM fuel_report").fetchone()[0] == 1


def test_off_topic_message_is_marked_processed(env):
    conn, bot = env
    asyncio.run(vk_handlers.handle_message(bot, _FakeVkMessage(text="всем доброе утро", cmid=4)))
    assert conn.execute("SELECT COUNT(*) FROM processed_message").fetchone()[0] == 1


def test_chat_command_is_marked_processed_too(env):
    conn, bot = env
    command = _FakeVkMessage(text="!лимит", cmid=5)
    asyncio.run(vk_handlers.handle_message(bot, command))
    assert len(command.replies) == 1
    assert conn.execute("SELECT COUNT(*) FROM processed_message").fetchone()[0] == 1


def test_unhandled_error_alerts_the_owner_once_per_window(env, monkeypatch):
    # G3-G5: снаружи все отказы выглядят как обычное молчание, а молчание
    # здесь сознательная фича. Алерт — единственное, что их различает.
    conn, bot = env
    monkeypatch.setattr(vk_handlers, "process_message", _boom)
    handler = _handler_with_catch(bot)

    asyncio.run(handler(_FakeVkMessage(text="Вилга 92 есть", cmid=11)))
    assert len(bot.api.sent) == 1
    alert = bot.api.sent[0]
    assert alert["user_id"] == 3022748
    assert "2000000001" in alert["message"]
    assert "RuntimeError" in alert["message"]
    # Текста участника в алерте нет — только беседа, номер сообщения и тип.
    assert "Вилга 92 есть" not in alert["message"]

    asyncio.run(handler(_FakeVkMessage(text="Вилга 95 есть", cmid=12)))
    assert len(bot.api.sent) == 1  # окно в 10 минут ещё не прошло


def test_alert_delivery_failure_does_not_break_anything(env, monkeypatch):
    # Пока владелец не разрешил сообщения от сообщества, VK отвечает 901 —
    # это не должно превращаться во второй сбой поверх первого.
    conn, bot = env
    bot.api.send_error = RuntimeError("VKAPIError_901")
    monkeypatch.setattr(vk_handlers, "process_message", _boom)
    handler = _handler_with_catch(bot)

    asyncio.run(handler(_FakeVkMessage(text="Вилга 92 есть", cmid=13)))  # наружу не бросает
    assert bot.api.sent == []


def test_handler_catches_errors_so_one_bad_message_does_not_stop_the_bot(env, monkeypatch):
    conn, bot = env
    # Именно setattr обратно, а не monkeypatch.undo(): undo откатил бы и
    # подмену _conn из фикстуры, и тест начал бы писать в боевую bot.db.
    original_process_message = vk_handlers.process_message
    monkeypatch.setattr(vk_handlers, "process_message", _boom)
    handler = _handler_with_catch(bot)
    asyncio.run(handler(_FakeVkMessage(text="Вилга 92 есть", cmid=14)))

    # Следующее сообщение обрабатывается как ни в чём не бывало.
    monkeypatch.setattr(vk_handlers, "process_message", original_process_message)
    asyncio.run(handler(_FakeVkMessage(text="Лукойл Вилга только 92 на табло", cmid=15)))
    assert conn.execute("SELECT COUNT(*) FROM fuel_report").fetchone()[0] == 1
