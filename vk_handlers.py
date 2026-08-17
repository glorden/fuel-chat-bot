import logging
import random
import time
from datetime import datetime, timedelta, timezone

from vkbottle.bot import Bot, Message

from config import ADMIN_ID, ALLOWED_PEER_IDS, AUTO_REPLY_ON_QUESTION, GROUP_ID, MIN_REPLY_GAP_SECONDS
from db import repo
from db.retention import apply_retention
from db.schema import get_connection
from pipeline.pipeline import process_message
from pipeline.prefilter import is_on_topic
from pipeline.resolve_station import get_displayed_brand_fuel_limits
from privacy import author_fingerprint
from templates import render_brand_limits_list

log = logging.getLogger("vk_bot")

_conn = get_connection()

# Дебаунс — на пару «беседа + автор», а не на беседу целиком. Раньше окно
# было общим на весь чат, и при всплеске бот отвечал одному из пяти
# спросивших: замер показал 1 ответ из 5 на rule-based и 2 из 5 на Mistral
# (находка H2) — ровно тогда, когда чат активен и бот полезнее всего.
# Заодно снимается F3: публичная команда !лимит больше не съедает окно
# чужих содержательных ответов.
_last_reply_at: dict[tuple[int, int], float] = {}

# Потолок на беседу поверх авторского дебаунса — страховка от
# патологического всплеска, а не от обычной активности: при 316 сообщениях
# в сутки десять ответов за минуту это уже аномалия. Срабатывание пишется в
# лог, чтобы не быть ещё одним видом молчания (G3).
_CHAT_REPLY_CAP = 10
_CHAT_REPLY_WINDOW_SECONDS = 60
_recent_chat_replies: dict[int, list[float]] = {}

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

# У беседы peer_id всегда 2000000000 + номер, у личного сообщения он равен
# VK-id отправителя. Проверено живьём на Long Poll, а не выведено из
# документации: личное сообщение сообществу приходит обычным message_new.
_CHAT_PEER_BASE = 2000000000

# Отпечатки тех, кто писал в личку не будучи владельцем — тот же приём, что
# и _unknown_peers_logged: одна строка на человека, а не на сообщение.
_foreign_private_logged: set[str] = set()

# Алерты владельцу: не чаще одного в 10 минут, чтобы повторяющийся сбой не
# превратился в поток сообщений в личку.
_RETENTION_INTERVAL_SECONDS = 3600
_last_retention_at = float("-inf")

_ALERT_MIN_GAP_SECONDS = 600
# -inf, а не 0: time.monotonic() на Linux — это аптайм, и с нуля первые
# 10 минут после перезагрузки хоста алерты бы молча глушились.
_last_alert_at = float("-inf")


def _is_private_peer(peer_id: int) -> bool:
    """Личное сообщение сообществу, а не сообщение из беседы."""
    return peer_id < _CHAT_PEER_BASE


def _is_allowed_peer(peer_id: int) -> bool:
    """Обслуживаем только беседы из ALLOWED_PEER_IDS (решение Р4). Первое
    сообщение из чужой беседы попадает в лог один раз: владелец узнаёт, что
    сообщество куда-то добавили, а дальше тишина.

    Сюда доходят только беседы: личку разбирает отдельная ветка в
    handle_message, и это важно для лога — у личного сообщения peer_id
    равен VK-id человека, а у беседы это безобидный 2000000001."""
    if peer_id in ALLOWED_PEER_IDS:
        return True
    if peer_id not in _unknown_peers_logged:
        _unknown_peers_logged.add(peer_id)
        log.warning("Сообщение из беседы вне allowlist, игнорирую её целиком. peer_id=%s", peer_id)
    return False


def _log_foreign_private_message(author_id: int) -> None:
    """Личка не от владельца: игнорируем молча, но один раз отмечаем в логе.

    Без peer_id и без from_id — в личке это одно и то же число, VK-id
    человека, а идентификаторам в логе не место (решение Р7, находки K1/F4).
    Отпечаток отличает одного писавшего от другого, никого не называя."""
    fingerprint = author_fingerprint(author_id)[:8]
    if fingerprint in _foreign_private_logged:
        return
    _foreign_private_logged.add(fingerprint)
    log.warning("Личное сообщение не от владельца, игнорирую. автор=%s", fingerprint)


def _can_reply(peer_id: int, author_id: int) -> bool:
    """Можно ли отвечать этому автору в этой беседе прямо сейчас."""
    now = time.monotonic()
    last = _last_reply_at.get((peer_id, author_id), float("-inf"))
    if (now - last) < MIN_REPLY_GAP_SECONDS:
        return False

    recent = [t for t in _recent_chat_replies.get(peer_id, []) if now - t < _CHAT_REPLY_WINDOW_SECONDS]
    _recent_chat_replies[peer_id] = recent
    if len(recent) >= _CHAT_REPLY_CAP:
        log.warning(
            "Потолок ответов на беседу исчерпан (%s за %s с), молчу. peer_id=%s",
            _CHAT_REPLY_CAP, _CHAT_REPLY_WINDOW_SECONDS, peer_id,
        )
        return False
    return True


