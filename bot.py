import logging
import sys

from vkbottle.bot import Bot

from config import GROUP_ID, VK_TOKEN
from vk_handlers import register_handlers

sys.stdout.reconfigure(encoding="utf-8")
logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
log = logging.getLogger("vk_bot")

bot = Bot(token=VK_TOKEN)
# По умолчанию vkbottle не парсит markup-упоминания (ABCMessageView.replace_mention
# = False), из-за чего message.is_mentioned/message.mention всегда молчат — без
# этого явно включённого флага бот не видит, что его тегнули.
bot.labeler.message_view.replace_mention = True
register_handlers(bot)

if __name__ == "__main__":
    log.info("Starting Long Poll, group_id=%s", GROUP_ID)
    bot.run_forever()
