from vk_handlers import _quoted_context_text


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