def _note_reply(peer_id: int, author_id: int) -> None:
    now = time.monotonic()
    _last_reply_at[(peer_id, author_id)] = now
    _recent_chat_replies.setdefault(peer_id, []).append(now)


def _recent_author_message(key: tuple[int, int]) -> str | None:
    entry = _last_author_message.get(key)
    if entry is None:
        return None
    text, ts = entry
    return text if time.monotonic() - ts <= _AUTHOR_CONTEXT_TTL_SECONDS else None


# Раньше этой даты сообщения из живого чата быть не может — так отсекается
# в первую очередь эпоха-ноль (vkbottle отдаёт её как 1970-01-01, если даты
# в событии не было).
_EARLIEST_PLAUSIBLE_MESSAGE_TIME = datetime(2020, 1, 1, tzinfo=timezone.utc)


def _message_time(message: Message) -> datetime:
    """Время самого сообщения, а не момента записи в БД (находка H6). При
    очереди из нескольких сообщений разница уже не косметическая: именно
    это время решает, какой отчёт свежее и не устарел ли факт.

    `Message.date` у vkbottle — уже `datetime` с UTC-таймзоной (проверено),
    но naive-значение всё равно приводится к UTC: одна naive-строка в БД
    навсегда ломает ответы по станции (находка D3).

    Негодная дата (её нет, эпоха-ноль, будущее) — повод вернуться ко
    времени обработки: лучше сместить факт на секунды, чем записать отчёт
    «из будущего», который никогда не устареет."""
    now = datetime.now(timezone.utc)
    raw = getattr(message, "date", None)
    if raw is None:
        return now

    if isinstance(raw, (int, float)):
        try:
            sent_at = datetime.fromtimestamp(raw, tz=timezone.utc)
        except (OverflowError, OSError, ValueError):
            log.warning("Негодная дата сообщения (%r), беру время обработки", raw)
            return now
    elif isinstance(raw, datetime):
        sent_at = raw if raw.tzinfo is not None else raw.replace(tzinfo=timezone.utc)
    else:
        log.warning("Неожиданный тип даты сообщения (%s), беру время обработки", type(raw).__name__)
        return now

    if sent_at < _EARLIEST_PLAUSIBLE_MESSAGE_TIME or sent_at > now + timedelta(minutes=5):
        log.warning("Неправдоподобная дата сообщения (%s), беру время обработки", sent_at.isoformat())
        return now
    return sent_at


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


_PRIVATE_HELP = (
    "Команды:\n"
    "!вкл — включить автоответ на вопросы\n"
    "!выкл — выключить автоответ\n"
    "!помощь — этот список"
)


def _parse_private_command(text: str) -> tuple[str, str] | None:
    """Разбор команды из лички: "!имя" плюс необязательный аргумент.
    None — если это вообще не похоже на команду.

    В беседе команды принимаются только целой строкой без хвостов
    (_parse_admin_command, _is_limit_list_command): там любое сообщение
    может оказаться обычной репликой по теме, и строгость — защита от
    ложного срабатывания. В личке этой опасности нет, единственный
    отправитель тут владелец и пишет он боту. Поэтому разбор свободнее и
    допускает аргумент — он понадобится командам вроде "!ошибка <станция>"."""
    normalized = text.strip()
    if not normalized.startswith("!"):
        return None
    name, _, argument = normalized.partition(" ")
    return name.lower(), argument.strip()


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


async def _send_private(bot: Bot, peer_id: int, text: str) -> bool:
    """Ответ в личку — через messages.send, а не message.reply: этот путь уже
    проверен живьём на алертах владельцу (решение Р8) и не зависит от того,
    как VK трактует «ответ на сообщение» в личной переписке.

    В лог не попадает peer_id: в личке это VK-id человека (см.
    _log_foreign_private_message)."""
    for attempt in (1, 2):
        try:
            await bot.api.messages.send(
                peer_id=peer_id,
                random_id=random.randint(1, 2**31 - 1),
                message=text,
            )
            return True
        except Exception:
            log.warning("Не удалось ответить в личку (попытка %s из 2)", attempt, exc_info=True)
    return False


