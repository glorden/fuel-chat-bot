import logging

from config import LLM_PROVIDER
from llm.schema import LLMAnalysis, _parse_arguments  # noqa: F401 — реэкспорт для тестов/scripts

log = logging.getLogger("vk_bot")


def analyze(text: str, *, previous_message: str | None = None) -> LLMAnalysis | None:
    """Единственная точка входа для pipeline.py. Диспетчер по
    config.LLM_PROVIDER; любой сбой/невалидный ответ любого провайдера —
    None, сигнал пайплайну откатиться на rule-based. `previous_message` —
    предыдущее сообщение того же автора, только контекст для разрешения
    неявных ссылок (см. llm/prompts.py); rule-based путь его не получает."""
    if LLM_PROVIDER == "gemini":
        from llm.gemini_client import raw_analyze
    else:
        from llm.groq_client import raw_analyze

    raw = raw_analyze(text, previous_message=previous_message)
    if raw is None:
        return None

    try:
        parsed = _parse_arguments(raw)
    except Exception:
        log.exception("%s: неожиданная форма ответа, откат на rule-based. raw=%r", LLM_PROVIDER, raw)
        return None

    if parsed is None:
        log.warning("%s: неожиданная форма ответа, откат на rule-based. raw=%r", LLM_PROVIDER, raw)
    return parsed
