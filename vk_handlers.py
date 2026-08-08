import logging

from vkbottle.bot import Bot, Message

from config import GROUP_ID

log = logging.getLogger("vk_bot")


def register_handlers(bot: Bot) -> None:
    @bot.on.message()
    async def on_message(message: Message) -> None:
        if message.from_id == -GROUP_ID:
            return
        log.info(
            "peer_id=%s from_id=%s text=%r",
            message.peer_id, message.from_id, message.text,
        )