async def _handle_private_command(bot: Bot, message: Message) -> None:
    """Команды владельца в личке. Ни один путь отсюда не пишет факты и не
    читает базу станций — это канал управления, а не третий источник данных
    (см. handle_message)."""
    parsed = _parse_private_command(message.text or "")
    if parsed is None:
        await _send_private(bot, message.peer_id, _PRIVATE_HELP)
        return

    name, _argument = parsed
    if name in ("!вкл", "!выкл"):
        enabled = name == "!вкл"
        repo.set_auto_reply_enabled(_conn, enabled=enabled, changed_by=message.from_id)
        state = "включён" if enabled else "выключен"
        await _send_private(bot, message.peer_id, f"Автоответ на вопросы {state}.")
        return

    if name in ("!помощь", "!команды"):
        await _send_private(bot, message.peer_id, _PRIVATE_HELP)
        return

    await _send_private(bot, message.peer_id, f"Не знаю команду «{name}».\n\n{_PRIVATE_HELP}")


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


def run_retention_if_due(*, force: bool = False) -> None:
    """Ретенция (решение Р7) без отдельного планировщика: раз в час на живом
    трафике плюс один прогон на старте. Своего шедулера в проекте нет, а
    заводить его ради одной уборки — лишняя подсистема; если трафика нет,
    то и новых данных, которым нужно истекать, тоже нет.

    Уборка не должна ронять обработку сообщения: её сбой — строка в логе."""
    global _last_retention_at
    if not force and time.monotonic() - _last_retention_at < _RETENTION_INTERVAL_SECONDS:
        return
    _last_retention_at = time.monotonic()
    try:
        apply_retention(_conn)
    except Exception:
        log.exception("Ретенция не отработала")


def _mark_processed(peer_id: int, conversation_message_id: int) -> None:
    """Отметка для путей, которые не доходят до пайплайна (команды в чате):
    там её некому поставить, а дедуп нужен и им."""
    with _conn:
        repo.mark_processed(_conn, peer_id, conversation_message_id)


async def handle_message(bot: Bot, message: Message) -> None:
    """Тело обработчика. Вынесено из замыкания, чтобы вокруг него можно
    было поставить один перехват (см. register_handlers) и чтобы его можно
    было вызывать в тестах напрямую."""
    # Собственные сообщения бота не разбираем ни в беседе, ни в личке —
    # иначе ответ на команду вернулся бы к нему же как новая команда.
    if message.from_id == -GROUP_ID:
        return

    # Личка владельца — канал управления, и ветка стоит до гейта по
    # allowlist сознательно. Решение Р4 этим не размывается: гейт охраняет
    # БЕСЕДЫ, из которых берутся и в которые уходят факты, а здесь не
    # пишется и не читается ни одного факта — только команды. Чужая личка
    # не получает ответа вообще, даже сообщения о том, что она чужая.
    if _is_private_peer(message.peer_id):
        if ADMIN_ID is None or message.peer_id != ADMIN_ID:
            _log_foreign_private_message(message.from_id)
            return
        if repo.already_processed(_conn, message.peer_id, message.conversation_message_id):
            return
        await _handle_private_command(bot, message)
        _mark_processed(message.peer_id, message.conversation_message_id)
        return

    # Гейт по беседе — до дедупа и до разбора команд: из чужой беседы не
    # читаем, в неё не отвечаем и ничего от неё не пишем в общую базу.
    if not _is_allowed_peer(message.peer_id):
        return
    if repo.already_processed(_conn, message.peer_id, message.conversation_message_id):
        return

    run_retention_if_due()

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
        if _can_reply(message.peer_id, message.from_id):
            _note_reply(message.peer_id, message.from_id)
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
    # Вызов LLM внутри уходит в отдельный поток, так что цикл событий тут
    # свободен и соседние сообщения не стоят в очереди за этим.
    outcome = await process_message(
        _conn,
        text=combined_text,
        own_text=own_text,
        quoted_context=quoted_text or None,
        previous_message=previous_message,
        peer_id=message.peer_id,
        conversation_message_id=message.conversation_message_id,
        author_id=message.from_id,
        reported_at=_message_time(message),
    )
    # Ни текста сообщения, ни VK-идентификатора: раньше эта строка писала
    # на КАЖДОЕ сообщение полный текст, процитированный текст и from_id —
    # на уровне INFO, то есть в обычном режиме (находка F4). Для разбора
    # инцидентов хватает исхода, длины и того, был ли контекст; отличить
    # одного автора от другого позволяет отпечаток.
    log.info(
        "peer_id=%s author=%s outcome=%s len=%s quoted=%s prev=%s",
        message.peer_id, author_fingerprint(message.from_id)[:8], outcome.label,
        len(own_text), bool(quoted_text), bool(previous_message),
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
    if not _can_reply(message.peer_id, message.from_id):
        return

    _note_reply(message.peer_id, message.from_id)
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
