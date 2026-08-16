import json
import logging

import httpx
from mistralai.client import Mistral

from config import AI_PROXY_URL, LLM_TIMEOUT_SECONDS, MISTRAL_API_KEY, MISTRAL_MODEL
from llm.prompts import build_system_prompt, build_user_content
from llm.schema import PARAMETERS_SCHEMA, TOOL_DESCRIPTION, TOOL_NAME

log = logging.getLogger("vk_bot")

_TOOL_SCHEMA = {
    "type": "function",
    "function": {
        "name": TOOL_NAME,
        "description": TOOL_DESCRIPTION,
        "parameters": PARAMETERS_SCHEMA,
    },
}

_SYSTEM_PROMPT = build_system_prompt()
# Тот же приём, что и в llm/groq_client.py — SDK у Mistral тоже сгенерирован
# Speakeasy, конструктор принимает произвольный httpx-совместимый client=
# (см. DEPLOY.md). В отличие от Groq, прямой вызов Mistral с library-vps не
# упирался в геоблок (403) — прокси тут скорее подстраховка на будущее, чем
# обязательное условие.
_client = (
    Mistral(
        api_key=MISTRAL_API_KEY,
        client=httpx.Client(proxy=AI_PROXY_URL, timeout=LLM_TIMEOUT_SECONDS) if AI_PROXY_URL else None,
        timeout_ms=LLM_TIMEOUT_SECONDS * 1000,
    )
    if MISTRAL_API_KEY else None
)


def raw_analyze(
    text: str, *, previous_message: str | None = None, quoted_context: str | None = None
) -> dict | None:
    """Разбор сообщения через Mistral. Возвращает сырой dict аргументов
    вызванной функции, либо None при любом сбое/неожиданном ответе —
    сигнал диспетчеру (llm/client.py) откатиться на rule-based. Схема та
    же, что и у Groq (PARAMETERS_SCHEMA без конвертации — API Mistral для
    tool calling совместимо по форме с OpenAI/Groq, в отличие от Gemini)."""
    if _client is None:
        return None

    try:
        response = _client.chat.complete(
            model=MISTRAL_MODEL,
            messages=[
                {"role": "system", "content": _SYSTEM_PROMPT},
                {
                    "role": "user",
                    "content": build_user_content(
                        text, previous_message=previous_message, quoted_context=quoted_context
                    ),
                },
            ],
            tools=[_TOOL_SCHEMA],
            tool_choice={"type": "function", "function": {"name": TOOL_NAME}},
        )
        tool_calls = response.choices[0].message.tool_calls
        if not tool_calls:
            log.warning("Mistral: ответ без tool call, откат на rule-based. len=%s", len(text))
            return None

        arguments = tool_calls[0].function.arguments
        # В отличие от Groq (всегда строка), Mistral SDK типизирует
        # arguments как Union[dict, str] (mistralai/client/models/functioncall.py)
        # — какой вариант реально приходит от модели, живой проверкой пока
        # не подтверждено, обрабатываем оба на основе типов SDK.
        if isinstance(arguments, str):
            return json.loads(arguments)
        return arguments
    except Exception:
        log.exception("Mistral: сбой запроса, откат на rule-based. len=%s", len(text))
        return None
