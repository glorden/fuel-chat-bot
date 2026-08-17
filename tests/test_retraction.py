"""Команда !ошибка — опровержение неверного факта.

Повод не гипотетический: разбор записал «лимит 8 л» из «на 8 часов только
ДТ» и «лимит 3 л» из «3 машины на колонку» (PROGRESS.md, Этап 42). Такой
факт живёт в append-only логе и повторяется в каждом будущем ответе, а
убрать его было нечем, кроме ручного SQL на проде.

Опровержение само append-only: строки фактов не меняются и не удаляются,
рядом ложится запись в fact_retraction, а чтение её учитывает.

Про подмену _conn и запрет monkeypatch.undo() — см. test_private_commands.py.
"""

import asyncio
from datetime import datetime, timedelta, timezone

import pytest

import pipeline.pipeline as pipeline_module
import vk_handlers
from db import repo
from db.schema import get_connection
from pipeline.extract import BreakInfo, LimitInfo, ReportItem
from pipeline.qa import answer_question
from tests.vk_fakes import FakeBot, FakeVkMessage

ADMIN = 3022748
STATION = "lukoil_vilga"


@pytest.fixture
def env(monkeypatch):
    conn = get_connection(":memory:")
    monkeypatch.setattr(vk_handlers, "_conn", conn)
    monkeypatch.setattr(vk_handlers, "ALLOWED_PEER_IDS", frozenset({2000000001}))
    monkeypatch.setattr(vk_handlers, "ADMIN_ID", ADMIN)
    monkeypatch.setattr(vk_handlers, "_foreign_private_logged", set())
    monkeypatch.setattr(vk_handlers, "_pending_drafts", {})
    monkeypatch.setattr(pipeline_module, "LLM_ENABLED", False)
    return conn, FakeBot()


def _common(**kw):
    return dict(peer_id=1, conversation_message_id=1, author_hash="x", raw_text="t", source="llm", **kw)


def _report(conn, grade, status, minutes_ago, station_id=STATION):
    repo.insert_fuel_report(
        conn, station_id=station_id, report=ReportItem(grade=grade, status=status), queue=None,
        reported_at=datetime.now(timezone.utc) - timedelta(minutes=minutes_ago), **_common(),
    )
    conn.commit()


def _limit(conn, status, liters, minutes_ago):
    repo.insert_fuel_limit(
        conn, station_id=STATION, limit_info=LimitInfo(status=status, liters=liters),
        reported_at=datetime.now(timezone.utc) - timedelta(minutes=minutes_ago), **_common(),
    )
    conn.commit()


def _break(conn, kind, minutes_ago):
    repo.insert_station_break(
        conn, station_id=STATION, break_info=BreakInfo(kind=kind, until=None, duration_note=None),
        reported_at=datetime.now(timezone.utc) - timedelta(minutes=minutes_ago), **_common(),
    )
    conn.commit()


_next_cmid = iter(range(1, 10_000))


def _command(bot, text):
    # Каждой команде свой conversation_message_id: одинаковый отбрасывается
    # дедупом, и вторая команда подряд просто не доедет до обработчика.
    message = FakeVkMessage(
        peer_id=ADMIN, text=text, from_id=ADMIN, cmid=next(_next_cmid), is_mentioned=False
    )
    asyncio.run(vk_handlers.handle_message(bot, message))
    return bot.api.sent[-1]["message"]


def test_retracting_a_limit_removes_it_from_the_answer(env):
    conn, bot = env
    _report(conn, "92", "available", 5)
    _limit(conn, "limited", 8, 5)
    assert "лимит 8 л" in answer_question(conn, station_id=STATION, grades=[], question_text="лимит?")

    reply = _command(bot, "!ошибка лукойл вилга лимит")

    assert "убрал лимит" in reply
    # Лимит ушёл, а отчёт по 92 остался — убирали именно названное.
    text = answer_question(conn, station_id=STATION, grades=[], question_text="лимит?")
    assert "лимит" not in text
    assert "92 - Есть" in text


def test_the_wrong_row_stays_in_the_table_it_is_only_hidden(env):
    """Append-only: сам факт вместе с исходным текстом остаётся — по нему
    потом видно, на чём ошибся разбор."""
    conn, bot = env
    _limit(conn, "limited", 8, 5)

    _command(bot, "!ошибка лукойл вилга лимит")

    assert conn.execute("SELECT COUNT(*) FROM fuel_limit").fetchone()[0] == 1
    assert conn.execute("SELECT COUNT(*) FROM fact_retraction").fetchone()[0] == 1


def test_retraction_peels_one_layer_and_reveals_the_previous_record(env):
    conn, bot = env
    _limit(conn, "unlimited", None, 60)
    _limit(conn, "limited", 8, 5)

    _command(bot, "!ошибка лукойл вилга лимит")

    text = answer_question(conn, station_id=STATION, grades=[], question_text="лимит?")
    assert "лимита нет" in text

    _command(bot, "!ошибка лукойл вилга лимит")
    assert answer_question(conn, station_id=STATION, grades=[], question_text="лимит?") is None


def test_retracting_a_named_grade_leaves_the_other_one(env):
    conn, bot = env
    _report(conn, "92", "available", 5)
    _report(conn, "95", "available", 5)

    reply = _command(bot, "!ошибка лукойл вилга 95")

    assert "убрал 95" in reply
    text = answer_question(conn, station_id=STATION, grades=[], question_text="что есть?")
    assert "92 - Есть" in text
    assert "95" not in text


def test_without_an_aspect_everything_about_the_station_goes(env):
    conn, bot = env
    _report(conn, "92", "available", 5)
    _break(conn, "слив", 5)
    _limit(conn, "limited", 8, 5)

    reply = _command(bot, "!ошибка лукойл вилга")

    assert "убрал" in reply
    assert answer_question(
        conn, station_id=STATION, grades=[], question_text="что есть? перерыв? лимит?"
    ) is None


def test_a_break_can_be_retracted_by_the_word_бензовоз(env):
    conn, bot = env
    _report(conn, "92", "available", 5)
    _break(conn, "слив", 5)

    reply = _command(bot, "!ошибка лукойл вилга бензовоз")

    assert "убрал перерыв" in reply
    text = answer_question(conn, station_id=STATION, grades=[], question_text="что есть? перерыв?")
    assert "92 - Есть" in text
    assert "слив" not in text


def test_retraction_touches_only_the_named_station(env):
    conn, bot = env
    _report(conn, "92", "available", 5)
    _report(conn, "92", "available", 5, station_id="gazprom")

    _command(bot, "!ошибка лукойл вилга")

    assert answer_question(conn, station_id=STATION, grades=[], question_text="что есть?") is None
    assert "92 - Есть" in answer_question(conn, station_id="gazprom", grades=[], question_text="что есть?")


def test_unresolved_station_is_reported_with_an_example(env):
    _conn, bot = env

    reply = _command(bot, "!ошибка где-то там")

    assert "Не понял, про какую заправку" in reply
    assert "!ошибка лукойл вилга лимит" in reply


def test_nothing_to_retract_is_said_plainly(env):
    _conn, bot = env

    reply = _command(bot, "!ошибка лукойл вилга")

    assert "убирать нечего" in reply


def test_bare_command_without_a_station_does_not_crash(env):
    _conn, bot = env

    reply = _command(bot, "!ошибка")

    assert "Не понял, про какую заправку" in reply


def test_the_command_is_listed_in_the_help(env):
    _conn, bot = env

    reply = _command(bot, "!помощь")

    assert "!ошибка" in reply
