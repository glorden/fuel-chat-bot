import sqlite3
from pathlib import Path

DB_PATH = Path(__file__).resolve().parent.parent / "bot.db"

_SCHEMA = """
CREATE TABLE IF NOT EXISTS processed_message (
    peer_id INTEGER NOT NULL,
    conversation_message_id INTEGER NOT NULL,
    processed_at TEXT NOT NULL,
    PRIMARY KEY (peer_id, conversation_message_id)
);

CREATE TABLE IF NOT EXISTS fuel_report (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    station_id TEXT NOT NULL,
    fuel_grade TEXT NOT NULL,
    status TEXT NOT NULL,
    -- Легаси: свободный текст про очередь от модели. Больше не пишется и
    -- НЕ читается (см. ARCH_DECISIONS.md, Р2 — через это поле проходила
    -- инъекция F1, и она оседала тут навсегда). Колонка оставлена, чтобы
    -- не переписывать историю append-only лога; воскрешать её на чтение
    -- нельзя. Актуальная очередь — в queue_* ниже.
    queue_note TEXT,
    queue_status TEXT,          -- "none" | "present" | NULL (не упоминалась)
    queue_minutes INTEGER,
    queue_cars_from INTEGER,
    queue_cars_to INTEGER,
    peer_id INTEGER NOT NULL,
    conversation_message_id INTEGER NOT NULL,
    author_id INTEGER NOT NULL,
    reported_at TEXT NOT NULL,
    raw_text TEXT NOT NULL,
    source TEXT,
    author_hash TEXT
);

CREATE TABLE IF NOT EXISTS unresolved_mention (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    peer_id INTEGER NOT NULL,
    conversation_message_id INTEGER NOT NULL,
    author_id INTEGER NOT NULL,
    seen_at TEXT NOT NULL,
    raw_text TEXT NOT NULL,
    author_hash TEXT
);

CREATE TABLE IF NOT EXISTS station_break (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    station_id TEXT NOT NULL,
    kind TEXT,
    until TEXT,
    duration_note TEXT,
    peer_id INTEGER NOT NULL,
    conversation_message_id INTEGER NOT NULL,
    author_id INTEGER NOT NULL,
    reported_at TEXT NOT NULL,
    raw_text TEXT NOT NULL,
    source TEXT,
    author_hash TEXT
);

CREATE TABLE IF NOT EXISTS fuel_limit (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    station_id TEXT NOT NULL,
    status TEXT NOT NULL,
    liters INTEGER,
    peer_id INTEGER NOT NULL,
    conversation_message_id INTEGER NOT NULL,
    author_id INTEGER NOT NULL,
    reported_at TEXT NOT NULL,
    raw_text TEXT NOT NULL,
    source TEXT,
    author_hash TEXT
);

-- Append-only, как station_break/fuel_limit: текущее значение — последняя
-- строка, без UPDATE. Управляется командами !вкл/!выкл от ADMIN_ID (см.
-- vk_handlers.py) — переживает рестарт/редеплой, в отличие от AUTO_REPLY_ON_QUESTION
-- в .env, который остаётся значением по умолчанию, пока сюда не написали ни разу.
CREATE TABLE IF NOT EXISTS auto_reply_setting (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    enabled INTEGER NOT NULL,
    changed_by INTEGER NOT NULL,
    changed_at TEXT NOT NULL
);

-- Режим модерации: ответ не уходит в беседу сразу, а ждёт подтверждения
-- владельца в личке (команда !модерация). Форма и смысл те же, что у
-- auto_reply_setting — append-only, последняя строка побеждает. Отдельная
-- таблица, а не колонка рядом: это независимый переключатель, и общая
-- строка заставляла бы писать обе настройки при смене любой из них.
--
-- Сами черновики сюда НЕ пишутся: они живут в памяти процесса и протухают
-- за MODERATION_TTL_MINUTES. Ответ старше этого срока всё равно бесполезен
-- (в нём стоит абсолютное время, а спросивший давно уехал), поэтому
-- переживать рестарт им незачем.
CREATE TABLE IF NOT EXISTS moderation_setting (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    enabled INTEGER NOT NULL,
    changed_by INTEGER NOT NULL,
    changed_at TEXT NOT NULL
);

-- Опровержение факта: владелец командой !ошибка помечает конкретную строку
-- как неверную, и чтение её больше не видит (см. pipeline/qa.py).
--
-- Отдельной таблицей, а НЕ колонкой в самих таблицах фактов и не удалением
-- строки: принцип «не переписывать, а копить» тут работает буквально —
-- ошибочный факт остаётся на месте вместе с исходным текстом, из которого
-- он извлечён, и по нему потом видно, на чём именно ошибся разбор. Живые
-- поводы: «лимит 8 л» из «на 8 часов только ДТ», «лимит 3 л» из «3 машины
-- на колонку» (см. PROGRESS.md, Этап 42).
CREATE TABLE IF NOT EXISTS fact_retraction (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    fact_table TEXT NOT NULL,
    fact_id INTEGER NOT NULL,
    station_id TEXT NOT NULL,
    retracted_by INTEGER NOT NULL,
    retracted_at TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_fact_retraction_lookup ON fact_retraction (fact_table, fact_id);
"""


# Колонки, добавленные после того, как таблица уже существовала на проде.
# "CREATE TABLE IF NOT EXISTS" их не добавит — существующую таблицу он
# просто пропускает, и бот падал бы не на старте, а на первом сообщении
# (находка D6). Полноценных миграций тут нет и не заводится: это
# минимальный догоняющий ALTER для того случая, который реально возник.
_ADDED_COLUMNS = {
    "fuel_report": {
        "queue_status": "TEXT",
        "queue_minutes": "INTEGER",
        "queue_cars_from": "INTEGER",
        "queue_cars_to": "INTEGER",
        # Чем разобрано сообщение: "llm" | "rule_based" | NULL у строк,
        # записанных до появления колонки. Нужна, чтобы задним числом было
        # видно происхождение факта (решение Р1).
        "source": "TEXT",
    },
    "station_break": {"source": "TEXT"},
    "fuel_limit": {"source": "TEXT"},
}

# Отпечаток автора вместо его VK-идентификатора (решение Р7). Добавляется
# во все четыре таблицы, где раньше бессрочно лежал числовой id реального
# человека; сам author_id у новых строк пишется нулём.
for _table in ("fuel_report", "station_break", "fuel_limit", "unresolved_mention"):
    _ADDED_COLUMNS.setdefault(_table, {})["author_hash"] = "TEXT"


def _add_missing_columns(conn: sqlite3.Connection) -> None:
    for table, columns in _ADDED_COLUMNS.items():
        existing = {row[1] for row in conn.execute(f"PRAGMA table_info({table})")}
        for name, declaration in columns.items():
            if name not in existing:
                conn.execute(f"ALTER TABLE {table} ADD COLUMN {name} {declaration}")
    conn.commit()


def get_connection(path: Path = DB_PATH) -> sqlite3.Connection:
    conn = sqlite3.connect(path)
    conn.execute("PRAGMA journal_mode=WAL")
    conn.executescript(_SCHEMA)
    _add_missing_columns(conn)
    return conn
