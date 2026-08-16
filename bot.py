import logging
import sys

from loguru import logger
from vkbottle.bot import Bot

from config import GROUP_ID, VK_TOKEN
from vk_handlers import register_handlers, run_retention_if_due

sys.stdout.reconfigure(encoding="utf-8")
sys.stderr.reconfigure(encoding="utf-8")
logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
log = logging.getLogger("vk_bot")

# vkbottle добавляет свой приёмник loguru БЕЗ указания уровня, а по умолчанию
# у loguru это DEBUG — в поток попадало каждое сырое VK-событие целиком,
# вместе с from_id и текстом сообщения (находки F4/G3: именно так в bot.log
# оказывались строки вида 'from_id': ..., 'text': ...). Плюс colorize=True
# вставлял ANSI-коды, из-за которых лог плохо грепается. Оставляем от
# vkbottle только предупреждения и ошибки; наш собственный INFO-поток идёт
# через стандартный logging и никакого текста участников не пишет.
logger.remove()
logger.add(sys.stderr, level="WARNING", colorize=False)

bot = Bot(token=VK_TOKEN)
# По умолчанию vkbottle не парсит markup-упоминания (ABCMessageView.replace_mention
# = False), из-за чего message.is_mentioned/message.mention всегда молчат — без
# этого явно включённого флага бот не видит, что его тегнули.
bot.labeler.message_view.replace_mention = True
register_handlers(bot)

if __name__ == "__main__":
    run_retention_if_due(force=True)
    log.info("Starting Long Poll, group_id=%s", GROUP_ID)
    bot.run_forever()
