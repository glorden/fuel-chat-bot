import logging

from google import genai
from google.genai import types

from config import GEMINI_API_KEY, GEMINI_MODEL, LLM_TIMEOUT_SECONDS
from llm.prompts import build_system_prompt, build_user_content
from llm.schema import PARAMETERS_SCHEMA, TOOL_DESCRIPTION, TOOL_NAME

log = logging.getLogger("vk_bot")


def _to_gemini_schema(schema):
    """JSON Schema (наш вариант, nullable как "type": [x, "null"]) -> формат
    types.Schema: Gemini не понимает union-типы, вместо этого — один
    конкретный type + отдельный флаг nullable. Не мутирует исходный dict
    (PARAMETERS_SCHEMA общий с groq_client.py)."""
    if not isinstance(schema, dict):
        return schema
    result = dict(schema)
    t = result.get("type")
    if isinstance(t, list):
        non_null = [x for x in t if x != "null"]
        if len(non_null) != 1:
            raise ValueError(f"unsupported union type for Gemini schema: {t!r}")
        result["type"] = non_null[0]
        result["nullable"] = True
    if "properties" in result:
        result["properties"] = {k: _to_gemini_schema(v) for k, v in result["properties"].items()}
    if "items" in result:
        result["items"] = _to_gemini_schema(result["items"])
    return result


_SYSTEM_PROMPT = build_system_prompt()
_TOOLS = [
    types.Tool(function_declarations=[
        types.FunctionDeclaration(
            name=TOOL_NAME,
            description=TOOL_DESCRIPTION,
            parameters=_to_gemini_schema(PARAMETERS_SCHEMA),
        )
    ])
]
_GENERATE_CONFIG = types.GenerateContentConfig(
    system_instruction=_SYSTEM_PROMPT,
    tools=_TOOLS,
    tool_config=types.ToolConfig(
        function_calling_config=types.FunctionCallingConfig(
            mode="ANY",
            allowed_function_names=[TOOL_NAME],
        )
    ),
)
_client = (
    genai.Client(api_key=GEMINI_API_KEY, http_options=types.HttpOptions(timeout=LLM_TIMEOUT_SECONDS * 1000))
    if GEMINI_API_KEY else None
)


def raw_analyze(text: str, *, previous_message: str | None = None) -> dict | None:
    """Разбор сообщения через Gemini. Возвращает сырой dict аргументов
    вызванной функции, либо None при любом сбое/неожиданном ответе —
    сигнал диспетчеру (llm/client.py) откатиться на rule-based."""
    if _client is None:
        return None
    try:
        response = _client.models.generate_content(
            model=GEMINI_MODEL,
            contents=build_user_content(text, previous_message=previous_message),
            config=_GENERATE_CONFIG,
        )
        calls = response.function_calls
        if not calls:
            log.warning("Gemini: ответ без function call, откат на rule-based. text=%r", text)
            return None
        raw = calls[0].args
    except Exception:
        log.exception("Gemini: сбой запроса, откат на rule-based. text=%r", text)
        return None

    if not isinstance(raw, dict):
        log.warning("Gemini: args не dict (%r), откат на rule-based.", type(raw))
        return None
    return raw
