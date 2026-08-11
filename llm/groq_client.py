import json
import logging

from groq import Groq

from config import GROQ_API_KEY, LLM_MODEL, LLM_TIMEOUT_SECONDS
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
_client = Groq(api_key=GROQ_API_KEY) if GROQ_API_KEY else None


def raw_analyze(
    text: str, *, previous_message: str | None = None, quoted_context: str | None = None
) -> dict | None:
    """Разбор сообщения через Groq. Возвращает сырой dict аргументов
    вызванной функции, либо None при любом сбое/неожиданном ответе —
    сигнал диспетчеру (llm/client.py) откатиться на rule-based."""
    if _client is None:
        return None

    try:
        response = _client.chat.completions.create(
            model=LLM_MODEL,
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
            timeout=LLM_TIMEOUT_SECONDS,
        )
        tool_calls = response.choices[0].message.tool_calls
        if not tool_calls:
            log.warning("Groq: ответ без tool call, откат на rule-based. text=%r", text)
            return None

        return json.loads(tool_calls[0].function.arguments)
    except Exception:
        log.exception("Groq: сбой запроса, откат на rule-based. text=%r", text)
        return None
