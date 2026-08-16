import logging
import random
import time

from vkbottle.bot import Bot, Message

from config import ADMIN_ID, ALLOWED_PEER_IDS, AUTO_REPLY_ON_QUESTION, GROUP_ID, MIN_REPLY_GAP_SECONDS
from db import repo
from db.schema import get_connection
from pipeline.pipeline import process_message
from pipeline.prefilter import is_on_topic
from pipeline.resolve_station import get_displayed_brand_fuel_limits
from templates import render_brand_limits_list

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

# Беседы вне allowlist, о которых уже писали в лог — чтобы чужой чат не мог
# залить лог одной строкой на каждое сообщение.
_unknown_peers_logged: set[int] = set()

# Алерты владельцу: не чаще одного в 10 минут, чтобы повторяющийся сбой не
# превратился в поток сообщений в личку.
_ALERT_MIN_GAP_SECONDS = 600
# -inf, а не 0: time.monotonic() на Linux — это аптайм, и с нуля первые
# 10 минут после перезагрузки хоста алерты бы молча глушились.
_last_alert_at = float("-inf")


def _is_allowed_peer(peer_id: int) -> bool:
    """Обслуживаем только беседы из ALLOWED_PEER_IDS (решение Р4). Первое
    сообщение из чужой беседы попадает в лог один раз: владелец узнаёт, что
    сообщество куда-то добавили, а дальше тишина."""
    if peer_id in ALLOWED_PEER_IDS:
        return True
    if peer_id not in _unknown_peers_logged:
        _unknown_peers_logged.add(peer_id)
        log.warning("Сообщение из беседы вне allowlist, игнорирую её целиком. peer_id=%s", peer_id)
    return False


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


def _parse_admin_command(text: str) -> bool | None:
    """True/False для "!вкл"/"!выкл" (переключатель AUTO_REPLY_ON_QUESTION),
    None — если это не команда. Только текст целиком, без хвостов — команда,
    а не обычная реплика по теме."""
    normalized = text.strip().lower()
    if normalized == "!вкл":
        return True
    if normalized == "!выкл":
        return False
    return None


def _is_limit_list_command(text: str) -> bool:
    """"!лимит"/"!лимиты" — доступна всем в чате (в отличие от !вкл/!выкл),
    голая команда целиком, без хвостов."""
    return text.strip().lower() in ("!лимит", "!лимиты")


async def _mention_tag(bot: Bot, user_id: int) -> str:
    """Тег вида "[id...|Имя], " для явного упоминания адресата в ответе.
    Пустая строка, если user_id — не пользователь (например, сообщение
    пришло от сообщества) или если VK не отдал имя.

    Отказ здесь не должен стоить готового ответа: это украшение, а ответ уже
    вычислен из БД (находка E4 — раньше сбой users.get уничтожал его целиком,
    и снаружи это выглядело как обычное молчание бота)."""
    if user_id <= 0:
        return ""
    try:
        users = await bot.api.users.get(user_ids=[user_id])
    except Exception:
        log.warning("users.get не ответил, отвечаю без тега. user_id=%s", user_id, exc_info=True)
        return ""
    if not users:
        return ""
    return f"[id{user_id}|{users[0].first_name}], "


async def _reply_with_retry(message: Message, text: str) -> bool:
    """Одна повторная попытка отправки. Ответ уже вычислен, и второй
    попытки не будет никогда: VK не переспрашивает сообщения (ts
    продвигается до обработки), а своей очереди повторов у нас нет —
    осознанное at-most-once, см. ARCH_DECISIONS.md, Р6."""
    for attempt in (1, 2):
        try:
            await message.reply(text)
            return True
        except Exception:
            log.warning(
                "Не удалось отправить ответ (попытка %s из 2). peer_id=%s cmid=%s",
                attempt, message.peer_id, message.conversation_message_id, exc_info=True,
            )
    return False


async def _alert_owner(bot: Bot, summary: str) -> None:
    """Сообщение владельцу в личку о необработанном сбое — не чаще раза в
    _ALERT_MIN_GAP_SECONDS (решение Р8). Снаружи все отказы выглядят
    одинаково, как обычное молчание бота, а молчание тут сознательная фича
    (находки G3-G5); это единственный способ отличить одно от другого.

    Текст сообщения участника в алерт НЕ попадает — только беседа, номер
    сообщения и тип ошибки (см. Р7, гигиена логов).

    Важно: если алерт отправить не удалось, это не должно ничего сломать —
    сбой доставки алерта остаётся только в логе."""
    global _last_alert_at
    if ADMIN_ID is None:
        return
    if time.monotonic() - _last_alert_at < _ALERT_MIN_GAP_SECONDS:
        return
    _last_alert_at = time.monotonic()
    try:
        await bot.api.messages.send(
            user_id=ADMIN_ID,
            random_id=random.randint(1, 2**31 - 1),
            message=f"Бот: необработанная ошибка.\n{summary}",
        )
    except Exception:
        # VKAPIError_901 ("can't send messages for users without permission")
        # означает, что владелец не разрешил сообщения от сообщества —
        # одноразовое действие с его стороны, см. DEPLOY.md.
        log.error("Не удалось отправить алерт владельцу", exc_info=True)


