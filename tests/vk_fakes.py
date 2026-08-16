"""Подставной VK для тестов обработчика.

Настоящий vkbottle-овский `Message` для этого не годится — это pydantic-модель,
которой нельзя подменить `reply`. А `handle_message` вынесен из замыкания
именно затем, чтобы его можно было звать напрямую с такой заглушкой.
"""

from datetime import datetime, timezone
from types import SimpleNamespace


class _FakeUsers:
    def __init__(self, api):
        self._api = api

    async def get(self, user_ids):
        if self._api.users_get_error is not None:
            raise self._api.users_get_error
        return [SimpleNamespace(first_name="Мария")]


class _FakeMessages:
    def __init__(self, api):
        self._api = api

    async def send(self, **kwargs):
        if self._api.send_error is not None:
            raise self._api.send_error
        self._api.sent.append(kwargs)
        return 1


class FakeApi:
    def __init__(self):
        self.users_get_error = None
        self.send_error = None
        self.sent = []
        self.users = _FakeUsers(self)
        self.messages = _FakeMessages(self)


class FakeBot:
    def __init__(self):
        self.api = FakeApi()


class FakeVkMessage:
    def __init__(
        self,
        *,
        peer_id=2000000001,
        text="",
        from_id=111,
        cmid=1,
        is_mentioned=True,
        date=None,
    ):
        self.peer_id = peer_id
        self.text = text
        self.from_id = from_id
        self.conversation_message_id = cmid
        self.is_mentioned = is_mentioned
        # vkbottle отдаёт date уже как tz-aware datetime (проверено), не как
        # unix-таймстамп — заглушка повторяет именно это.
        self.date = date if date is not None else datetime.now(timezone.utc)
        self.fwd_messages = []
        self.reply_message = None
        self.replies = []
        self.reply_failures = 0

    async def reply(self, text):
        if self.reply_failures > 0:
            self.reply_failures -= 1
            raise RuntimeError("VK недоступен")
        self.replies.append(text)
