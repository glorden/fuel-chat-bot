import logging
import time

from vkbottle.bot import Bot, Message

from config import AUTO_REPLY_ON_QUESTION, GROUP_ID, MIN_REPLY_GAP_SECONDS
from db import repo
from db.schema import get_connection
from pipeline.extract import extract
from pipeline.pipeline import process_message
from pipeline.qa import answer_question
from pipeline.resolve_station import resolve_station

log = logging.getLogger("vk_bot")

_conn = get_connection()
_last_reply_at: dict[int, float] = {}


def _can_reply(peer_id: int) -> bool:
    last = _last_reply_at.get(peer_id, 0.0)
    return (time.monotonic() - last) >= MIN_REPLY_GAP_SECONDS


def _answer_for_parent_message(message: Message) -> str | None:
    """Тегнули бота реплаем на чужой вопрос, без своего текста — частый способ
    адресовать существующий вопрос боту. В БД это не пишем (не новый отчёт,
    просто ответ на то, что уже сказал другой человек), только отвечаем."""
    if message.reply_message is None:
        return None
    parent_text = (message.reply_message.text or "").strip()
    parent_result = extract(parent_text)
    if parent_result.message_type != "question":
        return None
    station_id = resolve_station(parent_text)
    return answer_question(_conn, station_id=station_id, grades=parent_result.question_grades)


async def _mention_tag(bot: Bot, user_id: int) -> str:
    """Тег вида "[id...|Имя], " для явного упоминания адресата в ответе.
    Пустая строка, если user_id — не пользователь (например, сообщение
    пришло от сообщества)."""
    if user_id <= 0:
        return ""
    users = await bot.api.users.get(user_ids=[user_id])
    if not users:
        return ""
    return f"[id{user_id}|{users[0].first_name}], "


def register_handlers(bot: Bot) -> None:
    @bot.on.message()
    async def on_message(message: Message) -> None:
        if message.from_id == -GROUP_ID:
            return
        if repo.already_processed(_conn, message.peer_id, message.conversation_message_id):
            return
        repo.mark_processed(_conn, message.peer_id, message.conversation_message_id)

        own_text = (message.text or "").strip()
        outcome = process_message(
            _conn,
            text=own_text,
            peer_id=message.peer_id,
            conversation_message_id=message.conversation_message_id,
            author_id=message.from_id,
        )
        log.info(
            "peer_id=%s from_id=%s outcome=%s text=%r",
            message.peer_id, message.from_id, outcome.label, message.text,
        )

        reply_text = outcome.reply_text if outcome.label == "question" else None
        if reply_text is None and not own_text and message.is_mentioned:
            reply_text = _answer_for_parent_message(message)

        if not reply_text:
            return
        # Отвечаем, только если бота явно упомянули, или включён автоответ
        # (AUTO_REPLY_ON_QUESTION) — плюс дебаунс на чат как страховка от флуда.
        if not (message.is_mentioned or AUTO_REPLY_ON_QUESTION):
            return
        if not _can_reply(message.peer_id):
            return

        _last_reply_at[message.peer_id] = time.monotonic()
        tag = await _mention_tag(bot, message.from_id)
        await message.reply(f"{tag}{reply_text}")