def _mark_processed(peer_id: int, conversation_message_id: int) -> None:
    """Отметка для путей, которые не доходят до пайплайна (команды в чате):
    там её некому поставить, а дедуп нужен и им."""
    with _conn:
        repo.mark_processed(_conn, peer_id, conversation_message_id)


async def handle_message(bot: Bot, message: Message) -> None:
    """Тело обработчика. Вынесено из замыкания, чтобы вокруг него можно
    было поставить один перехват (см. register_handlers) и чтобы его можно
    было вызывать в тестах напрямую."""
    # Гейт по беседе — первым делом, до дедупа и до разбора команд:
    # из чужой беседы не читаем, в неё не отвечаем и ничего от неё не
    # пишем в общую базу.
    if not _is_allowed_peer(message.peer_id):
        return
    if message.from_id == -GROUP_ID:
        return
    if repo.already_processed(_conn, message.peer_id, message.conversation_message_id):
        return

    # Отметка "обработано" ставится ПОСЛЕ обработки, а не до (находка G1:
    # раньше любое исключение означало, что сообщение и не обработано, и
    # больше никогда не будет — дедуп отбрасывал повторную доставку). Для
    # пути с фактами она лежит в той же транзакции, что и сами факты
    # (pipeline.py), для команд — ставится тут же после ответа.
    own_text = (message.text or "").strip()

    admin_command = _parse_admin_command(own_text)
    if admin_command is not None and ADMIN_ID is not None and message.from_id == ADMIN_ID:
        repo.set_auto_reply_enabled(_conn, enabled=admin_command, changed_by=message.from_id)
        state = "включён" if admin_command else "выключен"
        await _reply_with_retry(message, f"Автоответ на вопросы {state}.")
        _mark_processed(message.peer_id, message.conversation_message_id)
        return

    if _is_limit_list_command(own_text):
        if _can_reply(message.peer_id):
            _last_reply_at[message.peer_id] = time.monotonic()
            await _reply_with_retry(message, render_brand_limits_list(get_displayed_brand_fuel_limits()))
        _mark_processed(message.peer_id, message.conversation_message_id)
        return

    quoted_text = _quoted_context_text(message)
    combined_text = f"{quoted_text}\n{own_text}".strip() if quoted_text else own_text

    author_key = (message.peer_id, message.from_id)
    previous_message = _recent_author_message(author_key)
    if own_text and is_on_topic(own_text):
        _last_author_message[author_key] = (own_text, time.monotonic())

    # Пишет факты и ставит отметку "обработано" одной транзакцией.
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
    # Отвечаем, только если бота явно упомянули, или включён автоответ —
    # плюс дебаунс на чат как страховка от флуда. Живое значение из БД
    # (переключается !вкл/!выкл, см. выше), AUTO_REPLY_ON_QUESTION из
    # .env — дефолт, пока переключателем ни разу не пользовались.
    if not (message.is_mentioned or repo.get_auto_reply_enabled(_conn, default=AUTO_REPLY_ON_QUESTION)):
        return
    if not _can_reply(message.peer_id):
        return

    _last_reply_at[message.peer_id] = time.monotonic()
    tag = await _mention_tag(bot, message.from_id)
    await _reply_with_retry(message, f"{tag}{reply_text}")


def register_handlers(bot: Bot) -> None:
    @bot.on.message()
    async def on_message(message: Message) -> None:
        # Единственный перехват на всю обработку. Без него любое исключение
        # съедалось представлением vkbottle и оставалось только строкой в
        # шумном DEBUG-логе, который никто не читает автоматически
        # (находки G3-G5): снаружи сбой неотличим от штатного молчания.
        try:
            await handle_message(bot, message)
        except Exception as exc:
            log.exception(
                "Необработанная ошибка обработки сообщения. peer_id=%s cmid=%s",
                message.peer_id, message.conversation_message_id,
            )
            await _alert_owner(
                bot,
                f"Беседа {message.peer_id}, сообщение {message.conversation_message_id}, "
                f"тип ошибки: {type(exc).__name__}. Подробности — в логе контейнера.",
            )
