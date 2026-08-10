import logging
import time

from vkbottle.bot import Bot, Message

from config import AUTO_REPLY_ON_QUESTION, GROUP_ID, MIN_REPLY_GAP_SECONDS
from db import repo
from db.schema import get_connection
from pipeline.pipeline import process_message

log = logging.getLogger("vk_bot")

_conn = get_connection()
_last_reply_at: dict[int, float] = {}


def _can_reply(peer_id: int) -> bool:
    last = _last_reply_at.get(peer_id, 0.0)
    return (time.monotonic() - last) >= MIN_REPLY_GAP_SECONDS


def _quoted_context_text(message: Message) -> str:
    """Текст из fwd_messages и reply_message (один уровень, без рекурсии в
    их собственные reply/fwd) — контекст для анализа текущего сообщения."""
    parts = []
    for fwd in message.fwd_messages or []:
        t = (fwd.text or "").strip()
        if t:
            parts.append(t)
    if message.reply_message is not None:
        t = (message.reply_message.text or "").strip()
        if t:
            parts.append(t)
    return "\n".join(parts)


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
        quoted_text = _quoted_context_text(message)
        combined_text = f"{quoted_text}\n{own_text}".strip() if quoted_text else own_text

        outcome = process_message(
            _conn,
            text=combined_text,
            own_text=own_text,
            peer_id=message.peer_id,
            conversation_message_id=message.conversation_message_id,
            author_id=message.from_id,
        )
        log.info(
            "peer_id=%s from_id=%s outcome=%s text=%r",
            message.peer_id, message.from_id, outcome.label, message.text,
        )

        reply_text = outcome.reply_text if outcome.label == "question" else None

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
