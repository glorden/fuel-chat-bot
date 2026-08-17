"""Режим модерации: ответ ждёт подтверждения владельца в личке.

Ключевое свойство, ради которого режим и делался, — он гейтит только
исходящий текст. Факты пишутся как обычно, иначе на время теста бот
перестал бы копить базу.

Про подмену _conn и запрет monkeypatch.undo() — см. tests/test_private_commands.py.
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
    monkeypatch.setattr(vk_handlers, "_pending_drafts", {})
    monkeypatch.setattr(vk_handlers, "_draft_counter", 0)
    monkeypatch.setattr(pipeline_module, "LLM_ENABLED", False)
    return conn, FakeBot()


def _run(bot, message):
    asyncio.run(vk_handlers.handle_message(bot, message))


def _sent(bot):
    return [call["message"] for call in bot.api.sent]


def _private(text, *, cmid=50):
    return FakeVkMessage(peer_id=ADMIN, text=text, from_id=ADMIN, cmid=cmid, is_mentioned=False)


def _seed_fact(conn, bot):
    _run(bot, FakeVkMessage(peer_id=CHAT, text="Лукойл Вилга только 92 на табло", cmid=1))
    assert conn.execute("SELECT COUNT(*) FROM fuel_report").fetchone()[0] == 1


def _ask(bot, *, cmid=2, from_id=111):
    question = FakeVkMessage(peer_id=CHAT, text="Есть 92 в вилге?", from_id=from_id, cmid=cmid)
    _run(bot, question)
    return question


def _enable_moderation(conn):
    repo.set_moderation_enabled(conn, enabled=True, changed_by=ADMIN)


def test_without_moderation_the_answer_goes_straight_to_the_chat(env):
    conn, bot = env
    _seed_fact(conn, bot)

    question = _ask(bot)

    assert len(question.replies) == 1
    assert "92 - Есть" in question.replies[0]
    assert _sent(bot) == []


def test_with_moderation_nothing_reaches_the_chat_and_the_owner_gets_a_card(env):
    conn, bot = env
    _seed_fact(conn, bot)
    _enable_moderation(conn)

    question = _ask(bot)

    assert question.replies == []
    assert len(_sent(bot)) == 1
    card = _sent(bot)[0]
    assert "Черновик №1" in card
    assert "Есть 92 в вилге?" in card
    assert "92 - Есть" in card
    assert bot.api.sent[0]["peer_id"] == ADMIN


def test_approval_sends_the_draft_to_the_original_chat(env):
    conn, bot = env
    _seed_fact(conn, bot)
    _enable_moderation(conn)
    question = _ask(bot)

    _run(bot, _private("+1"))

    assert len(question.replies) == 1
    assert "92 - Есть" in question.replies[0]
    assert vk_handlers._pending_drafts == {}


def test_what_is_sent_is_exactly_what_was_approved(env):
    """Ответ не пересчитывается в момент подтверждения: иначе владелец
    одобрил бы один текст, а в беседу ушёл бы другой."""
    conn, bot = env
    _seed_fact(conn, bot)
    _enable_moderation(conn)
    question = _ask(bot)
    drafted = vk_handlers._pending_drafts[1].text

    _run(bot, _private("+1"))

    assert question.replies == [drafted]


def test_rejection_sends_nothing_anywhere(env):
    conn, bot = env
    _seed_fact(conn, bot)
    _enable_moderation(conn)
    question = _ask(bot)

    _run(bot, _private("-1"))

    assert question.replies == []
    assert vk_handlers._pending_drafts == {}
    assert "отклонён" in _sent(bot)[-1]


def test_bare_plus_approves_the_most_recent_draft(env):
    conn, bot = env
    _seed_fact(conn, bot)
    _enable_moderation(conn)
    first = _ask(bot, cmid=2, from_id=111)
    second = _ask(bot, cmid=3, from_id=222)

    _run(bot, _private("+"))

    assert second.replies != []
    assert first.replies == []


def test_a_draft_can_be_resolved_only_once(env):
    conn, bot = env
    _seed_fact(conn, bot)
    _enable_moderation(conn)
    question = _ask(bot)

    _run(bot, _private("+1", cmid=50))
    _run(bot, _private("+1", cmid=51))

    assert len(question.replies) == 1
    assert "не найден" in _sent(bot)[-1]


def test_expired_draft_is_not_sent_and_never_auto_sends(env):
    conn, bot = env
    _seed_fact(conn, bot)
    _enable_moderation(conn)
    question = _ask(bot)
    vk_handlers._pending_drafts[1].created_at -= vk_handlers._MODERATION_TTL_SECONDS + 1

    # Сама уборка ничего не отправляет — это и есть отсутствие автоотправки.
    assert vk_handlers._sweep_expired_drafts() == [1]
    assert question.replies == []

    _run(bot, _private("+1"))

    assert question.replies == []
    assert "протух" in _sent(bot)[-1]


def test_facts_are_still_recorded_while_moderation_holds_the_answers(env):
    """Главное свойство режима: придерживается публикация, не сбор."""
    conn, bot = env
    _enable_moderation(conn)

    _run(bot, FakeVkMessage(peer_id=CHAT, text="Лукойл Вилга только 92 на табло", cmid=9))

    assert conn.execute("SELECT COUNT(*) FROM fuel_report").fetchone()[0] == 1


def test_moderation_command_toggles_and_persists(env):
    conn, bot = env

    _run(bot, _private("!модерация вкл", cmid=60))
    assert repo.get_moderation_enabled(conn, default=False) is True

    _run(bot, _private("!модерация выкл", cmid=61))
    assert repo.get_moderation_enabled(conn, default=True) is False


def test_moderation_command_without_argument_reports_state(env):
    conn, bot = env
    _seed_fact(conn, bot)
    _enable_moderation(conn)
    _ask(bot)

    _run(bot, _private("!модерация", cmid=62))

    assert "Модерация включена" in _sent(bot)[-1]
    assert "Черновиков ждёт: 1" in _sent(bot)[-1]


def test_moderation_without_admin_id_answers_directly_instead_of_swallowing(env, monkeypatch):
    """Иначе включённый режим означал бы, что бот молча съедает все ответы."""
    conn, bot = env
    _seed_fact(conn, bot)
    _enable_moderation(conn)
    monkeypatch.setattr(vk_handlers, "ADMIN_ID", None)

    question = _ask(bot)

    assert len(question.replies) == 1
    assert _sent(bot) == []


def test_draft_is_not_kept_if_the_owner_never_got_the_card(env):
    conn, bot = env
    _seed_fact(conn, bot)
    _enable_moderation(conn)
    bot.api.send_error = RuntimeError("VK недоступен")

    question = _ask(bot)

    assert question.replies == []
    assert vk_handlers._pending_drafts == {}


def test_verdict_for_a_number_that_never_existed_is_reported(env):
    conn, bot = env
    _seed_fact(conn, bot)
    _enable_moderation(conn)
    _ask(bot)

    _run(bot, _private("+77"))

    assert "№77 не найден" in _sent(bot)[-1]


def test_verdict_when_there_are_no_drafts_at_all(env):
    _conn, bot = env

    _run(bot, _private("+"))

    assert "Черновиков нет" in _sent(bot)[-1]
