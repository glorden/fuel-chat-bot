import logging

from config import LLM_FALLBACK_PROVIDER, LLM_PROVIDER
from llm.schema import LLMAnalysis, _parse_arguments  # noqa: F401 — реэкспорт для тестов/scripts

log = logging.getLogger("vk_bot")


def _raw_analyze_for(provider: str):
    if provider == "gemini":
        from llm.gemini_client import raw_analyze
    elif provider == "mistral":
        from llm.mistral_client import raw_analyze
    else:
        from llm.groq_client import raw_analyze
    return raw_analyze


def _provider_chain() -> list[str]:
    """Основной провайдер, за ним — резервный, если он задан и отличается."""
    if LLM_FALLBACK_PROVIDER is None or LLM_FALLBACK_PROVIDER == LLM_PROVIDER:
        return [LLM_PROVIDER]
    return [LLM_PROVIDER, LLM_FALLBACK_PROVIDER]


def _analyze_with(provider: str, text: str, *, previous_message, quoted_context) -> LLMAnalysis | None:
    raw = _raw_analyze_for(provider)(
        text, previous_message=previous_message, quoted_context=quoted_context
    )
    if raw is None:
        return None

    try:
        parsed = _parse_arguments(raw)
    except Exception:
        log.exception("%s: неожиданная форма ответа. raw=%r", provider, raw)
        return None

    if parsed is None:
        log.warning("%s: неожиданная форма ответа. raw=%r", provider, raw)
    return parsed


def analyze(
    text: str, *, previous_message: str | None = None, quoted_context: str | None = None
) -> LLMAnalysis | None:
    """Единственная точка входа для pipeline.py. Диспетчер по
    config.LLM_PROVIDER, при неудаче — вторая попытка через
    LLM_FALLBACK_PROVIDER, если он задан. None означает, что не ответил ни
    один провайдер: пайплайн после этого отвечает на вопросы по rule-based,
    но факты с него уже не пишет (решение Р1).

    `previous_message`/`quoted_context` — контекст для разрешения неявных
    ссылок (см. llm/prompts.py::build_user_content, каждый со своей меткой);
    rule-based путь их не получает."""
    chain = _provider_chain()
    for position, provider in enumerate(chain):
        parsed = _analyze_with(
            provider, text, previous_message=previous_message, quoted_context=quoted_context
        )
        if parsed is not None:
            if position > 0:
                log.warning("Основной провайдер не ответил, разобрано резервным: %s", provider)
            return parsed
        log.warning("%s не ответил (%s из %s в цепочке)", provider, position + 1, len(chain))

    log.info("path=rule_based reason=llm_fallback providers=%s", ",".join(chain))
    return None
