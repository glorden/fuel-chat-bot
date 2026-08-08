import logging

from vkbottle.bot import Bot, Message

from config import GROUP_ID
from db import repo
from db.schema import get_connection
from pipeline.pipeline import process_message

log = logging.getLogger("vk_bot")

_conn = get_connection()


def register_handlers(bot: Bot) -> None:
    @bot.on.message()
    async def on_message(message: Message) -> None:
        if message.from_id == -GROUP_ID:
            return
        if repo.already_processed(_conn, message.peer_id, message.conversation_message_id):
            return
        repo.mark_processed(_conn, message.peer_id, message.conversation_message_id)

        outcome = process_message(
            _conn,
            text=message.text or "",
            peer_id=message.peer_id,
            conversation_message_id=message.conversation_message_id,
            author_id=message.from_id,
        )
        log.info(
            "peer_id=%s from_id=%s outcome=%s text=%r",
            message.peer_id, message.from_id, outcome, message.text,
        )
