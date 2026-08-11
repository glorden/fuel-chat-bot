import logging
import time

from vkbottle.bot import Bot, Message

from config import AUTO_REPLY_ON_QUESTION, GROUP_ID, MIN_REPLY_GAP_SECONDS
from db import repo
from db.schema import get_connection
from pipeline.pipeline import process_message
from pipeline.prefilter import is_on_topic

log = logging.getLogger("vk_bot")

_conn = get_connection()
_last_reply_at: dict[int, float] = {}

# Предыдущее сообщение автора в этом же чате — только для LLM-контекста
# (см. pipeline/pipeline.py::_analyze), rule-based его не получает. Ключ —
# (peer_id, author_id): не объединяется между двумя подключёнными беседами,
# в отличие от фактов — разговорная связность внутри одного треда и общая
# база знаний по станциям — разные вещи. TTL не откалиброван (в отличие от
# FRESH_MINUTES/STALE_MINUTES) — оценка, можно поправить после живого теста.
_last_author_message: dict[tuple[int, int], tuple[str, float]] = {}
_AUTHOR_CONTEXT_TTL_SECONDS = 120


def _can_reply(peer_id: int) -> bool:
    last = _last_reply_at.get(peer_id, 0.0)
    return (time.monotonic() - last) >= MIN_REPLY_GAP_SECONDS


def _recent_author_message(key: tuple[int, int]) -> str | None:
    entry = _last_author_message.get(key)
    if entry is None:
        return None
    text, ts = entry
    return text if time.monotonic() - ts <= _AUTHOR_CONTEXT_TTL_SECONDS else None


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

        author_key = (message.peer_id, message.from_id)
        previous_message = _recent_author_message(author_key)
        if own_text and is_on_topic(own_text):
            _last_author_message[author_key] = (own_text, time.monotonic())

        outcome = process_message(
            _conn,
            text=combined_text,
            own_text=own_text,
            quoted_context=quoted_text or None,
            previous_message=previous_message,
            peer_id=message.peer_id,
            conversation_message_id=message.conversation_message_id,
            author_id=message.from_id,
        )
        log.info(
            "peer_id=%s from_id=%s outcome=%s own_text=%r quoted=%r prev_msg=%r",
            message.peer_id, message.from_id, outcome.label, own_text, quoted_text or None, previous_message,
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
