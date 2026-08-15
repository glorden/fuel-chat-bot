import time

import vk_handlers
from vk_handlers import _parse_admin_command, _quoted_context_text, _recent_author_message


class _FakeForeign:
    def __init__(self, text):
        self.text = text


class _FakeMessage:
    def __init__(self, reply_message=None, fwd_messages=None):
        self.reply_message = reply_message
        self.fwd_messages = fwd_messages or []


def test_quoted_context_text_empty_when_no_reply_no_fwd():
    assert _quoted_context_text(_FakeMessage()) == ""


def test_quoted_context_text_includes_reply_message():
    msg = _FakeMessage(reply_message=_FakeForeign("95 нет на Лукойле"))
    assert _quoted_context_text(msg) == "95 нет на Лукойле"


def test_quoted_context_text_includes_multiple_fwd_messages_joined():
    msg = _FakeMessage(fwd_messages=[_FakeForeign("первое сообщение"), _FakeForeign("второе сообщение")])
    assert _quoted_context_text(msg) == "первое сообщение\nвторое сообщение"


def test_quoted_context_text_combines_fwd_and_reply():
    msg = _FakeMessage(
        fwd_messages=[_FakeForeign("форвард")],
        reply_message=_FakeForeign("реплай"),
    )
    assert _quoted_context_text(msg) == "форвард\nреплай"


def test_quoted_context_text_skips_empty_texts():
    msg = _FakeMessage(
        fwd_messages=[_FakeForeign(""), _FakeForeign("   ")],
        reply_message=_FakeForeign(None),
    )
    assert _quoted_context_text(msg) == ""


def test_recent_author_message_returns_none_when_no_entry():
    assert _recent_author_message((999999, 888888)) is None


def test_recent_author_message_returns_fresh_entry():
    key = (999999, 888889)
    vk_handlers._last_author_message[key] = ("95 есть на Газпроме?", time.monotonic())
    try:
        assert _recent_author_message(key) == "95 есть на Газпроме?"
    finally:
        del vk_handlers._last_author_message[key]


def test_recent_author_message_expires_after_ttl():
    key = (999999, 888890)
    expired_at = time.monotonic() - vk_handlers._AUTHOR_CONTEXT_TTL_SECONDS - 1
    vk_handlers._last_author_message[key] = ("95 есть на Газпроме?", expired_at)
    try:
        assert _recent_author_message(key) is None
    finally:
        del vk_handlers._last_author_message[key]


def test_parse_admin_command_recognizes_on_and_off():
    assert _parse_admin_command("!вкл") is True
    assert _parse_admin_command("!выкл") is False


def test_parse_admin_command_case_insensitive_and_trims_whitespace():
    assert _parse_admin_command("  !ВКЛ  ") is True
    assert _parse_admin_command("!Выкл") is False


def test_parse_admin_command_none_for_regular_text():
    assert _parse_admin_command("Есть 95 на Роснефти?") is None
    assert _parse_admin_command("!вкл, пожалуйста") is None  # не голая команда
    assert _parse_admin_command("") is None
